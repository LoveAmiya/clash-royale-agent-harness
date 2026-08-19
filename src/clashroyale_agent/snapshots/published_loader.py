"""Loading and compatibility migration for the canonical published snapshot."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable


def load_published_snapshot(
    data_dir: Path,
    *,
    snapshot_file_name: str,
    is_complete: Callable[[object], bool],
    build_stats: Callable[..., dict],
    build_documents: Callable[[dict], list[dict]],
    validate_documents: Callable[[dict, list[dict]], dict],
    fingerprint: Callable[[list[dict]], str],
    atomic_write: Callable[[Path, object], None],
) -> dict | None:
    """Load the canonical snapshot and repair derived compatibility files."""
    path = data_dir / snapshot_file_name
    if not path.exists():
        return None
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not is_complete(snapshot):
        return None

    migrated = False
    if not isinstance(snapshot.get("card_deck_stats"), dict):
        snapshot["card_deck_stats"] = build_stats(
            list(snapshot.get("raw_battles", [])),
            fetched_at=str(snapshot.get("fetched_at") or ""),
            sample_battles=int(snapshot.get("sample_battles") or 0),
            target_battles=int(snapshot.get("target_battles") or 0),
        )
        migrated = True

    documents = build_documents(snapshot)
    validation = validate_documents(snapshot, documents)
    if not validation["passed"]:
        return None
    expected_fingerprint = validation["docs_fingerprint"]
    documents_path = data_dir / "rag_documents.json"
    try:
        current_documents = json.loads(documents_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current_documents = None
    current_fingerprint = fingerprint(current_documents) if isinstance(current_documents, list) else None
    try:
        derived_files_use_crlf = any(
            b"\r\n" in candidate.read_bytes()
            for candidate in (
                data_dir / "cards_meta.json",
                data_dir / "top_decks.json",
                documents_path,
            )
            if candidate.exists()
        )
    except OSError:
        derived_files_use_crlf = True
    if (
        snapshot.get("rag_docs_fingerprint") != expected_fingerprint
        or current_fingerprint != expected_fingerprint
        or derived_files_use_crlf
    ):
        snapshot["rag_document_counts"] = dict(Counter(document["source_type"] for document in documents))
        snapshot["rag_docs_fingerprint"] = expected_fingerprint
        snapshot["rag_document_validation"] = {
            key: validation[key]
            for key in (
                "schema_version",
                "snapshot_id",
                "docs_fingerprint",
                "document_count",
                "source_counts",
                "card_documents_checked",
                "deck_documents_checked",
                "matchup_documents_checked",
                "passed",
                "failures",
            )
        }
        atomic_write(data_dir / "cards_meta.json", snapshot.get("cards_meta", []))
        atomic_write(data_dir / "top_decks.json", snapshot.get("top_decks", []))
        atomic_write(documents_path, documents)
        migrated = True
    if migrated:
        atomic_write(path, snapshot)
    return snapshot


__all__ = ["load_published_snapshot"]
