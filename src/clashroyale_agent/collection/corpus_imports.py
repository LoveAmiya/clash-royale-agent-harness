"""Workspace and legacy archive import orchestration for the rolling corpus."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from clashroyale_agent.collection.corpus_normalization import (
    as_utc,
    base_fact,
    canonical_battle,
    canonical_loadout_pair,
    fact_json,
    iso,
    normalized_tag,
)
from clashroyale_agent.collection.corpus_policy import CorpusError


def import_workspace_batch(
    connection: sqlite3.Connection,
    workspace_path: Path,
    *,
    batch_id: str,
    batch_type: str,
    started_at: datetime | str,
    leaderboard_frozen_at: datetime | str,
    observed_at: datetime | str,
    create_batch_fn: Callable[..., None],
    insert_fact_observation_fn: Callable[..., tuple[bool, bool, bool]],
) -> dict:
    workspace_path = Path(workspace_path)
    if not workspace_path.is_file():
        raise CorpusError("collection workspace database is missing")
    create_batch_fn(
        batch_id,
        batch_type=batch_type,
        started_at=started_at,
        leaderboard_frozen_at=leaderboard_frozen_at,
    )
    source = sqlite3.connect(f"file:{workspace_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    facts_inserted = 0
    observations_imported = 0
    conflicts = 0
    loadout_metadata_refreshes = 0
    loadout_battles_observed = 0
    complete_loadout_battles = 0
    evolution_slots = 0
    elite_slots = 0
    unknown_special_slots = 0
    slot_contract_failures = 0
    timestamp = iso(observed_at)
    try:
        player_rows = source.execute(
            """
            SELECT player_tag, observer_rank, observer_source, request_status, attempts
            FROM player_requests ORDER BY observer_rank, player_tag
            """
        )
        observation_rows = source.execute(
            """
            SELECT battles.payload, observations.observer_tag, observations.observer_rank,
                   observations.observer_source, observations.expansion_root_rank
            FROM battle_observations AS observations
            JOIN battles ON battles.battle_id=observations.battle_id
            ORDER BY battles.sequence, observations.observer_tag
            """
        )
        for loadout_row in source.execute("SELECT payload FROM battles ORDER BY sequence"):
            try:
                loadout_record = canonical_battle(json.loads(loadout_row["payload"]))
            except (CorpusError, json.JSONDecodeError, TypeError, ValueError):
                continue
            loadout_pair = canonical_loadout_pair(loadout_record)
            if loadout_pair is None:
                continue
            loadout_battles_observed += 1
            complete_loadout_battles += int(bool(loadout_pair.get("complete")))
            for side_name in ("team_loadout", "opponent_loadout"):
                side = loadout_pair.get(side_name)
                if not isinstance(side, dict):
                    continue
                slots = side.get("slot_counts") or {}
                evolution_slots += int(slots.get("evolution") or 0)
                elite_slots += int(slots.get("elite") or 0)
                unknown_special_slots += sum(
                    card.get("special_mode") == "unknown"
                    for card in side.get("cards", [])
                    if isinstance(card, dict)
                )
                slot_contract_failures += int(
                    not bool((side.get("coverage") or {}).get("slot_contract"))
                )
        with connection:
            connection.executemany(
                """
                INSERT INTO batch_players(
                    batch_id, player_tag, observer_rank, observer_source, request_status, attempts
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        batch_id,
                        row["player_tag"],
                        row["observer_rank"],
                        row["observer_source"],
                        row["request_status"],
                        row["attempts"],
                    )
                    for row in player_rows
                ),
            )
            for row in observation_rows:
                try:
                    canonical = canonical_battle(json.loads(row["payload"]))
                    fact = base_fact(canonical)
                    loadout_pair = canonical_loadout_pair(canonical)
                except (CorpusError, json.JSONDecodeError, TypeError, ValueError):
                    conflicts += 1
                    continue
                payload = fact_json(fact)
                inserted, conflicted, metadata_refreshed = insert_fact_observation_fn(
                    batch_id=batch_id,
                    fact=fact,
                    payload=payload,
                    payload_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    timestamp=timestamp,
                    observer_tag=normalized_tag(row["observer_tag"]),
                    observer_rank=row["observer_rank"],
                    observer_source=row["observer_source"],
                    expansion_root_rank=row["expansion_root_rank"],
                    loadout_pair=loadout_pair,
                )
                facts_inserted += int(inserted)
                observations_imported += int(not conflicted)
                conflicts += int(conflicted)
                loadout_metadata_refreshes += int(metadata_refreshed)
            if conflicts:
                connection.execute(
                    "UPDATE collection_batches SET status='conflicted' WHERE batch_id=?",
                    (batch_id,),
                )
    finally:
        source.close()
    return {
        "batch_id": batch_id,
        "facts_inserted": facts_inserted,
        "observations_imported": observations_imported,
        "conflicts": conflicts,
        "loadout_metadata_refreshes": loadout_metadata_refreshes,
        "loadout_coverage": {
            "observed_battle_rows": loadout_battles_observed,
            "complete_battle_rows": complete_loadout_battles,
            "evolution_slots": evolution_slots,
            "elite_slots": elite_slots,
            "unknown_special_slots": unknown_special_slots,
            "slot_contract_failures": slot_contract_failures,
        },
    }


def import_legacy_archive(
    connection: sqlite3.Connection,
    aggregate_path: Path,
    *,
    batch_id: str,
    completed_at: datetime | str,
    create_batch_fn: Callable[..., None],
    insert_fact_observation_fn: Callable[..., tuple[bool, bool, bool]],
) -> dict:
    aggregate_path = Path(aggregate_path)
    if not aggregate_path.is_file():
        raise CorpusError("legacy aggregate database is missing")
    completed = as_utc(completed_at)
    create_batch_fn(
        batch_id,
        batch_type="legacy_weekly_full_only",
        started_at=completed,
        leaderboard_frozen_at=completed,
    )
    source = sqlite3.connect(f"file:{aggregate_path.as_posix()}?mode=ro", uri=True)
    imported = 0
    conflicts = 0
    skipped_invalid = 0
    try:
        with connection:
            for (payload,) in source.execute("SELECT payload FROM battles ORDER BY sequence"):
                try:
                    canonical = canonical_battle(json.loads(payload))
                    fact = base_fact(canonical)
                    loadout_pair = canonical_loadout_pair(canonical)
                except (CorpusError, json.JSONDecodeError, TypeError, ValueError):
                    skipped_invalid += 1
                    continue
                canonical_payload = fact_json(fact)
                _, conflicted, _ = insert_fact_observation_fn(
                    batch_id=batch_id,
                    fact=fact,
                    payload=canonical_payload,
                    payload_hash=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
                    timestamp=iso(completed),
                    observer_tag=fact.get("team_tag") or fact.get("opponent_tag") or "#LEGACY",
                    observer_rank=None,
                    observer_source="legacy_full",
                    expansion_root_rank=None,
                    loadout_pair=loadout_pair,
                )
                imported += int(not conflicted)
                conflicts += int(conflicted)
            report = {
                "passed": conflicts == 0 and imported > 0,
                "failures": [] if conflicts == 0 and imported > 0 else ["invalid_or_conflicting_legacy_records"],
                "unique_battles": imported,
                "skipped_invalid_records": skipped_invalid,
                "conflicting_records": conflicts,
                "ranked_successes": 0,
                "ranked_target": 0,
                "coverage": None,
            }
            connection.execute(
                """
                UPDATE collection_batches SET
                    status=?, completed_at=?, expires_at=?, unique_battles=?,
                    ranked_target=0, top_rank_target=0, validation_json=?
                WHERE batch_id=?
                """,
                (
                    "accepted" if report["passed"] else "rejected",
                    iso(completed),
                    iso(completed + timedelta(days=35)),
                    imported,
                    json.dumps(report, sort_keys=True, separators=(",", ":")),
                    batch_id,
                ),
            )
            if not report["passed"]:
                connection.execute(
                    "DELETE FROM battle_observations WHERE batch_id=?",
                    (batch_id,),
                )
                connection.execute(
                    "DELETE FROM battles WHERE NOT EXISTS (SELECT 1 FROM battle_observations o WHERE o.battle_id=battles.battle_id)"
                )
    finally:
        source.close()
    return report
