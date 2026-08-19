"""Compact snapshot summaries used by collector and readiness status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


SUMMARY_FIELDS = (
    "snapshot_id", "published_at", "fetched_at", "sample_battles", "target_battles",
    "shortfall_battles", "ranked_players", "fetched_players", "sampled_players",
    "failed_players", "usable_battles", "leaderboard_candidate_limit", "leaderboard_start_rank",
    "leaderboard_last_scanned_rank", "collection_metrics", "special_fields_probe",
    "rag_docs_fingerprint", "rag_document_counts", "rag_document_validation", "aggregate_store",
)


def collector_snapshot_summary(snapshot: dict, *, snapshot_file_name: str) -> dict:
    summary = {key: snapshot.get(key) for key in SUMMARY_FIELDS if key in snapshot}
    raw_storage = snapshot.get("raw_battles_storage")
    if not isinstance(raw_storage, dict):
        raw_storage = {"format": "canonical_json_array", "canonical_file": snapshot_file_name, "record_count": int(snapshot.get("sample_battles") or 0)}
    summary["raw_battles"] = []
    summary["raw_battles_storage"] = {**raw_storage, "loaded": False}
    summary["cards_meta"] = []
    summary["top_decks"] = []
    summary["card_deck_stats"] = {}
    summary["deck_matchups"] = []
    return summary


def load_published_snapshot_summary(
    data_dir: Path,
    *,
    pointer_file_name: str,
    archive_dir_name: str,
    is_complete: Callable[[dict], bool],
) -> dict | None:
    try:
        pointer = json.loads((data_dir / pointer_file_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    snapshot_id = str(pointer.get("snapshot_id") or "") if isinstance(pointer, dict) else ""
    if not snapshot_id or any(character in snapshot_id for character in ("/", "\\", ":")):
        return None
    try:
        summary = json.loads((data_dir / archive_dir_name / snapshot_id / "collector_snapshot.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if summary.get("snapshot_id") != snapshot_id or not is_complete(summary):
        return None
    return summary
