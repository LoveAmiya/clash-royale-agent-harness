"""Rolling Path of Legend fact store with batch-scoped provenance."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from clashroyale_agent.collection.corpus_policy import (
    BatchValidationPolicy,
    CorpusConflictError,
    CorpusError,
    CorpusWriterBusyError,
    CorpusWriterLock,
)
from clashroyale_agent.collection.corpus_normalization import (
    as_utc as _as_utc,
    base_fact as _base_fact,
    canonical_battle as _canonical_battle,
    canonical_loadout_pair as _canonical_loadout_pair,
    fact_json as _fact_json,
    iso as _iso,
    normalized_tag as _normalized_tag,
)
from clashroyale_agent.collection.corpus_observations import (
    insert_fact_observation,
    upsert_loadout,
)
from clashroyale_agent.collection.corpus_lifecycle import (
    batch_status as batch_status_orchestrated,
    begin_publication_generation as begin_publication_generation_orchestrated,
    create_batch as create_batch_orchestrated,
    finish_publication_generation as finish_publication_generation_orchestrated,
    unique_batch_id as unique_batch_id_orchestrated,
)
from clashroyale_agent.collection.corpus_schema import create_schema as create_corpus_schema
from clashroyale_agent.collection.corpus_imports import (
    import_legacy_archive as import_legacy_archive_orchestrated,
    import_workspace_batch as import_workspace_batch_orchestrated,
)
from clashroyale_agent.collection.corpus_finalize import finalize_batch as finalize_batch_orchestrated


DEFAULT_DATASET_SCOPE = "7d_all"
DATASET_WINDOWS = (7, 35)
DATASET_RANK_LIMITS = (100, 200, 500, 1000)
DATASET_WINDOW_DEFINITIONS = {
    "7d": {
        "window_kind": "current",
        "start_offset_days": 0,
        "end_offset_days": 7,
    },
    "d7_14": {
        "window_kind": "historical_slice",
        "start_offset_days": 7,
        "end_offset_days": 14,
    },
    "d14_21": {
        "window_kind": "historical_slice",
        "start_offset_days": 14,
        "end_offset_days": 21,
    },
    "d21_28": {
        "window_kind": "historical_slice",
        "start_offset_days": 21,
        "end_offset_days": 28,
    },
    "d28_35": {
        "window_kind": "historical_slice",
        "start_offset_days": 28,
        "end_offset_days": 35,
    },
    "35d": {
        "window_kind": "rolling",
        "start_offset_days": 0,
        "end_offset_days": 35,
    },
}
DATASET_SCOPES = tuple(
    f"{prefix}_{suffix}"
    for prefix in DATASET_WINDOW_DEFINITIONS
    for suffix in (*[f"top_{rank}" for rank in DATASET_RANK_LIMITS], "all")
)
_SCOPE_PATTERN = re.compile(
    r"^(7d|d7_14|d14_21|d21_28|d28_35|35d)_(?:top_(100|200|500|1000)|all)$"
)
_BATCH_TYPES = {"daily_ranked", "weekly_expanded", "legacy_weekly_full_only"}
_RANKED_SOURCE = "ranked_direct"
_EXPANSION_SOURCE = "opponent_expansion"
_PATH_OF_LEGEND_TYPE = "pathOfLegend"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class RollingCorpusStore:
    """SQLite fact store; query consumers read materialized artifacts instead."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _create_schema(self) -> None:
        create_corpus_schema(self.connection)

    def create_batch(
        self,
        batch_id: str,
        *,
        batch_type: str,
        started_at: datetime | str,
        leaderboard_frozen_at: datetime | str,
    ) -> None:
        create_batch_orchestrated(
            self.connection,
            batch_id,
            batch_type=batch_type,
            started_at=started_at,
            leaderboard_frozen_at=leaderboard_frozen_at,
            batch_types=_BATCH_TYPES,
        )

    def batch_status(self, batch_id: str) -> str | None:
        return batch_status_orchestrated(self.connection, batch_id)

    def unique_batch_id(self, preferred: str) -> str:
        return unique_batch_id_orchestrated(self.connection, preferred)

    def begin_publication_generation(self, generation_id: str, *, created_at: datetime | str) -> None:
        begin_publication_generation_orchestrated(
            self.connection, generation_id, created_at=created_at
        )

    def finish_publication_generation(
        self,
        generation_id: str,
        *,
        status: str,
        manifest: dict,
        published_at: datetime | str | None = None,
    ) -> None:
        finish_publication_generation_orchestrated(
            self.connection,
            generation_id,
            status=status,
            manifest=manifest,
            published_at=published_at,
        )

    def record_player(
        self,
        batch_id: str,
        *,
        player_tag: str,
        observer_rank: int | None,
        observer_source: str,
        request_status: str,
        attempts: int,
    ) -> None:
        tag = _normalized_tag(player_tag)
        if not tag:
            raise CorpusError("player_tag is required")
        rank = int(observer_rank) if observer_rank is not None else None
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO batch_players(
                    batch_id, player_tag, observer_rank, observer_source, request_status, attempts
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id, player_tag) DO UPDATE SET
                    observer_rank=excluded.observer_rank,
                    observer_source=excluded.observer_source,
                    request_status=excluded.request_status,
                    attempts=excluded.attempts
                """,
                (batch_id, tag, rank, str(observer_source), str(request_status), max(1, int(attempts))),
            )

    def ingest_battle(
        self,
        batch_id: str,
        record: dict,
        *,
        observer_tag: str,
        observer_rank: int | None,
        observer_source: str,
        expansion_root_rank: int | None = None,
        observed_at: datetime | str | None = None,
    ) -> bool:
        canonical = _canonical_battle(record)
        fact = _base_fact(canonical)
        loadout_pair = _canonical_loadout_pair(canonical)
        payload = _fact_json(fact)
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        timestamp = _iso(observed_at or datetime.now(timezone.utc))
        tag = _normalized_tag(observer_tag)
        if not tag:
            raise CorpusError("observer_tag is required")
        with self.connection:
            inserted, conflict_detected, _ = self._insert_fact_observation(
                batch_id=batch_id,
                fact=fact,
                payload=payload,
                payload_hash=payload_hash,
                timestamp=timestamp,
                observer_tag=tag,
                observer_rank=observer_rank,
                observer_source=observer_source,
                expansion_root_rank=expansion_root_rank,
                loadout_pair=loadout_pair,
            )
        if conflict_detected:
            raise CorpusConflictError("battle ID maps to conflicting facts")
        return inserted

    def _insert_fact_observation(
        self,
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
    ) -> tuple[bool, bool, bool]:
        return insert_fact_observation(
            self.connection,
            batch_id=batch_id,
            fact=fact,
            payload=payload,
            payload_hash=payload_hash,
            timestamp=timestamp,
            observer_tag=observer_tag,
            observer_rank=observer_rank,
            observer_source=observer_source,
            expansion_root_rank=expansion_root_rank,
            loadout_pair=loadout_pair,
            upsert_loadout_fn=lambda connection, **kwargs: self._upsert_loadout(
                batch_id=kwargs["batch_id"],
                battle_id=kwargs["battle_id"],
                loadout_pair=kwargs["loadout_pair"],
                timestamp=kwargs["timestamp"],
            ),
            fact_json=_fact_json,
        )

    def _upsert_loadout(
        self,
        *,
        batch_id: str,
        battle_id: str,
        loadout_pair: dict,
        timestamp: str,
    ) -> tuple[bool, bool]:
        return upsert_loadout(
            self.connection,
            batch_id=batch_id,
            battle_id=battle_id,
            loadout_pair=loadout_pair,
            timestamp=timestamp,
            fact_json=_fact_json,
        )

    def import_workspace_batch(
        self,
        workspace_path: Path,
        *,
        batch_id: str,
        batch_type: str,
        started_at: datetime | str,
        leaderboard_frozen_at: datetime | str,
        observed_at: datetime | str,
    ) -> dict:
        return import_workspace_batch_orchestrated(
            self.connection,
            workspace_path,
            batch_id=batch_id,
            batch_type=batch_type,
            started_at=started_at,
            leaderboard_frozen_at=leaderboard_frozen_at,
            observed_at=observed_at,
            create_batch_fn=self.create_batch,
            insert_fact_observation_fn=self._insert_fact_observation,
        )

    def import_legacy_archive(
        self,
        aggregate_path: Path,
        *,
        batch_id: str,
        completed_at: datetime | str,
    ) -> dict:
        return import_legacy_archive_orchestrated(
            self.connection,
            aggregate_path,
            batch_id=batch_id,
            completed_at=completed_at,
            create_batch_fn=self.create_batch,
            insert_fact_observation_fn=self._insert_fact_observation,
        )

    def assert_disk_capacity(self, *, minimum_free_bytes: int = 20 * 1024**3) -> dict:
        usage = shutil.disk_usage(self.path.parent)
        if usage.free < minimum_free_bytes:
            raise CorpusError("insufficient free disk space for collection")
        return {"free_bytes": usage.free, "minimum_free_bytes": int(minimum_free_bytes)}

    def accept_batch_for_test(self, batch_id: str, *, completed_at: datetime | str) -> None:
        completed = _as_utc(completed_at)
        with self.connection:
            self.connection.execute(
                """
                UPDATE collection_batches
                SET status='accepted', completed_at=?, expires_at=?
                WHERE batch_id=?
                """,
                (_iso(completed), _iso(completed + timedelta(days=35)), batch_id),
            )

    def finalize_batch(
        self,
        batch_id: str,
        *,
        completed_at: datetime | str,
        policy: BatchValidationPolicy,
        request_count: int,
        rate_limited: int,
        refresh_budget_exhausted: bool,
        source_exhausted: bool,
    ) -> dict:
        return finalize_batch_orchestrated(
            self.connection,
            batch_id,
            completed_at=completed_at,
            policy=policy,
            request_count=request_count,
            rate_limited=rate_limited,
            refresh_budget_exhausted=refresh_budget_exhausted,
            source_exhausted=source_exhausted,
            ranked_source=_RANKED_SOURCE,
            expansion_source=_EXPANSION_SOURCE,
            conflict_count=self.conflict_count,
        )

    def _scope_parts(self, scope: str) -> tuple[dict, int | None]:
        match = _SCOPE_PATTERN.fullmatch(str(scope or ""))
        if match is None:
            raise CorpusError("invalid dataset_scope")
        return DATASET_WINDOW_DEFINITIONS[match.group(1)], int(match.group(2)) if match.group(2) else None

    @staticmethod
    def _scope_bounds(definition: dict, current: datetime) -> tuple[datetime, datetime]:
        return (
            current - timedelta(days=int(definition["end_offset_days"])),
            current - timedelta(days=int(definition["start_offset_days"])),
        )

    def scope_battle_ids(self, scope: str, *, now: datetime | str) -> list[str]:
        definition, rank_limit = self._scope_parts(scope)
        current = _as_utc(now)
        window_started, window_ended = self._scope_bounds(definition, current)
        parameters: list[object] = [_iso(window_started), _iso(window_ended)]
        ranked_clause = ""
        if rank_limit is not None:
            ranked_clause = " AND observations.observer_source=? AND observations.observer_rank<=?"
            parameters.extend((_RANKED_SOURCE, rank_limit))
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT observations.battle_id
            FROM battle_observations AS observations
            JOIN collection_batches AS batches ON batches.batch_id=observations.batch_id
            WHERE batches.status='accepted'
              AND batches.completed_at>? AND batches.completed_at<=?
              {ranked_clause}
            ORDER BY observations.battle_id
            """,
            parameters,
        ).fetchall()
        return [str(row[0]) for row in rows]

    def iter_scope_battles(self, scope: str, *, now: datetime | str) -> Iterator[dict]:
        definition, rank_limit = self._scope_parts(scope)
        current = _as_utc(now)
        window_started, window_ended = self._scope_bounds(definition, current)
        parameters: list[object] = [_iso(window_started), _iso(window_ended)]
        ranked_clause = ""
        if rank_limit is not None:
            ranked_clause = " AND observations.observer_source=? AND observations.observer_rank<=?"
            parameters.extend((_RANKED_SOURCE, rank_limit))
        rows = self.connection.execute(
            f"""
            SELECT facts.payload, loadouts.schema_version, loadouts.team_loadout_json,
                   loadouts.opponent_loadout_json
            FROM battles AS facts
            LEFT JOIN battle_loadouts AS loadouts ON loadouts.battle_id=facts.battle_id
            WHERE EXISTS (
                SELECT 1
                FROM battle_observations AS observations
                JOIN collection_batches AS batches ON batches.batch_id=observations.batch_id
                WHERE observations.battle_id=facts.battle_id
                  AND batches.status='accepted'
                  AND batches.completed_at>? AND batches.completed_at<=?
                  {ranked_clause}
            )
            ORDER BY facts.battle_id
            """,
            parameters,
        )
        for row in rows:
            record = json.loads(row["payload"])
            if row["schema_version"] is not None:
                record["loadout_schema_version"] = int(row["schema_version"])
                record["team_loadout"] = (
                    json.loads(row["team_loadout_json"]) if row["team_loadout_json"] else None
                )
                record["opponent_loadout"] = (
                    json.loads(row["opponent_loadout_json"]) if row["opponent_loadout_json"] else None
                )
            yield record

    def scope_battle_count(self, scope: str, *, now: datetime | str) -> int:
        definition, rank_limit = self._scope_parts(scope)
        current = _as_utc(now)
        window_started, window_ended = self._scope_bounds(definition, current)
        parameters: list[object] = [_iso(window_started), _iso(window_ended)]
        ranked_clause = ""
        if rank_limit is not None:
            ranked_clause = " AND observations.observer_source=? AND observations.observer_rank<=?"
            parameters.extend((_RANKED_SOURCE, rank_limit))
        row = self.connection.execute(
            f"""
            SELECT COUNT(*) FROM battles AS facts
            WHERE EXISTS (
                SELECT 1
                FROM battle_observations AS observations
                JOIN collection_batches AS batches ON batches.batch_id=observations.batch_id
                WHERE observations.battle_id=facts.battle_id
                  AND batches.status='accepted'
                  AND batches.completed_at>? AND batches.completed_at<=?
                  {ranked_clause}
            )
            """,
            parameters,
        ).fetchone()
        return int(row[0])

    def minimum_rank(self, battle_id: str, *, window_days: int, now: datetime | str) -> int | None:
        if window_days not in DATASET_WINDOWS:
            raise CorpusError("unsupported rolling window")
        current = _as_utc(now)
        row = self.connection.execute(
            """
            SELECT MIN(observations.observer_rank)
            FROM battle_observations AS observations
            JOIN collection_batches AS batches ON batches.batch_id=observations.batch_id
            WHERE observations.battle_id=? AND observations.observer_source=?
              AND batches.status='accepted' AND batches.completed_at>? AND batches.completed_at<=?
            """,
            (battle_id, _RANKED_SOURCE, _iso(current - timedelta(days=window_days)), _iso(current)),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def dataset_summary(self, scope: str, *, now: datetime | str) -> dict:
        definition, rank_limit = self._scope_parts(scope)
        current = _as_utc(now)
        window_started, window_ended = self._scope_bounds(definition, current)
        start_offset = int(definition["start_offset_days"])
        end_offset = int(definition["end_offset_days"])
        window_days = end_offset - start_offset
        rows = self.connection.execute(
            """
            SELECT batch_type, completed_at, ranked_successes, ranked_target, coverage
            FROM collection_batches
            WHERE status='accepted' AND completed_at>? AND completed_at<=?
            ORDER BY completed_at
            """,
            (_iso(window_started), _iso(window_ended)),
        ).fetchall()
        weekly_count = sum(row["batch_type"] == "weekly_expanded" for row in rows)
        daily_count = sum(row["batch_type"] == "daily_ranked" for row in rows)
        ranked_successes = sum(int(row["ranked_successes"] or 0) for row in rows)
        ranked_targets = sum(int(row["ranked_target"] or 0) for row in rows)
        observed_dates = {
            _as_utc(row["completed_at"]).astimezone(_SHANGHAI).date().isoformat()
            for row in rows
            if row["batch_type"] in {"daily_ranked", "weekly_expanded"}
        }
        local_today = current.astimezone(_SHANGHAI).date()
        expected_dates = [
            (local_today - timedelta(days=offset)).isoformat()
            for offset in range(start_offset, end_offset)
        ]
        unique_battles = self.scope_battle_count(scope, now=current)
        return {
            "dataset_scope": scope,
            "window_days": window_days,
            "window_kind": definition["window_kind"],
            "window_start_offset_days": start_offset,
            "window_end_offset_days": end_offset,
            "rank_limit": rank_limit,
            "window_started_at": _iso(window_started),
            "window_ended_at": _iso(window_ended),
            "unique_battles": unique_battles,
            "ready": unique_battles > 0,
            "weekly_batch_count": weekly_count,
            "daily_batch_count": daily_count,
            "ranked_coverage": round(ranked_successes / ranked_targets, 6) if ranked_targets else None,
            "missing_collection_dates": [value for value in expected_dates if value not in observed_dates],
        }

    def dataset_summaries(self, *, now: datetime | str) -> list[dict]:
        return [self.dataset_summary(scope, now=now) for scope in DATASET_SCOPES]

    def expire_and_prune(self, *, now: datetime | str) -> dict:
        current = _as_utc(now)
        cutoff = current - timedelta(days=35)
        accepted = self.connection.execute(
            """
            SELECT batch_id, batch_type, completed_at FROM collection_batches
            WHERE status='accepted' ORDER BY completed_at DESC, batch_id DESC
            """
        ).fetchall()
        retain: set[str] = set()
        weekly_count = 0
        daily_count = 0
        for row in accepted:
            completed = _as_utc(row["completed_at"])
            if completed <= cutoff or completed > current:
                continue
            if row["batch_type"] == "weekly_expanded":
                weekly_count += 1
            elif row["batch_type"] == "daily_ranked":
                if daily_count >= 35:
                    continue
                daily_count += 1
            retain.add(str(row["batch_id"]))
        expired = [str(row["batch_id"]) for row in accepted if str(row["batch_id"]) not in retain]
        with self.connection:
            if expired:
                placeholders = ",".join("?" for _ in expired)
                self.connection.execute(
                    f"UPDATE collection_batches SET status='expired' WHERE batch_id IN ({placeholders})",
                    expired,
                )
                self.connection.execute(
                    f"DELETE FROM battle_observations WHERE batch_id IN ({placeholders})",
                    expired,
                )
            self.connection.execute(
                "DELETE FROM battles WHERE NOT EXISTS (SELECT 1 FROM battle_observations o WHERE o.battle_id=battles.battle_id)"
            )
        return {
            "retained_weekly_batches": weekly_count,
            "retained_daily_batches": daily_count,
            "expired_batch_ids": expired,
            "remaining_battles": self.fact_count(),
        }

    def fact_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM battles").fetchone()[0])

    def observation_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM battle_observations").fetchone()[0])

    def conflict_count(self, batch_id: str) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM corpus_conflicts WHERE batch_id=?",
                (batch_id,),
            ).fetchone()[0]
        )

