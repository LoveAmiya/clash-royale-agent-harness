"""SQLite persistence primitives for rolling battle observations."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from clashroyale_agent.collection.corpus_policy import CorpusConflictError
from clashroyale_agent.collection.loadout_normalization import (
    LOADOUT_SCHEMA_VERSION,
    loadout_fact_signature,
    loadout_payload,
    loadout_quality,
)


def upsert_loadout(
    connection: Any,
    *,
    batch_id: str,
    battle_id: str,
    loadout_pair: dict,
    timestamp: str,
    fact_json: Callable[[dict], str],
) -> tuple[bool, bool]:
    team = loadout_pair.get("team_loadout")
    opponent = loadout_pair.get("opponent_loadout")
    pair_payload = fact_json(loadout_pair)
    incoming_hash = hashlib.sha256(pair_payload.encode("utf-8")).hexdigest()
    existing = connection.execute(
        "SELECT * FROM battle_loadouts WHERE battle_id=?",
        (battle_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO battle_loadouts(
                battle_id, schema_version, team_loadout_json, opponent_loadout_json,
                complete, loadout_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                battle_id,
                LOADOUT_SCHEMA_VERSION,
                loadout_payload(team) if isinstance(team, dict) else None,
                loadout_payload(opponent) if isinstance(opponent, dict) else None,
                int(bool(loadout_pair.get("complete"))),
                incoming_hash,
                timestamp,
            ),
        )
        return False, False
    if existing["loadout_hash"] == incoming_hash:
        return False, False
    existing_team = json.loads(existing["team_loadout_json"]) if existing["team_loadout_json"] else None
    existing_opponent = (
        json.loads(existing["opponent_loadout_json"]) if existing["opponent_loadout_json"] else None
    )
    if bool(existing["complete"]) and bool(loadout_pair.get("complete")):
        existing_fact = (
            loadout_fact_signature(existing_team),
            loadout_fact_signature(existing_opponent),
        )
        incoming_fact = (
            loadout_fact_signature(team),
            loadout_fact_signature(opponent),
        )
        if existing_fact == incoming_fact and all(existing_fact):
            connection.execute(
                """
                UPDATE battle_loadouts
                SET schema_version=?, team_loadout_json=?, opponent_loadout_json=?,
                    complete=?, loadout_hash=?, updated_at=?
                WHERE battle_id=?
                """,
                (
                    LOADOUT_SCHEMA_VERSION,
                    loadout_payload(team),
                    loadout_payload(opponent),
                    1,
                    incoming_hash,
                    timestamp,
                    battle_id,
                ),
            )
            return False, True
        connection.execute(
            """
            INSERT INTO corpus_conflicts(
                batch_id, battle_id, existing_hash, incoming_hash, detected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (batch_id, battle_id, existing["loadout_hash"], incoming_hash, timestamp),
        )
        connection.execute(
            "UPDATE collection_batches SET status='conflicted' WHERE batch_id=?",
            (batch_id,),
        )
        return True, False
    existing_quality = (loadout_quality(existing_team), loadout_quality(existing_opponent))
    incoming_quality = (loadout_quality(team), loadout_quality(opponent))
    if incoming_quality > existing_quality:
        connection.execute(
            """
            UPDATE battle_loadouts
            SET schema_version=?, team_loadout_json=?, opponent_loadout_json=?,
                complete=?, loadout_hash=?, updated_at=?
            WHERE battle_id=?
            """,
            (
                LOADOUT_SCHEMA_VERSION,
                loadout_payload(team) if isinstance(team, dict) else None,
                loadout_payload(opponent) if isinstance(opponent, dict) else None,
                int(bool(loadout_pair.get("complete"))),
                incoming_hash,
                timestamp,
                battle_id,
            ),
        )
    return False, False


def insert_fact_observation(
    connection: Any,
    *,
    batch_id: str,
    fact: dict,
    payload: str,
    payload_hash: str,
    timestamp: str,
    observer_tag: str,
    observer_rank: int | None,
    observer_source: str,
    expansion_root_rank: int | None,
    loadout_pair: dict | None = None,
    upsert_loadout_fn: Callable[..., tuple[bool, bool]] = upsert_loadout,
    fact_json: Callable[[dict], str] | None = None,
) -> tuple[bool, bool, bool]:
    row = connection.execute(
        "SELECT payload_hash FROM battles WHERE battle_id = ?",
        (fact["battle_id"],),
    ).fetchone()
    if row is not None and row["payload_hash"] != payload_hash:
        connection.execute(
            """
            INSERT INTO corpus_conflicts(
                batch_id, battle_id, existing_hash, incoming_hash, detected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (batch_id, fact["battle_id"], row["payload_hash"], payload_hash, timestamp),
        )
        connection.execute(
            "UPDATE collection_batches SET status='conflicted' WHERE batch_id=?",
            (batch_id,),
        )
        return False, True, False
    inserted = row is None
    if inserted:
        connection.execute(
            """
            INSERT INTO battles(battle_id, battle_time, payload, payload_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (fact["battle_id"], fact["battle_time"], payload, payload_hash, timestamp),
        )
    loadout_metadata_refreshed = False
    if loadout_pair is not None:
        loadout_conflicted, loadout_metadata_refreshed = upsert_loadout_fn(
            connection,
            batch_id=batch_id,
            battle_id=fact["battle_id"],
            loadout_pair=loadout_pair,
            timestamp=timestamp,
            fact_json=fact_json or (lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )
        if loadout_conflicted:
            return False, True, False
    connection.execute(
        """
        INSERT INTO battle_observations(
            batch_id, battle_id, observer_tag, observer_rank, observer_source,
            expansion_root_rank, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id, battle_id, observer_tag, observer_source) DO UPDATE SET
            observer_rank=CASE
                WHEN battle_observations.observer_rank IS NULL THEN excluded.observer_rank
                WHEN excluded.observer_rank IS NULL THEN battle_observations.observer_rank
                ELSE MIN(battle_observations.observer_rank, excluded.observer_rank)
            END,
            expansion_root_rank=CASE
                WHEN battle_observations.expansion_root_rank IS NULL THEN excluded.expansion_root_rank
                WHEN excluded.expansion_root_rank IS NULL THEN battle_observations.expansion_root_rank
                ELSE MIN(battle_observations.expansion_root_rank, excluded.expansion_root_rank)
            END
        """,
        (
            batch_id,
            fact["battle_id"],
            observer_tag,
            int(observer_rank) if observer_rank is not None else None,
            str(observer_source),
            int(expansion_root_rank) if expansion_root_rank is not None else None,
            timestamp,
        ),
    )
    return inserted, False, loadout_metadata_refreshed


__all__ = ["insert_fact_observation", "upsert_loadout"]
