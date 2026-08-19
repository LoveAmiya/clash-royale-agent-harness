"""Deterministic, local-only snapshot audit export and review validation.

This module never imports the model gateway, retriever, embedding client, or
Supercell HTTP client. It works only with an already-published snapshot archive.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import app_config as _app_config  # noqa: F401 - bootstrap src package imports

from snapshot_store import compute_rag_docs_fingerprint
from src.clashroyale_agent.snapshots.review_validation import (
    read_jsonl_documents as read_jsonl_documents_orchestrated,
    normalized_numbers as normalized_numbers_orchestrated,
    verify_audit_files as verify_audit_files_orchestrated,
    review_validation_report as review_validation_report_orchestrated,
)
try:
    from clashroyale_agent.snapshots.audit_primitives import (
        publish_directory as _publish_directory_orchestrated,
        read_json as _read_json_orchestrated,
        sha256 as _sha256_orchestrated,
        validated_snapshot_id as _validated_snapshot_id_orchestrated,
        write_json as _write_json_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.audit_primitives import (
        publish_directory as _publish_directory_orchestrated,
        read_json as _read_json_orchestrated,
        sha256 as _sha256_orchestrated,
        validated_snapshot_id as _validated_snapshot_id_orchestrated,
        write_json as _write_json_orchestrated,
    )


AUDIT_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
DEFAULT_PARTITION_SIZE = 50_000
MAX_REVIEW_DOCUMENT_BYTES = 4 * 1024 * 1024
_SAFE_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_])")


class SnapshotAuditError(ValueError):
    """Raised when a published package cannot produce a trustworthy export."""


class ExternalReviewValidationError(ValueError):
    """Raised when reviewed documents fail the local import boundary."""

    def __init__(self, report: dict):
        super().__init__("external review rejected: " + ", ".join(report.get("failures", [])))
        self.report = report


def _validated_snapshot_id(snapshot_id: str) -> str:
    return _validated_snapshot_id_orchestrated(snapshot_id, SnapshotAuditError)


def _read_json(path: Path) -> dict | list:
    return _read_json_orchestrated(path, SnapshotAuditError)


def _write_json(path: Path, value: object) -> None:
    _write_json_orchestrated(path, value)


def _sha256(path: Path) -> str:
    return _sha256_orchestrated(path)


def _publish_directory(source: Path, destination: Path) -> None:
    _publish_directory_orchestrated(source, destination)


def _file_entry(root: Path, path: Path, *, role: str, records: int | None = None) -> dict:
    entry = {
        "path": path.relative_to(root).as_posix(),
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if records is not None:
        entry["records"] = int(records)
    return entry


def _write_line(handle, value: object) -> None:
    handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    handle.write("\n")


def _side_records(battle: dict) -> tuple[dict, dict]:
    team_crowns = int(battle.get("team_crowns") or 0)
    opponent_crowns = int(battle.get("opponent_crowns") or 0)
    if team_crowns > opponent_crowns:
        team_result, opponent_result = "win", "loss"
    elif team_crowns < opponent_crowns:
        team_result, opponent_result = "loss", "win"
    else:
        team_result = opponent_result = "draw"
    battle_id = str(battle.get("battle_id") or "")
    common = {
        "battle_id": battle_id or None,
        "battle_time": battle.get("battle_time"),
    }
    team = {
        **common,
        "side_record_id": f"{battle_id}:team",
        "source_side": "team",
        "player_tag": battle.get("team_tag"),
        "opponent_tag": battle.get("opponent_tag"),
        "deck": list(battle.get("team_deck") or []),
        "opponent_deck": list(battle.get("opponent_deck") or []),
        "crowns": team_crowns,
        "opponent_crowns": opponent_crowns,
        "result": team_result,
        "won": team_result == "win",
    }
    opponent = {
        **common,
        "side_record_id": f"{battle_id}:opponent",
        "source_side": "opponent",
        "player_tag": battle.get("opponent_tag"),
        "opponent_tag": battle.get("team_tag"),
        "deck": list(battle.get("opponent_deck") or []),
        "opponent_deck": list(battle.get("team_deck") or []),
        "crowns": opponent_crowns,
        "opponent_crowns": team_crowns,
        "result": opponent_result,
        "won": opponent_result == "win",
    }
    return team, opponent


def _export_raw_probe_partitions(
    connection: sqlite3.Connection,
    export_dir: Path,
    partition_size: int,
) -> tuple[list[tuple[Path, int]], int]:
    files: list[tuple[Path, int]] = []
    handle = None
    count = 0
    part_count = 0
    try:
        for (payload,) in connection.execute("SELECT payload FROM probe_battles ORDER BY sequence"):
            if count % partition_size == 0:
                if handle is not None:
                    handle.close()
                    files.append((path, part_count))
                path = export_dir / f"raw_battlelogs.part-{len(files) + 1:05d}.jsonl"
                handle = path.open("w", encoding="utf-8", newline="\n")
                part_count = 0
            handle.write(str(payload).strip())
            handle.write("\n")
            count += 1
            part_count += 1
    finally:
        if handle is not None:
            handle.close()
            files.append((path, part_count))
    return files, count


def _export_battle_records(
    connection: sqlite3.Connection,
    export_dir: Path,
    partition_size: int,
) -> tuple[list[tuple[Path, int]], int, int, int]:
    combined_path = export_dir / "normalized_battles.jsonl"
    side_path = export_dir / "side_records.jsonl"
    partitions: list[tuple[Path, int]] = []
    partition_handle = None
    battle_count = 0
    partition_count = 0
    invalid_deck_shapes = 0
    try:
        with combined_path.open("w", encoding="utf-8", newline="\n") as combined, side_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as sides:
            for (payload,) in connection.execute("SELECT payload FROM battles ORDER BY sequence"):
                if battle_count % partition_size == 0:
                    if partition_handle is not None:
                        partition_handle.close()
                        partitions.append((partition_path, partition_count))
                    partition_path = export_dir / f"normalized_battles.part-{len(partitions) + 1:05d}.jsonl"
                    partition_handle = partition_path.open("w", encoding="utf-8", newline="\n")
                    partition_count = 0
                battle = json.loads(payload)
                team_deck = battle.get("team_deck")
                opponent_deck = battle.get("opponent_deck")
                if not (
                    isinstance(team_deck, list)
                    and len(team_deck) == 8
                    and isinstance(opponent_deck, list)
                    and len(opponent_deck) == 8
                ):
                    invalid_deck_shapes += 1
                _write_line(combined, battle)
                _write_line(partition_handle, battle)
                for side in _side_records(battle):
                    _write_line(sides, side)
                battle_count += 1
                partition_count += 1
    finally:
        if partition_handle is not None:
            partition_handle.close()
            partitions.append((partition_path, partition_count))
    return partitions, battle_count, battle_count * 2, invalid_deck_shapes


def _export_cards(connection: sqlite3.Connection, path: Path, total_battles: int) -> int:
    rows = connection.execute(
        "SELECT card_name, appearances, wins FROM card_stats ORDER BY appearances DESC, card_name"
    )
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("card_name", "appearances", "wins", "losses", "usage_rate", "win_rate"),
        )
        writer.writeheader()
        for card_name, appearances, wins in rows:
            writer.writerow(
                {
                    "card_name": card_name,
                    "appearances": appearances,
                    "wins": wins,
                    "losses": appearances - wins,
                    "usage_rate": round(appearances / total_battles * 100, 6) if total_battles else 0,
                    "win_rate": round(wins / appearances * 100, 6) if appearances else 0,
                }
            )
            count += 1
    return count


def _export_decks(connection: sqlite3.Connection, path: Path) -> int:
    rows = connection.execute(
        "SELECT deck_json, battles, wins, elixir_total, elixir_samples "
        "FROM deck_stats ORDER BY battles DESC, deck_json"
    )
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("deck_signature", "battles", "wins", "losses", "win_rate", "average_elixir"),
        )
        writer.writeheader()
        for deck_json, battles, wins, elixir_total, elixir_samples in rows:
            writer.writerow(
                {
                    "deck_signature": deck_json,
                    "battles": battles,
                    "wins": wins,
                    "losses": battles - wins,
                    "win_rate": round(wins / battles * 100, 6) if battles else 0,
                    "average_elixir": round(elixir_total / elixir_samples, 6) if elixir_samples else "",
                }
            )
            count += 1
    return count


def _export_matchups(connection: sqlite3.Connection, path: Path) -> int:
    rows = connection.execute(
        "SELECT deck_key, opponent_json, games, wins FROM matchup_stats "
        "ORDER BY games DESC, deck_key, opponent_json"
    )
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("deck_signature", "opponent_signature", "games", "wins", "losses", "win_rate"),
        )
        writer.writeheader()
        for deck_key, opponent_json, games, wins in rows:
            writer.writerow(
                {
                    "deck_signature": deck_key,
                    "opponent_signature": opponent_json,
                    "games": games,
                    "wins": wins,
                    "losses": games - wins,
                    "win_rate": round(wins / games * 100, 6) if games else 0,
                }
            )
            count += 1
    return count


def _export_rag_documents(documents: list[dict], export_dir: Path) -> tuple[int, int]:
    rag_path = export_dir / "rag_documents.generated.jsonl"
    archetype_path = export_dir / "archetypes.csv"
    with rag_path.open("w", encoding="utf-8", newline="\n") as handle:
        for document in documents:
            _write_line(handle, document)
    archetypes = [document for document in documents if document.get("source_type") == "archetype"]
    with archetype_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("doc_id", "archetype", "games", "sample_win_rate", "metadata_json"),
        )
        writer.writeheader()
        for document in archetypes:
            metadata = document.get("metadata") or {}
            writer.writerow(
                {
                    "doc_id": document.get("doc_id"),
                    "archetype": metadata.get("archetype"),
                    "games": metadata.get("games"),
                    "sample_win_rate": metadata.get("sample_win_rate"),
                    "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                }
            )
    return len(documents), len(archetypes)


def _readme_text(snapshot_id: str) -> str:
    return f"""# Clash Royale Snapshot Audit Package

Snapshot ID: `{snapshot_id}`

This package is for manual review in ChatGPT web. It does not use the project's API key.

## Source boundary

- `normalized_battles.jsonl` and its partitions contain the complete collector-normalized corpus.
- `raw_battlelogs.part-*.jsonl` contains only the bounded raw API probe subset retained during collection.
- The project did not retain all original per-player API responses. Do not describe normalized records as complete raw API payloads.
- `side_records.jsonl` expands every normalized battle into both perspectives for later unbiased statistics.
- CSV files come from the archived exact SQLite aggregates.

## Review output contract

Return one JSON object per line in `rag_documents.reviewed.jsonl`. Preserve every `doc_id`,
`source_type`, and `metadata` value exactly. You may improve only `text`. Do not add new numeric
claims. The local importer verifies all audit hashes, document coverage, immutable source fields,
and numeric agreement before staging the review. It never replaces active RAG documents.
"""


def _existing_export_if_valid(destination: Path) -> dict | None:
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        return None
    for entry in manifest.get("files", []):
        path = destination / str(entry.get("path") or "")
        if not path.is_file() or _sha256(path) != entry.get("sha256"):
            return None
    return manifest


def export_snapshot_audit(
    data_dir: Path,
    snapshot_id: str,
    *,
    partition_size: int = DEFAULT_PARTITION_SIZE,
) -> dict:
    """Export one published snapshot package using bounded-memory iteration."""
    data_dir = Path(data_dir)
    snapshot_id = _validated_snapshot_id(snapshot_id)
    if not isinstance(partition_size, int) or partition_size <= 0:
        raise SnapshotAuditError("partition_size must be a positive integer")
    archive = data_dir / "snapshot_archives" / snapshot_id
    archive_manifest = _read_json(archive / "manifest.json")
    if not isinstance(archive_manifest, dict) or archive_manifest.get("snapshot_id") != snapshot_id:
        raise SnapshotAuditError("snapshot archive identity mismatch")
    if archive_manifest.get("complete") is not True:
        raise SnapshotAuditError("snapshot archive is incomplete")
    database_path = archive / "aggregates.sqlite"
    rag_path = archive / "rag_documents.json"
    summary_path = archive / "collector_snapshot.json"
    if not database_path.is_file() or not rag_path.is_file() or not summary_path.is_file():
        raise SnapshotAuditError("snapshot archive package is incomplete")

    destination = data_dir / "audit_exports" / snapshot_id
    if destination.exists():
        existing = _existing_export_if_valid(destination)
        if existing is not None:
            return existing
        raise SnapshotAuditError("existing audit export is incomplete or has hash mismatches")

    export_root = data_dir / "audit_exports"
    export_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=export_root))
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        summary = _read_json(summary_path)
        documents = _read_json(rag_path)
        if not isinstance(summary, dict) or summary.get("snapshot_id") != snapshot_id:
            raise SnapshotAuditError("collector summary identity mismatch")
        if not isinstance(documents, list) or not documents:
            raise SnapshotAuditError("archived RAG documents are missing")

        raw_parts, raw_probe_count = _export_raw_probe_partitions(connection, temp_dir, partition_size)
        normalized_parts, battle_count, side_count, invalid_decks = _export_battle_records(
            connection, temp_dir, partition_size
        )
        declared_battles = int(summary.get("sample_battles") or 0)
        if battle_count != declared_battles:
            raise SnapshotAuditError(
                f"normalized battle count mismatch: declared={declared_battles} actual={battle_count}"
            )
        cards_count = _export_cards(connection, temp_dir / "cards.csv", battle_count)
        decks_count = _export_decks(connection, temp_dir / "decks.csv")
        matchups_count = _export_matchups(connection, temp_dir / "matchups.csv")
        rag_count, archetypes_count = _export_rag_documents(documents, temp_dir)
        metadata = {key: value for key, value in summary.items() if key != "raw_battles"}
        _write_json(temp_dir / "snapshot_metadata.json", metadata)
        (temp_dir / "README_FOR_CHATGPT.md").write_text(
            _readme_text(snapshot_id), encoding="utf-8", newline="\n"
        )

        described_files: list[tuple[Path, str, int | None]] = [
            (temp_dir / "snapshot_metadata.json", "snapshot_metadata", 1),
            (temp_dir / "normalized_battles.jsonl", "complete_normalized_battle_corpus", battle_count),
            (temp_dir / "side_records.jsonl", "two_sided_normalized_records", side_count),
            (temp_dir / "cards.csv", "collector_card_aggregates", cards_count),
            (temp_dir / "decks.csv", "exact_deck_aggregates", decks_count),
            (temp_dir / "matchups.csv", "exact_matchup_aggregates", matchups_count),
            (temp_dir / "archetypes.csv", "generated_archetype_evidence", archetypes_count),
            (temp_dir / "rag_documents.generated.jsonl", "generated_rag_documents", rag_count),
            (temp_dir / "README_FOR_CHATGPT.md", "manual_review_instructions", None),
        ]
        described_files.extend((path, "raw_api_probe_subset", count) for path, count in raw_parts)
        described_files.extend((path, "normalized_battle_partition", count) for path, count in normalized_parts)
        files = [
            _file_entry(temp_dir, path, role=role, records=records)
            for path, role, records in described_files
        ]
        manifest = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "generator": "snapshot_audit.py",
            "cost_boundaries": {
                "supercell_requests": 0,
                "cloud_llm_calls": 0,
                "cloud_embedding_calls": 0,
                "local_embedding_calls": 0,
            },
            "source_boundaries": {
                "complete_normalized_corpus": True,
                "raw_api_payload_coverage": "probe_subset_only",
                "raw_api_probe_records": raw_probe_count,
                "full_raw_api_payloads_retained": False,
            },
            "source_archive": {
                "manifest_sha256": _sha256(archive / "manifest.json"),
                "aggregates_sqlite_sha256": _sha256(database_path),
                "rag_documents_json_sha256": _sha256(rag_path),
                "collector_snapshot_json_sha256": _sha256(summary_path),
                "rag_docs_fingerprint": compute_rag_docs_fingerprint(documents),
            },
            "counts": {
                "normalized_battles": battle_count,
                "side_records": side_count,
                "invalid_eight_card_shapes": invalid_decks,
                "cards": cards_count,
                "decks": decks_count,
                "matchups": matchups_count,
                "archetypes": archetypes_count,
                "rag_documents": rag_count,
            },
            "files": sorted(files, key=lambda entry: entry["path"]),
        }
        _write_json(temp_dir / "manifest.json", manifest)
        _publish_directory(temp_dir, destination)
        return manifest
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    finally:
        connection.close()


def _read_jsonl_documents(path: Path) -> list[dict]:
    return read_jsonl_documents_orchestrated(path, max_bytes=MAX_REVIEW_DOCUMENT_BYTES, error_type=SnapshotAuditError)


def _normalized_numbers(value: object) -> Counter[str]:
    return normalized_numbers_orchestrated(value)


def _verify_audit_files(audit_dir: Path, manifest: dict) -> list[str]:
    return verify_audit_files_orchestrated(audit_dir, manifest, sha256=_sha256)


def _review_validation_report(
    *,
    snapshot_id: str,
    expected: list[dict],
    reviewed: list[dict],
    audit_hash_mismatches: list[str],
    reviewed_path: Path,
) -> dict:
    return review_validation_report_orchestrated(
        snapshot_id=snapshot_id, expected=expected, reviewed=reviewed,
        audit_hash_mismatches=audit_hash_mismatches, reviewed_path=reviewed_path,
        schema_version=REVIEW_SCHEMA_VERSION, source="Supercell API live sample",
        fingerprint=compute_rag_docs_fingerprint, sha256=_sha256,
    )


def import_reviewed_rag_documents(
    data_dir: Path,
    snapshot_id: str,
    reviewed_documents_path: Path,
    *,
    review_notes_path: Path | None = None,
) -> dict:
    """Validate and stage reviewed text without changing active RAG evidence."""
    data_dir = Path(data_dir)
    snapshot_id = _validated_snapshot_id(snapshot_id)
    reviewed_documents_path = Path(reviewed_documents_path)
    audit_dir = data_dir / "audit_exports" / snapshot_id
    manifest = _read_json(audit_dir / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("snapshot_id") != snapshot_id:
        raise SnapshotAuditError("audit manifest identity mismatch")
    expected_path = audit_dir / "rag_documents.generated.jsonl"
    expected = _read_jsonl_documents(expected_path)
    reviewed = _read_jsonl_documents(reviewed_documents_path)
    hash_mismatches = _verify_audit_files(audit_dir, manifest)
    report = _review_validation_report(
        snapshot_id=snapshot_id,
        expected=expected,
        reviewed=reviewed,
        audit_hash_mismatches=hash_mismatches,
        reviewed_path=reviewed_documents_path,
    )
    review_dir = data_dir / "external_reviews" / snapshot_id
    review_dir.mkdir(parents=True, exist_ok=True)
    if not report["passed"]:
        _write_json(review_dir / "validation_report.rejected.json", report)
        raise ExternalReviewValidationError(report)

    destination = review_dir / "rag_documents.reviewed.jsonl"
    descriptor, temp_name = tempfile.mkstemp(prefix=".rag_documents.reviewed.", suffix=".tmp", dir=review_dir)
    os.close(descriptor)
    try:
        shutil.copyfile(reviewed_documents_path, temp_name)
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    if review_notes_path is not None:
        notes = Path(review_notes_path)
        if notes.is_file():
            shutil.copyfile(notes, review_dir / "review_notes.md")
    _write_json(review_dir / "validation_report.json", report)
    return report
