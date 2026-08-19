"""Persist and derive the weekly official Supercell snapshot.

The canonical snapshot is the only production source for card, deck, matchup,
and RAG evidence. Derived JSON files are written for compatibility, but are
never independently trusted at runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import app_config as _app_config  # noqa: F401 - bootstrap src package imports

from deck_archetypes import CLASSIFIER_VERSION, archetype_family, classify_deck
try:
    from clashroyale_agent.snapshots.identity import (
        compute_rag_docs_fingerprint as compute_rag_docs_fingerprint_orchestrated,
        is_complete_daily_snapshot as is_complete_daily_snapshot_orchestrated,
        is_path_of_legend_snapshot as is_path_of_legend_snapshot_orchestrated,
        parse_timestamp as parse_timestamp_orchestrated,
        snapshot_age_seconds as snapshot_age_seconds_orchestrated,
        snapshot_id as snapshot_id_orchestrated,
        snapshot_refresh_due as snapshot_refresh_due_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.identity import (
        compute_rag_docs_fingerprint as compute_rag_docs_fingerprint_orchestrated,
        is_complete_daily_snapshot as is_complete_daily_snapshot_orchestrated,
        is_path_of_legend_snapshot as is_path_of_legend_snapshot_orchestrated,
        parse_timestamp as parse_timestamp_orchestrated,
        snapshot_age_seconds as snapshot_age_seconds_orchestrated,
        snapshot_id as snapshot_id_orchestrated,
        snapshot_refresh_due as snapshot_refresh_due_orchestrated,
    )
try:
    from clashroyale_agent.snapshots.evidence_primitives import (
        archetype_name as _archetype_name_primitive,
        counter_summary as _counter_summary_primitive,
        percent as _percent_primitive,
        raw_deck as _raw_deck_primitive,
        with_snapshot_metadata as _with_snapshot_metadata_primitive,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.evidence_primitives import (
        archetype_name as _archetype_name_primitive,
        counter_summary as _counter_summary_primitive,
        percent as _percent_primitive,
        raw_deck as _raw_deck_primitive,
        with_snapshot_metadata as _with_snapshot_metadata_primitive,
    )
try:
    from clashroyale_agent.snapshots.retention import (
        cleanup_snapshot_retention as cleanup_snapshot_retention_orchestrated,
    )
except ModuleNotFoundError:
    # Root compatibility imports also work before an editable package install.
    from src.clashroyale_agent.snapshots.retention import (
        cleanup_snapshot_retention as cleanup_snapshot_retention_orchestrated,
    )
try:
    from clashroyale_agent.snapshots.aggregate_evidence import (
        build_aggregate_evidence_documents as build_aggregate_evidence_documents_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.aggregate_evidence import (
        build_aggregate_evidence_documents as build_aggregate_evidence_documents_orchestrated,
    )
try:
    from clashroyale_agent.snapshots.rag_validation import validate_snapshot_rag_documents as validate_snapshot_rag_documents_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.rag_validation import validate_snapshot_rag_documents as validate_snapshot_rag_documents_orchestrated
try:
    from clashroyale_agent.snapshots.published_loader import load_published_snapshot as load_published_snapshot_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.published_loader import load_published_snapshot as load_published_snapshot_orchestrated
try:
    from clashroyale_agent.snapshots.publisher import publish_daily_snapshot as publish_daily_snapshot_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.publisher import publish_daily_snapshot as publish_daily_snapshot_orchestrated
try:
    from clashroyale_agent.snapshots.documents import build_snapshot_rag_documents as build_snapshot_rag_documents_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.documents import build_snapshot_rag_documents as build_snapshot_rag_documents_orchestrated
try:
    from clashroyale_agent.snapshots.storage import (
        archive_published_snapshot as archive_published_snapshot_orchestrated,
        atomic_copy_file as atomic_copy_file_orchestrated,
        atomic_write_json as atomic_write_json_orchestrated,
        write_streaming_snapshot_json as write_streaming_snapshot_json_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.storage import (
        archive_published_snapshot as archive_published_snapshot_orchestrated,
        atomic_copy_file as atomic_copy_file_orchestrated,
        atomic_write_json as atomic_write_json_orchestrated,
        write_streaming_snapshot_json as write_streaming_snapshot_json_orchestrated,
    )
try:
    from clashroyale_agent.snapshots.status_summary import (
        collector_snapshot_summary as collector_snapshot_summary_orchestrated,
        load_published_snapshot_summary as load_published_snapshot_summary_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.snapshots.status_summary import (
        collector_snapshot_summary as collector_snapshot_summary_orchestrated,
        load_published_snapshot_summary as load_published_snapshot_summary_orchestrated,
    )
from supercell_live import (
    JsonlRecordSequence,
    PATH_OF_LEGEND_BATTLE_TYPE,
    PATH_OF_LEGEND_COLLECTION_SCOPE,
    PATH_OF_LEGEND_SCOPE_CONTRACT,
    build_card_deck_stats,
)


# The DAILY names remain as compatibility aliases for existing imports. The
# production contract is now one complete 200,000-battle snapshot per week.
DAILY_TARGET_BATTLES = 200_000
DAILY_REFRESH_INTERVAL = timedelta(days=7)
SNAPSHOT_FILE_NAME = "official_daily_snapshot.json"
SNAPSHOT_POINTER_FILE_NAME = "official_snapshot_pointer.json"
SNAPSHOT_ARCHIVE_DIR_NAME = "snapshot_archives"
SNAPSHOT_RETENTION_DAYS = 14
SNAPSHOT_RETENTION_MAX_COMPLETE = 2
MIN_MATCHUP_RAG_EVIDENCE_GAMES = 5
MAX_CARD_PROFILE_RAG_DOCUMENTS = 180
MAX_DECK_PROFILE_RAG_DOCUMENTS = 150
MAX_ARCHETYPE_RAG_DOCUMENTS = 80
MAX_CARD_PAIR_RAG_DOCUMENTS = 250
MAX_COUNTER_RAG_DOCUMENTS = 300
MIN_AGGREGATE_EVIDENCE_GAMES = 20


RAG_REQUIRED_COMMON_METADATA = ("snapshot_id", "fetched_at", "sample_battles", "source")


def compute_rag_docs_fingerprint(documents: list[dict]) -> str:
    """Stable identity for the exact evidence corpus, independent of index state."""
    return compute_rag_docs_fingerprint_orchestrated(documents)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _rate_valid(value: object) -> bool:
    return _is_number(value) and 0 <= float(value) <= 100


def _same_number(left: object, right: object) -> bool:
    return _is_number(left) and _is_number(right) and round(float(left), 6) == round(float(right), 6)


def _doc_text_has_missing_value(doc: dict) -> bool:
    text = str(doc.get("text", ""))
    lowered = text.lower()
    return any(token in lowered for token in ("none%", "null%", "nan%", "undefined%"))


def _deck_key(deck_name: object, cards: object) -> tuple[str, tuple[str, ...]]:
    return (
        str(deck_name or "").strip(),
        tuple(str(card).strip() for card in cards if isinstance(card, str) and card.strip())
        if isinstance(cards, list)
        else (),
    )


def _parse_timestamp(value: object) -> datetime | None:
    return parse_timestamp_orchestrated(value)


def is_complete_daily_snapshot(snapshot: object) -> bool:
    return is_complete_daily_snapshot_orchestrated(snapshot, target_battles=DAILY_TARGET_BATTLES, scope=PATH_OF_LEGEND_COLLECTION_SCOPE, contract=PATH_OF_LEGEND_SCOPE_CONTRACT)


def is_path_of_legend_snapshot(snapshot: object) -> bool:
    return bool(
        isinstance(snapshot, dict)
        and snapshot.get("collection_scope") == PATH_OF_LEGEND_COLLECTION_SCOPE
        and snapshot.get("scope_contract") == PATH_OF_LEGEND_SCOPE_CONTRACT
    )


def snapshot_refresh_due(snapshot: dict | None, *, now: datetime | None = None) -> bool:
    return snapshot_refresh_due_orchestrated(snapshot, now=now, refresh_interval=DAILY_REFRESH_INTERVAL, is_complete=is_complete_daily_snapshot)


def snapshot_age_seconds(snapshot: dict | None, *, now: datetime | None = None) -> float | None:
    return snapshot_age_seconds_orchestrated(snapshot, now=now)


def _snapshot_id(snapshot: dict) -> str:
    return snapshot_id_orchestrated(snapshot)


def _atomic_write_json(path: Path, payload: object) -> None:
    return atomic_write_json_orchestrated(path, payload, record_type=JsonlRecordSequence)


def _write_streaming_snapshot_json(handle, payload: dict) -> None:
    return write_streaming_snapshot_json_orchestrated(handle, payload, record_type=JsonlRecordSequence)


def _atomic_copy_file(source: Path, destination: Path) -> None:
    return atomic_copy_file_orchestrated(source, destination)


def _archive_published_snapshot(data_dir: Path, snapshot: dict, documents: list[dict]) -> None:
    """Write a self-contained rollback package before the canonical pointer moves."""
    return archive_published_snapshot_orchestrated(
        data_dir,
        snapshot,
        documents,
        archive_dir_name=SNAPSHOT_ARCHIVE_DIR_NAME,
        atomic_write=_atomic_write_json,
        atomic_copy=_atomic_copy_file,
        collector_summary=_collector_snapshot_summary,
    )


def _collector_snapshot_summary(snapshot: dict) -> dict:
    """Keep collector restarts independent of the 200,000-record JSON array."""
    return collector_snapshot_summary_orchestrated(snapshot, snapshot_file_name=SNAPSHOT_FILE_NAME)


def load_published_snapshot_summary(data_dir: Path) -> dict | None:
    """Load the active collector status without parsing canonical raw battles."""
    return load_published_snapshot_summary_orchestrated(
        data_dir,
        pointer_file_name=SNAPSHOT_POINTER_FILE_NAME,
        archive_dir_name=SNAPSHOT_ARCHIVE_DIR_NAME,
        is_complete=is_complete_daily_snapshot,
    )


def cleanup_snapshot_retention(
    data_dir: Path,
    *,
    active_snapshot_id: str,
    now: datetime | None = None,
) -> dict:
    """Keep the active and newest previous complete snapshot package only.

    This must be called after the active snapshot's RAG index is ready. Unknown
    directories without a valid archive manifest are intentionally untouched.
    """
    return cleanup_snapshot_retention_orchestrated(
        data_dir,
        active_snapshot_id=active_snapshot_id,
        retention_days=SNAPSHOT_RETENTION_DAYS,
        retention_max_complete=SNAPSHOT_RETENTION_MAX_COMPLETE,
        archive_dir_name=SNAPSHOT_ARCHIVE_DIR_NAME,
        storage_roots=(
            SNAPSHOT_ARCHIVE_DIR_NAME,
            "daily_snapshot_qdrant",
            "audit_exports",
            "external_reviews",
            "structured_stats",
        ),
        parse_timestamp=_parse_timestamp,
        now=now,
    )


def _with_snapshot_metadata(items: object, snapshot: dict) -> list[dict]:
    return _with_snapshot_metadata_primitive(items, snapshot)


def _raw_deck(record: object, key: str) -> tuple[str, ...]:
    return _raw_deck_primitive(record, key)


def _archetype_name(deck: tuple[str, ...]) -> str:
    """Compatibility wrapper around the shared feature-weighted classifier."""
    return _archetype_name_primitive(deck, classify_deck)


def _percent(wins: int, games: int) -> float:
    return _percent_primitive(wins, games)


def _counter_summary(counter: Counter, *, limit: int = 3) -> str:
    return _counter_summary_primitive(counter, limit=limit)


def _build_aggregate_evidence_documents(snapshot: dict, common_metadata: dict) -> list[dict]:
    """Compatibility wrapper for packaged aggregate evidence derivation."""
    return build_aggregate_evidence_documents_orchestrated(
        snapshot,
        common_metadata,
        raw_record_type=JsonlRecordSequence,
        raw_deck=_raw_deck,
        archetype_name=_archetype_name,
        archetype_family=archetype_family,
        classifier_version=CLASSIFIER_VERSION,
        percent=_percent,
        counter_summary=_counter_summary,
        max_card_profile_documents=MAX_CARD_PROFILE_RAG_DOCUMENTS,
        max_deck_profile_documents=MAX_DECK_PROFILE_RAG_DOCUMENTS,
        max_archetype_documents=MAX_ARCHETYPE_RAG_DOCUMENTS,
        max_card_pair_documents=MAX_CARD_PAIR_RAG_DOCUMENTS,
        max_counter_documents=MAX_COUNTER_RAG_DOCUMENTS,
        minimum_games=MIN_AGGREGATE_EVIDENCE_GAMES,
    )


def build_snapshot_rag_documents(snapshot: dict) -> list[dict]:
    """Build compact evidence documents from one validated official snapshot."""
    return build_snapshot_rag_documents_orchestrated(
        snapshot,
        is_complete=is_complete_daily_snapshot,
        with_snapshot_metadata=_with_snapshot_metadata,
        build_aggregate_documents=_build_aggregate_evidence_documents,
        min_matchup_games=MIN_MATCHUP_RAG_EVIDENCE_GAMES,
    )


def validate_snapshot_rag_documents(snapshot: dict, documents: list[dict]) -> dict:
    return validate_snapshot_rag_documents_orchestrated(
        snapshot, documents, is_complete=is_complete_daily_snapshot, non_empty=_non_empty_text,
        rate_valid=_rate_valid, missing_value=_doc_text_has_missing_value,
        build_documents=build_snapshot_rag_documents, fingerprint=compute_rag_docs_fingerprint,
        min_matchup_games=MIN_MATCHUP_RAG_EVIDENCE_GAMES,
        min_aggregate_games=MIN_AGGREGATE_EVIDENCE_GAMES,
    )
    snapshot_id = str(snapshot.get("snapshot_id") or "") if isinstance(snapshot, dict) else ""
    failures: set[str] = set()
    invalid_doc_ids: set[str] = set()
    source_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    def invalidate(doc: object, failure: str) -> None:
        failures.add(failure)
        if isinstance(doc, dict):
            invalid_doc_ids.add(str(doc.get("doc_id") or "<missing-doc-id>"))

    if not is_complete_daily_snapshot(snapshot):
        failures.add("incomplete_snapshot")
    if not snapshot_id or not _non_empty_text(snapshot.get("fetched_at")):
        failures.add("invalid_snapshot_identity")
    if not isinstance(snapshot.get("cards_meta"), list) or not snapshot.get("cards_meta"):
        failures.add("cards_meta_missing")
    if not isinstance(snapshot.get("top_decks"), list) or not snapshot.get("top_decks"):
        failures.add("top_decks_missing")
    if not isinstance(documents, list) or not documents:
        failures.add("rag_documents_missing")
        documents = []

    for doc in documents:
        if not isinstance(doc, dict):
            invalidate(doc, "invalid_evidence_fields")
            continue
        doc_id = doc.get("doc_id")
        source_type = doc.get("source_type")
        metadata = doc.get("metadata")
        if not _non_empty_text(doc_id) or doc_id in seen_ids:
            invalidate(doc, "duplicate_or_missing_doc_id")
        else:
            seen_ids.add(doc_id)
        if not _non_empty_text(source_type) or not _non_empty_text(doc.get("text")):
            invalidate(doc, "invalid_evidence_fields")
        else:
            source_counts[source_type] += 1
        if not isinstance(metadata, dict):
            invalidate(doc, "invalid_evidence_fields")
            continue
        if any(not _non_empty_text(metadata.get(key)) for key in ("snapshot_id", "fetched_at", "source")):
            invalidate(doc, "invalid_evidence_fields")
        if metadata.get("snapshot_id") != snapshot_id:
            invalidate(doc, "snapshot_id_mismatch")
        if metadata.get("fetched_at") != snapshot.get("fetched_at"):
            invalidate(doc, "snapshot_metadata_mismatch")
        if metadata.get("sample_battles") != snapshot.get("sample_battles"):
            invalidate(doc, "snapshot_metadata_mismatch")
        if metadata.get("source") != "Supercell API live sample":
            invalidate(doc, "source_mismatch")
        if _doc_text_has_missing_value(doc):
            invalidate(doc, "invalid_evidence_fields")

    try:
        expected_documents = build_snapshot_rag_documents(snapshot)
    except (KeyError, TypeError, ValueError):
        expected_documents = []
        failures.add("rag_document_build_failed")
    expected_by_id = {
        doc.get("doc_id"): doc
        for doc in expected_documents
        if isinstance(doc, dict) and _non_empty_text(doc.get("doc_id"))
    }
    actual_by_id = {
        doc.get("doc_id"): doc
        for doc in documents
        if isinstance(doc, dict) and _non_empty_text(doc.get("doc_id"))
    }
    if set(expected_by_id) != set(actual_by_id):
        failures.add("rag_document_coverage_mismatch")
        invalid_doc_ids.update(str(doc_id) for doc_id in set(expected_by_id) ^ set(actual_by_id))
    for doc_id in set(expected_by_id) & set(actual_by_id):
        if expected_by_id[doc_id] != actual_by_id[doc_id]:
            source_type = expected_by_id[doc_id].get("source_type")
            if source_type == "card":
                failure = "card_document_mismatch"
            elif source_type == "deck":
                failure = "deck_document_mismatch"
            elif source_type == "matchup":
                failure = "matchup_document_mismatch"
            else:
                failure = "aggregate_document_mismatch"
            invalidate(actual_by_id[doc_id], failure)

    for doc in documents:
        if not isinstance(doc, dict) or not isinstance(doc.get("metadata"), dict):
            continue
        metadata = doc["metadata"]
        source_type = doc.get("source_type")
        if source_type == "card":
            valid = (
                _non_empty_text(metadata.get("card_name"))
                and isinstance(metadata.get("rank"), int)
                and metadata["rank"] > 0
                and _rate_valid(metadata.get("usage_rate"))
                and _rate_valid(metadata.get("win_rate"))
                and _rate_valid(metadata.get("clean_win_rate"))
                and isinstance(metadata.get("appearance_count"), int)
                and metadata["appearance_count"] >= 0
            )
            if not valid:
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "deck":
            cards = metadata.get("cards")
            valid = (
                _non_empty_text(metadata.get("deck_name"))
                and isinstance(metadata.get("rank"), int)
                and metadata["rank"] > 0
                and isinstance(cards, list)
                and len(cards) == 8
                and len(set(cards)) == 8
                and all(_non_empty_text(card) for card in cards)
                and isinstance(metadata.get("battles"), int)
                and metadata["battles"] > 0
                and _rate_valid(metadata.get("sample_win_rate"))
            )
            if not valid:
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "matchup":
            valid = (
                _non_empty_text(metadata.get("deck_name"))
                and _non_empty_text(metadata.get("opponent_deck_name"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_MATCHUP_RAG_EVIDENCE_GAMES
                and isinstance(metadata.get("wins"), int)
                and 0 <= metadata["wins"] <= metadata["games"]
                and _rate_valid(metadata.get("win_rate"))
            )
            if not valid:
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "card_profile":
            if not (
                _non_empty_text(metadata.get("card_name"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] > 0
                and _rate_valid(metadata.get("win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "deck_profile":
            cards = metadata.get("cards")
            if not (
                _non_empty_text(metadata.get("deck_name"))
                and isinstance(cards, list)
                and len(cards) == 8
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_AGGREGATE_EVIDENCE_GAMES
                and _rate_valid(metadata.get("sample_win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "archetype":
            if not (
                _non_empty_text(metadata.get("archetype"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] > 0
                and _rate_valid(metadata.get("win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "card_pair":
            cards = metadata.get("cards")
            if not (
                isinstance(cards, list)
                and len(cards) == 2
                and all(_non_empty_text(card) for card in cards)
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_AGGREGATE_EVIDENCE_GAMES
                and _rate_valid(metadata.get("sample_win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "counter":
            if not (
                _non_empty_text(metadata.get("card_name"))
                and _non_empty_text(metadata.get("opponent_card_name"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_AGGREGATE_EVIDENCE_GAMES
                and _rate_valid(metadata.get("win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type != "snapshot":
            invalidate(doc, "unknown_source_type")

    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id or None,
        "docs_fingerprint": compute_rag_docs_fingerprint(documents),
        "document_count": len(documents),
        "source_counts": dict(source_counts),
        "card_documents_checked": source_counts.get("card", 0),
        "deck_documents_checked": source_counts.get("deck", 0),
        "matchup_documents_checked": source_counts.get("matchup", 0),
        "passed": not failures,
        "failures": sorted(failures),
        "invalid_doc_ids": sorted(invalid_doc_ids),
    }


def publish_daily_snapshot(snapshot: dict, data_dir: Path) -> dict:
    """Atomically publish a complete official snapshot and its derived datasets."""
    return publish_daily_snapshot_orchestrated(
        snapshot,
        data_dir,
        is_complete=is_complete_daily_snapshot,
        snapshot_id=_snapshot_id,
        now_utc=lambda: datetime.now(timezone.utc).isoformat(),
        with_snapshot_metadata=_with_snapshot_metadata,
        raw_record_type=JsonlRecordSequence,
        build_card_deck_stats=build_card_deck_stats,
        build_documents=build_snapshot_rag_documents,
        validate_documents=validate_snapshot_rag_documents,
        atomic_write=_atomic_write_json,
        archive_snapshot=_archive_published_snapshot,
        remove_tree=shutil.rmtree,
        scope_name=PATH_OF_LEGEND_COLLECTION_SCOPE,
        scope_contract=PATH_OF_LEGEND_SCOPE_CONTRACT,
        battle_type=PATH_OF_LEGEND_BATTLE_TYPE,
        archive_dir_name=SNAPSHOT_ARCHIVE_DIR_NAME,
        snapshot_file_name=SNAPSHOT_FILE_NAME,
        pointer_file_name=SNAPSHOT_POINTER_FILE_NAME,
    )


def load_published_snapshot(data_dir: Path) -> dict | None:
    return load_published_snapshot_orchestrated(
        data_dir,
        snapshot_file_name=SNAPSHOT_FILE_NAME,
        is_complete=is_complete_daily_snapshot,
        build_stats=build_card_deck_stats,
        build_documents=build_snapshot_rag_documents,
        validate_documents=validate_snapshot_rag_documents,
        fingerprint=compute_rag_docs_fingerprint,
        atomic_write=_atomic_write_json,
    )
