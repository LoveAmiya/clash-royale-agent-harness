"""Snapshot identity and freshness primitives."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any


def compute_rag_docs_fingerprint(documents: list[dict]) -> str:
    return hashlib.sha256(json.dumps(documents, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_complete_daily_snapshot(snapshot: object, *, target_battles: int, scope: str, contract: str) -> bool:
    if not isinstance(snapshot, dict): return False
    if snapshot.get("sample_battles") != target_battles or snapshot.get("target_battles") != target_battles or snapshot.get("shortfall_battles", 0) != 0: return False
    if "collection_scope" in snapshot and (snapshot.get("collection_scope") != scope or snapshot.get("scope_contract") != contract): return False
    raw_storage = snapshot.get("raw_battles_storage")
    if isinstance(raw_storage, dict):
        try: declared_count = int(raw_storage.get("record_count"))
        except (TypeError, ValueError): return False
        if raw_storage.get("loaded") is False:
            if declared_count != int(snapshot.get("sample_battles") or 0): return False
        else:
            try: raw_count = len(snapshot.get("raw_battles"))
            except TypeError: return False
            if raw_count != declared_count or raw_count != int(snapshot.get("sample_battles") or 0): return False
    metrics = snapshot.get("collection_metrics", {})
    return not bool(metrics.get("refresh_budget_exhausted")) and not bool(metrics.get("rate_limited"))


def is_path_of_legend_snapshot(snapshot: object, *, scope: str, contract: str, battle_type: str) -> bool:
    if not isinstance(snapshot, dict) or snapshot.get("collection_scope") != scope or snapshot.get("scope_contract") != contract:
        return False
    records = snapshot.get("raw_battles")
    return isinstance(records, list) and bool(records) and all(isinstance(record, dict) and record.get("battle_type") == battle_type for record in records)


def snapshot_age_seconds(snapshot: dict | None, *, now: datetime | None = None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    reference = parse_timestamp(snapshot.get("published_at") or snapshot.get("fetched_at"))
    if reference is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - reference).total_seconds())


def snapshot_refresh_due(snapshot: dict | None, *, now: datetime | None = None, refresh_interval: timedelta, is_complete: Any) -> bool:
    if not is_complete(snapshot): return True
    reference = parse_timestamp(snapshot.get("published_at") or snapshot.get("fetched_at"))
    if reference is None: return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None: current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - reference >= refresh_interval


def snapshot_id(snapshot: dict) -> str:
    existing = snapshot.get("snapshot_id")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    stable_fields = {"fetched_at": snapshot.get("fetched_at"), "sample_battles": snapshot.get("sample_battles"), "target_battles": snapshot.get("target_battles"), "cards_meta": snapshot.get("cards_meta", []), "top_decks": snapshot.get("top_decks", [])}
    digest = hashlib.sha256(json.dumps(stable_fields, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"supercell-{digest}"
