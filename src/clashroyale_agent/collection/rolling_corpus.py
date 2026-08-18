"""Rolling Path of Legend fact store with batch-scoped provenance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from clashroyale_agent.collection.loadout_normalization import (
    LOADOUT_SCHEMA_VERSION,
    canonical_loadout,
    loadout_fact_signature,
    loadout_payload,
    loadout_quality,
)


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


class CorpusError(ValueError):
    """Base error for deterministic corpus validation failures."""


class CorpusConflictError(CorpusError):
    """Raised when one battle ID is associated with conflicting facts."""


class CorpusWriterBusyError(CorpusError):
    """Raised when another collection, retention, or publication writer owns the lock."""


class CorpusWriterLock(AbstractContextManager):
    """Cross-process single-writer lock released automatically on process exit."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if self.handle.tell() == 0 and self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise CorpusWriterBusyError("another rolling corpus writer is already running") from exc
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return False


@dataclass(frozen=True)
class BatchValidationPolicy:
    required_top_rank: int = 100
    ranked_player_target: int = 1000
    minimum_coverage: float = 0.99
    minimum_expansion_coverage: float = 0.99
    weekly_target_battles: int = 200_000


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise CorpusError("timestamp must be an ISO string or datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | str) -> str:
    return _as_utc(value).isoformat()


def _normalized_tag(value: object) -> str:
    return str(value or "").strip().upper()


def _canonical_battle(record: dict) -> dict:
    if not isinstance(record, dict):
        raise CorpusError("battle must be an object")
    battle_id = str(record.get("battle_id") or "").strip()
    battle_time = str(record.get("battle_time") or "").strip()
    battle_type = str(record.get("battle_type") or "").strip()
    team_deck = record.get("team_deck")
    opponent_deck = record.get("opponent_deck")
    if not battle_id or not battle_time:
        raise CorpusError("battle_id and battle_time are required")
    if battle_type.casefold() != _PATH_OF_LEGEND_TYPE.casefold():
        raise CorpusError("only Path of Legend battles are accepted")
    if not isinstance(team_deck, list) or not isinstance(opponent_deck, list):
        raise CorpusError("both decks must be arrays")
    if len(team_deck) != 8 or len(opponent_deck) != 8:
        raise CorpusError("both decks must contain exactly eight cards")
    if any(not isinstance(card, str) or not card.strip() for card in team_deck + opponent_deck):
        raise CorpusError("deck cards must be non-empty strings")
    team_crowns = int(record.get("team_crowns", 0) or 0)
    opponent_crowns = int(record.get("opponent_crowns", 0) or 0)
    sides = sorted(
        (
            {
                "tag": _normalized_tag(record.get("team_tag")) or None,
                "deck": [str(card).strip() for card in team_deck],
                "crowns": team_crowns,
                "loadout": canonical_loadout(record.get("team_loadout")),
            },
            {
                "tag": _normalized_tag(record.get("opponent_tag")) or None,
                "deck": [str(card).strip() for card in opponent_deck],
                "crowns": opponent_crowns,
                "loadout": canonical_loadout(record.get("opponent_loadout")),
            },
        ),
        key=lambda side: (side["tag"] or "", tuple(side["deck"]), side["crowns"]),
    )
    canonical_team, canonical_opponent = sides
    canonical = {
        "battle_id": battle_id,
        "battle_type": _PATH_OF_LEGEND_TYPE,
        "battle_time": battle_time,
        "team_tag": canonical_team["tag"],
        "opponent_tag": canonical_opponent["tag"],
        "team_deck": canonical_team["deck"],
        "opponent_deck": canonical_opponent["deck"],
        "team_crowns": canonical_team["crowns"],
        "opponent_crowns": canonical_opponent["crowns"],
        "won": canonical_team["crowns"] > canonical_opponent["crowns"],
    }
    if canonical_team["loadout"] is not None or canonical_opponent["loadout"] is not None:
        canonical.update(
            {
                "loadout_schema_version": LOADOUT_SCHEMA_VERSION,
                "team_loadout": canonical_team["loadout"],
                "opponent_loadout": canonical_opponent["loadout"],
            }
        )
    return canonical


def _base_fact(record: dict) -> dict:
    return {key: value for key, value in record.items() if key not in {
        "loadout_schema_version", "team_loadout", "opponent_loadout"
    }}


def _canonical_loadout_pair(record: dict) -> dict | None:
    team = record.get("team_loadout")
    opponent = record.get("opponent_loadout")
    if not isinstance(team, dict) and not isinstance(opponent, dict):
        return None
    return {
        "schema_version": LOADOUT_SCHEMA_VERSION,
        "team_loadout": team,
        "opponent_loadout": opponent,
        "complete": bool(
            isinstance(team, dict)
            and team.get("complete")
            and isinstance(opponent, dict)
            and opponent.get("complete")
        ),
    }


def _fact_json(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS collection_batches(
                batch_id TEXT PRIMARY KEY,
                batch_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                leaderboard_frozen_at TEXT NOT NULL,
                completed_at TEXT,
                expires_at TEXT,
                request_count INTEGER NOT NULL DEFAULT 0,
                rate_limited INTEGER NOT NULL DEFAULT 0,
                refresh_budget_exhausted INTEGER NOT NULL DEFAULT 0,
                source_exhausted INTEGER NOT NULL DEFAULT 0,
                ranked_successes INTEGER NOT NULL DEFAULT 0,
                ranked_target INTEGER NOT NULL DEFAULT 1000,
                top_rank_successes INTEGER NOT NULL DEFAULT 0,
                top_rank_target INTEGER NOT NULL DEFAULT 100,
                coverage REAL NOT NULL DEFAULT 0,
                unique_battles INTEGER NOT NULL DEFAULT 0,
                validation_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS battles(
                battle_id TEXT PRIMARY KEY,
                battle_time TEXT NOT NULL,
                payload TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS battle_observations(
                batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id) ON DELETE CASCADE,
                battle_id TEXT NOT NULL REFERENCES battles(battle_id) ON DELETE CASCADE,
                observer_tag TEXT NOT NULL,
                observer_rank INTEGER,
                observer_source TEXT NOT NULL,
                expansion_root_rank INTEGER,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(batch_id, battle_id, observer_tag, observer_source)
            );
            CREATE TABLE IF NOT EXISTS battle_loadouts(
                battle_id TEXT PRIMARY KEY REFERENCES battles(battle_id) ON DELETE CASCADE,
                schema_version INTEGER NOT NULL,
                team_loadout_json TEXT,
                opponent_loadout_json TEXT,
                complete INTEGER NOT NULL DEFAULT 0,
                loadout_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_players(
                batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id) ON DELETE CASCADE,
                player_tag TEXT NOT NULL,
                observer_rank INTEGER,
                observer_source TEXT NOT NULL,
                request_status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                PRIMARY KEY(batch_id, player_tag)
            );
            CREATE TABLE IF NOT EXISTS corpus_conflicts(
                conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                battle_id TEXT NOT NULL,
                existing_hash TEXT NOT NULL,
                incoming_hash TEXT NOT NULL,
                detected_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publication_generations(
                generation_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT,
                manifest_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_batches_status_completed
                ON collection_batches(status, completed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_observations_battle
                ON battle_observations(battle_id, observer_rank);
            CREATE INDEX IF NOT EXISTS idx_observations_rank
                ON battle_observations(observer_source, observer_rank, batch_id);
            CREATE INDEX IF NOT EXISTS idx_loadouts_complete
                ON battle_loadouts(complete, battle_id);
            """
        )
        self.connection.commit()

    def create_batch(
        self,
        batch_id: str,
        *,
        batch_type: str,
        started_at: datetime | str,
        leaderboard_frozen_at: datetime | str,
    ) -> None:
        normalized_id = str(batch_id or "").strip()
        if not normalized_id:
            raise CorpusError("batch_id is required")
        if batch_type not in _BATCH_TYPES:
            raise CorpusError("invalid batch_type")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO collection_batches(
                    batch_id, batch_type, status, started_at, leaderboard_frozen_at
                ) VALUES (?, ?, 'collecting', ?, ?)
                """,
                (normalized_id, batch_type, _iso(started_at), _iso(leaderboard_frozen_at)),
            )

    def batch_status(self, batch_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM collection_batches WHERE batch_id=?",
            (str(batch_id),),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def unique_batch_id(self, preferred: str) -> str:
        candidate = str(preferred or "").strip()
        if not candidate:
            raise CorpusError("batch_id is required")
        if self.batch_status(candidate) is None:
            return candidate
        attempt = 2
        while self.batch_status(f"{candidate}-attempt-{attempt}") is not None:
            attempt += 1
        return f"{candidate}-attempt-{attempt}"

    def begin_publication_generation(self, generation_id: str, *, created_at: datetime | str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO publication_generations(generation_id, status, created_at)
                VALUES (?, 'building', ?)
                ON CONFLICT(generation_id) DO UPDATE SET
                    status='building', created_at=excluded.created_at, published_at=NULL, manifest_json='{}'
                """,
                (generation_id, _iso(created_at)),
            )

    def finish_publication_generation(
        self,
        generation_id: str,
        *,
        status: str,
        manifest: dict,
        published_at: datetime | str | None = None,
    ) -> None:
        if status not in {"published", "failed"}:
            raise CorpusError("invalid publication generation status")
        with self.connection:
            self.connection.execute(
                """
                UPDATE publication_generations
                SET status=?, published_at=?, manifest_json=?
                WHERE generation_id=?
                """,
                (
                    status,
                    _iso(published_at) if published_at is not None else None,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    generation_id,
                ),
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
        row = self.connection.execute(
            "SELECT payload_hash FROM battles WHERE battle_id = ?",
            (fact["battle_id"],),
        ).fetchone()
        if row is not None and row["payload_hash"] != payload_hash:
            self.connection.execute(
                """
                INSERT INTO corpus_conflicts(
                    batch_id, battle_id, existing_hash, incoming_hash, detected_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (batch_id, fact["battle_id"], row["payload_hash"], payload_hash, timestamp),
            )
            self.connection.execute(
                "UPDATE collection_batches SET status='conflicted' WHERE batch_id=?",
                (batch_id,),
            )
            return False, True, False
        inserted = row is None
        if inserted:
            self.connection.execute(
                """
                INSERT INTO battles(battle_id, battle_time, payload, payload_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fact["battle_id"], fact["battle_time"], payload, payload_hash, timestamp),
            )
        loadout_metadata_refreshed = False
        if loadout_pair is not None:
            loadout_conflicted, loadout_metadata_refreshed = self._upsert_loadout(
                batch_id=batch_id,
                battle_id=fact["battle_id"],
                loadout_pair=loadout_pair,
                timestamp=timestamp,
            )
            if loadout_conflicted:
                return False, True, False
        self.connection.execute(
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

    def _upsert_loadout(
        self,
        *,
        batch_id: str,
        battle_id: str,
        loadout_pair: dict,
        timestamp: str,
    ) -> tuple[bool, bool]:
        team = loadout_pair.get("team_loadout")
        opponent = loadout_pair.get("opponent_loadout")
        pair_payload = _fact_json(loadout_pair)
        incoming_hash = hashlib.sha256(pair_payload.encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            "SELECT * FROM battle_loadouts WHERE battle_id=?",
            (battle_id,),
        ).fetchone()
        if existing is None:
            self.connection.execute(
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
                self.connection.execute(
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
            self.connection.execute(
                """
                INSERT INTO corpus_conflicts(
                    batch_id, battle_id, existing_hash, incoming_hash, detected_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (batch_id, battle_id, existing["loadout_hash"], incoming_hash, timestamp),
            )
            self.connection.execute(
                "UPDATE collection_batches SET status='conflicted' WHERE batch_id=?",
                (batch_id,),
            )
            return True, False
        existing_quality = (loadout_quality(existing_team), loadout_quality(existing_opponent))
        incoming_quality = (loadout_quality(team), loadout_quality(opponent))
        if incoming_quality > existing_quality:
            self.connection.execute(
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
        workspace_path = Path(workspace_path)
        if not workspace_path.is_file():
            raise CorpusError("collection workspace database is missing")
        self.create_batch(
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
        timestamp = _iso(observed_at)
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
                    loadout_record = _canonical_battle(json.loads(loadout_row["payload"]))
                except (CorpusError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                loadout_pair = _canonical_loadout_pair(loadout_record)
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
            with self.connection:
                self.connection.executemany(
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
                        canonical = _canonical_battle(json.loads(row["payload"]))
                        fact = _base_fact(canonical)
                        loadout_pair = _canonical_loadout_pair(canonical)
                    except (CorpusError, json.JSONDecodeError, TypeError, ValueError):
                        conflicts += 1
                        continue
                    payload = _fact_json(fact)
                    inserted, conflicted, metadata_refreshed = self._insert_fact_observation(
                        batch_id=batch_id,
                        fact=fact,
                        payload=payload,
                        payload_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                        timestamp=timestamp,
                        observer_tag=_normalized_tag(row["observer_tag"]),
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
                    self.connection.execute(
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
        self,
        aggregate_path: Path,
        *,
        batch_id: str,
        completed_at: datetime | str,
    ) -> dict:
        aggregate_path = Path(aggregate_path)
        if not aggregate_path.is_file():
            raise CorpusError("legacy aggregate database is missing")
        completed = _as_utc(completed_at)
        self.create_batch(
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
            with self.connection:
                for (payload,) in source.execute("SELECT payload FROM battles ORDER BY sequence"):
                    try:
                        canonical = _canonical_battle(json.loads(payload))
                        fact = _base_fact(canonical)
                        loadout_pair = _canonical_loadout_pair(canonical)
                    except (CorpusError, json.JSONDecodeError, TypeError, ValueError):
                        skipped_invalid += 1
                        continue
                    canonical_payload = _fact_json(fact)
                    _, conflicted, _ = self._insert_fact_observation(
                        batch_id=batch_id,
                        fact=fact,
                        payload=canonical_payload,
                        payload_hash=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
                        timestamp=_iso(completed),
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
                self.connection.execute(
                    """
                    UPDATE collection_batches SET
                        status=?, completed_at=?, expires_at=?, unique_battles=?,
                        ranked_target=0, top_rank_target=0, validation_json=?
                    WHERE batch_id=?
                    """,
                    (
                        "accepted" if report["passed"] else "rejected",
                        _iso(completed),
                        _iso(completed + timedelta(days=35)),
                        imported,
                        json.dumps(report, sort_keys=True, separators=(",", ":")),
                        batch_id,
                    ),
                )
                if not report["passed"]:
                    self.connection.execute(
                        "DELETE FROM battle_observations WHERE batch_id=?",
                        (batch_id,),
                    )
                    self.connection.execute(
                        "DELETE FROM battles WHERE NOT EXISTS (SELECT 1 FROM battle_observations o WHERE o.battle_id=battles.battle_id)"
                    )
        finally:
            source.close()
        return report

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
        batch = self.connection.execute(
            "SELECT batch_type, status FROM collection_batches WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise CorpusError("unknown batch_id")
        top_successes = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM batch_players
                WHERE batch_id=? AND observer_source=? AND observer_rank<=? AND request_status='success'
                """,
                (batch_id, _RANKED_SOURCE, policy.required_top_rank),
            ).fetchone()[0]
        )
        ranked_successes = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM batch_players
                WHERE batch_id=? AND observer_source=? AND observer_rank<=? AND request_status='success'
                """,
                (batch_id, _RANKED_SOURCE, policy.ranked_player_target),
            ).fetchone()[0]
        )
        expansion_target = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM batch_players WHERE batch_id=? AND observer_source=?",
                (batch_id, _EXPANSION_SOURCE),
            ).fetchone()[0]
        )
        expansion_successes = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM batch_players
                WHERE batch_id=? AND observer_source=? AND request_status='success'
                """,
                (batch_id, _EXPANSION_SOURCE),
            ).fetchone()[0]
        )
        unique_battles = int(
            self.connection.execute(
                "SELECT COUNT(DISTINCT battle_id) FROM battle_observations WHERE batch_id=?",
                (batch_id,),
            ).fetchone()[0]
        )
        coverage = ranked_successes / policy.ranked_player_target if policy.ranked_player_target else 0.0
        expansion_coverage = expansion_successes / expansion_target if expansion_target else 0.0
        failures = []
        if top_successes != policy.required_top_rank:
            failures.append("incomplete_top_rank_coverage")
        if coverage < policy.minimum_coverage:
            failures.append("ranked_coverage_below_threshold")
        if int(rate_limited) != 0:
            failures.append("rate_limited")
        if refresh_budget_exhausted:
            failures.append("refresh_budget_exhausted")
        if batch["status"] == "conflicted" or self.conflict_count(batch_id):
            failures.append("conflicting_battle_facts")
        if batch["batch_type"] == "weekly_expanded":
            if source_exhausted:
                if expansion_target == 0:
                    failures.append("expansion_queue_empty")
                elif expansion_coverage < policy.minimum_expansion_coverage:
                    failures.append("expansion_coverage_below_threshold")
                if unique_battles == 0:
                    failures.append("no_usable_battles")
            elif unique_battles != policy.weekly_target_battles:
                failures.append("weekly_target_not_met")
        passed = not failures
        completed = _as_utc(completed_at)
        report = {
            "passed": passed,
            "failures": failures,
            "top_rank_successes": top_successes,
            "top_rank_target": policy.required_top_rank,
            "ranked_successes": ranked_successes,
            "ranked_target": policy.ranked_player_target,
            "coverage": round(coverage, 6),
            "expansion_successes": expansion_successes,
            "expansion_target": expansion_target,
            "expansion_coverage": round(expansion_coverage, 6),
            "unique_battles": unique_battles,
        }
        with self.connection:
            self.connection.execute(
                """
                UPDATE collection_batches SET
                    status=?, completed_at=?, expires_at=?, request_count=?, rate_limited=?,
                    refresh_budget_exhausted=?, source_exhausted=?, ranked_successes=?, ranked_target=?,
                    top_rank_successes=?, top_rank_target=?, coverage=?, unique_battles=?, validation_json=?
                WHERE batch_id=?
                """,
                (
                    "accepted" if passed else "rejected",
                    _iso(completed),
                    _iso(completed + timedelta(days=35)),
                    max(0, int(request_count)),
                    max(0, int(rate_limited)),
                    int(bool(refresh_budget_exhausted)),
                    int(bool(source_exhausted)),
                    ranked_successes,
                    policy.ranked_player_target,
                    top_successes,
                    policy.required_top_rank,
                    coverage,
                    unique_battles,
                    json.dumps(report, sort_keys=True, separators=(",", ":")),
                    batch_id,
                ),
            )
            if not passed:
                self.connection.execute(
                    "DELETE FROM battle_observations WHERE batch_id=?",
                    (batch_id,),
                )
                self.connection.execute(
                    """
                    DELETE FROM battles
                    WHERE NOT EXISTS (
                        SELECT 1 FROM battle_observations AS observations
                        WHERE observations.battle_id=battles.battle_id
                    )
                    """
                )
        return report

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

