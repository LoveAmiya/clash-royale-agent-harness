from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Iterator

from clashroyale_agent.collection.battle_parser import (
    normalize_battle_record,
    normalize_player_tag as _normalize_player_tag,
    team_cards as _team_cards,
)
from clashroyale_agent.collection.live_snapshot import (
    MAX_RESUMABLE_WORKSPACE_AGE_SECONDS,
    MAX_SPECIAL_FIELD_PROBE_BATTLES,
    PATH_OF_LEGEND_COLLECTION_SCOPE,
    PATH_OF_LEGEND_SCOPE_CONTRACT,
    build_disk_backed_snapshot,
)


class JsonlRecordSequence:
    """Re-iterable JSONL records that never materialize the corpus in memory."""

    def __init__(self, path: Path, count: int):
        self.path = Path(path)
        self.count = max(0, int(count))

    def __len__(self) -> int:
        return self.count

    def __iter__(self) -> Iterator[dict]:
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    if isinstance(record, dict):
                        yield record


class DiskBackedSnapshotWorkspace:
    """Transactional collection state with bounded Python memory usage."""

    def __init__(
        self,
        root: Path,
        *,
        target_battles: int,
        player_limit: int,
        battles_per_player: int,
        seed_player_limit: int | None = None,
        collection_mode: str = "weekly_expanded",
        max_workspace_bytes: int | None = None,
    ):
        self.root = Path(root)
        self.max_workspace_bytes = (
            max(1, int(max_workspace_bytes)) if max_workspace_bytes is not None else None
        )
        resolved_seed_limit = min(player_limit, seed_player_limit or 1000)
        identity = (
            f"{PATH_OF_LEGEND_SCOPE_CONTRACT}:{target_battles}:{player_limit}:"
            f"{battles_per_player}:{resolved_seed_limit}:{collection_mode}"
        )
        self.path = self.root / ("collection-" + hashlib.sha256(identity.encode("ascii")).hexdigest()[:12])
        self.path.mkdir(parents=True, exist_ok=True)
        self.database_path = self.path / "aggregates.sqlite"
        self.raw_path = self.path / "raw_battles.jsonl"
        self.players_path = self.path / "players.json"
        self.connection = sqlite3.connect(self.database_path)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS battles (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                battle_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS battle_observations (
                battle_id TEXT NOT NULL,
                observer_tag TEXT NOT NULL,
                observer_rank INTEGER,
                observer_source TEXT NOT NULL,
                expansion_root_rank INTEGER,
                PRIMARY KEY (battle_id, observer_tag, observer_source)
            );
            CREATE TABLE IF NOT EXISTS player_requests (
                player_tag TEXT PRIMARY KEY,
                observer_rank INTEGER,
                observer_source TEXT NOT NULL,
                expansion_root_rank INTEGER,
                request_status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS card_stats (
                card_name TEXT PRIMARY KEY,
                appearances INTEGER NOT NULL,
                wins INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deck_stats (
                deck_key TEXT PRIMARY KEY,
                deck_json TEXT NOT NULL,
                battles INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                elixir_total REAL NOT NULL,
                elixir_samples INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deck_cards (
                deck_key TEXT NOT NULL,
                card_name TEXT NOT NULL,
                PRIMARY KEY (deck_key, card_name)
            );
            CREATE TABLE IF NOT EXISTS matchup_stats (
                deck_key TEXT NOT NULL,
                opponent_key TEXT NOT NULL,
                opponent_json TEXT NOT NULL,
                games INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                PRIMARY KEY (deck_key, opponent_key)
            );
            CREATE TABLE IF NOT EXISTS probe_battles (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL
            );
            """
        )
        expected = {
            "schema_version": "6",
            "collection_scope": PATH_OF_LEGEND_COLLECTION_SCOPE,
            "scope_contract": PATH_OF_LEGEND_SCOPE_CONTRACT,
            "target_battles": str(target_battles),
            "player_limit": str(player_limit),
            "battles_per_player": str(battles_per_player),
            "seed_player_limit": str(resolved_seed_limit),
            "collection_mode": collection_mode,
        }
        existing = dict(self.connection.execute("SELECT key, value FROM metadata"))
        try:
            workspace_age = time.time() - float(existing.get("started_at_epoch", time.time()))
        except (TypeError, ValueError):
            workspace_age = MAX_RESUMABLE_WORKSPACE_AGE_SECONDS + 1
        try:
            prior_rate_limited = int(existing.get("rate_limited", "0") or 0)
        except (TypeError, ValueError):
            prior_rate_limited = 1
        if existing and (
            any(existing.get(key) != value for key, value in expected.items())
            or workspace_age > MAX_RESUMABLE_WORKSPACE_AGE_SECONDS
            or prior_rate_limited > 0
        ):
            self.connection.close()
            shutil.rmtree(self.path)
            self.__init__(
                root,
                target_battles=target_battles,
                player_limit=player_limit,
                battles_per_player=battles_per_player,
                seed_player_limit=resolved_seed_limit,
                collection_mode=collection_mode,
                max_workspace_bytes=self.max_workspace_bytes,
            )
            return
        self.connection.executemany(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            expected.items(),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES ('started_at_epoch', ?)",
            (str(time.time()),),
        )
        self.connection.commit()
        self.max_in_memory_battle_records = 0
        self.assert_storage_budget()

    @property
    def storage_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in self.path.iterdir()
            if path.is_file()
        )

    def assert_storage_budget(self) -> int:
        size = self.storage_bytes
        if self.max_workspace_bytes is not None and size > self.max_workspace_bytes:
            self.close()
            raise ValueError("snapshot workspace byte limit exceeded")
        return size

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def discard(self) -> None:
        work_path = self.path.resolve()
        root_path = self.root.resolve()
        self.close()
        if work_path.is_dir() and work_path.parent == root_path and work_path.name.startswith("collection-"):
            shutil.rmtree(work_path)

    def mark_rate_limited(self, count: int) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('rate_limited', ?)",
            (str(max(1, int(count))),),
        )
        self.connection.commit()

    def load_players(self) -> list[dict] | None:
        try:
            payload = json.loads(self.players_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, list):
            return None
        tags = [_normalize_player_tag(item.get("tag")) for item in payload if isinstance(item, dict)]
        if len(tags) != len(payload) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            return None
        if len(set(tags)) != len(tags):
            return None
        for item, tag in zip(payload, tags):
            item["tag"] = tag
        return payload

    def save_players(self, players: list[dict]) -> None:
        temp_path = self.players_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(players, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(self.players_path)

    def metadata_int(self, key: str, default: int = 0) -> int:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        try:
            return int(row[0]) if row else default
        except (TypeError, ValueError):
            return default

    @property
    def processed_players(self) -> int:
        return self.metadata_int("processed_players")

    @property
    def battle_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM battles").fetchone()[0])

    @property
    def observation_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM battle_observations").fetchone()[0])

    def failed_ranked_players(self) -> list[dict]:
        return [
            {"tag": row[0], "rank": row[1], "seed_source": "global_path_of_legend"}
            for row in self.connection.execute(
                """
                SELECT player_tag, observer_rank FROM player_requests
                WHERE observer_source='ranked_direct' AND request_status='failed'
                ORDER BY observer_rank, player_tag
                """
            )
        ]

    @property
    def failed_player_count(self) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM player_requests WHERE request_status='failed'"
            ).fetchone()[0]
        )

    def record_player(
        self,
        *,
        player_index: int,
        player_tag: str,
        battles: list[dict],
        failed: bool,
        target_battles: int,
        observer_rank: int | None = None,
        observer_source: str = "ranked_direct",
        expansion_root_rank: int | None = None,
    ) -> int:
        self.max_in_memory_battle_records = max(self.max_in_memory_battle_records, len(battles))
        accepted = 0
        starting_battle_count = self.battle_count
        probe_count = int(self.connection.execute("SELECT COUNT(*) FROM probe_battles").fetchone()[0])
        if not failed:
            for battle_index, battle in enumerate(battles):
                record = normalize_battle_record(battle, player_tag)
                if not record:
                    continue
                deck = tuple(record.get("team_deck") or ())
                opponent_deck = tuple(record.get("opponent_deck") or ())
                card_names = deck + opponent_deck
                if (
                    not 1 <= len(deck) <= 16
                    or len(opponent_deck) > 16
                    or any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in card_names)
                ):
                    continue
                payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                battle_id = str(record.get("battle_id") or "")
                storage_battle_id = battle_id or (
                    f"missing-time:{player_index}:{battle_index}:"
                    f"{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"
                )
                existing_fact = self.connection.execute(
                    "SELECT 1 FROM battles WHERE battle_id=?",
                    (storage_battle_id,),
                ).fetchone()
                if starting_battle_count + accepted >= target_battles and existing_fact is None:
                    continue
                cursor = self.connection.execute(
                    "INSERT OR IGNORE INTO battles(battle_id, payload) VALUES (?, ?)",
                    (storage_battle_id, payload),
                )
                self.connection.execute(
                    """
                    INSERT INTO battle_observations(
                        battle_id, observer_tag, observer_rank, observer_source, expansion_root_rank
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(battle_id, observer_tag, observer_source) DO UPDATE SET
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
                        storage_battle_id,
                        _normalize_player_tag(player_tag),
                        int(observer_rank) if observer_rank is not None else None,
                        str(observer_source),
                        int(expansion_root_rank) if expansion_root_rank is not None else None,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                accepted += 1
                won = int(bool(record.get("won")))
                deck_json = json.dumps(deck, ensure_ascii=False, separators=(",", ":"))
                opponent_json = json.dumps(opponent_deck, ensure_ascii=False, separators=(",", ":"))
                team_cards = _team_cards(battle)
                costs = [
                    float(card["elixirCost"])
                    for card in team_cards
                    if isinstance(card.get("elixirCost"), (int, float))
                ]
                elixir_average = sum(costs) / len(costs) if costs else 0.0
                self.connection.execute(
                    """
                    INSERT INTO deck_stats(deck_key, deck_json, battles, wins, elixir_total, elixir_samples)
                    VALUES (?, ?, 1, ?, ?, ?)
                    ON CONFLICT(deck_key) DO UPDATE SET
                        battles = battles + 1,
                        wins = wins + excluded.wins,
                        elixir_total = elixir_total + excluded.elixir_total,
                        elixir_samples = elixir_samples + excluded.elixir_samples
                    """,
                    (deck_json, deck_json, won, elixir_average, int(bool(costs))),
                )
                for card_name in deck:
                    self.connection.execute(
                        """
                        INSERT INTO card_stats(card_name, appearances, wins) VALUES (?, 1, ?)
                        ON CONFLICT(card_name) DO UPDATE SET
                            appearances = appearances + 1,
                            wins = wins + excluded.wins
                        """,
                        (card_name, won),
                    )
                    self.connection.execute(
                        "INSERT OR IGNORE INTO deck_cards(deck_key, card_name) VALUES (?, ?)",
                        (deck_json, card_name),
                    )
                if opponent_deck:
                    self.connection.execute(
                        """
                        INSERT INTO matchup_stats(deck_key, opponent_key, opponent_json, games, wins)
                        VALUES (?, ?, ?, 1, ?)
                        ON CONFLICT(deck_key, opponent_key) DO UPDATE SET
                            games = games + 1,
                            wins = wins + excluded.wins
                        """,
                        (deck_json, opponent_json, opponent_json, won),
                    )
                if probe_count < MAX_SPECIAL_FIELD_PROBE_BATTLES:
                    self.connection.execute(
                        "INSERT INTO probe_battles(payload) VALUES (?)",
                        (json.dumps(battle, ensure_ascii=False, separators=(",", ":")),),
                    )
                    probe_count += 1
        self.connection.execute(
            """
            INSERT INTO player_requests(
                player_tag, observer_rank, observer_source, expansion_root_rank, request_status, attempts
            ) VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(player_tag) DO UPDATE SET
                observer_rank=excluded.observer_rank,
                observer_source=excluded.observer_source,
                expansion_root_rank=excluded.expansion_root_rank,
                request_status=excluded.request_status,
                attempts=player_requests.attempts + 1
            """,
            (
                _normalize_player_tag(player_tag),
                int(observer_rank) if observer_rank is not None else None,
                str(observer_source),
                int(expansion_root_rank) if expansion_root_rank is not None else None,
                "failed" if failed else "success",
            ),
        )
        processed_players = max(self.processed_players, player_index + 1)
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('processed_players', ?)",
            (str(processed_players),),
        )
        if accepted:
            sampled_players = self.metadata_int("sampled_players") + 1
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('sampled_players', ?)",
                (str(sampled_players),),
            )
        if failed:
            failed_players = self.metadata_int("failed_players") + 1
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('failed_players', ?)",
                (str(failed_players),),
            )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('max_in_memory_battle_records', ?)",
            (str(max(self.metadata_int("max_in_memory_battle_records"), self.max_in_memory_battle_records)),),
        )
        self.connection.commit()
        self.assert_storage_budget()
        return accepted

    def export_raw_records(self) -> JsonlRecordSequence:
        with self.raw_path.open("w", encoding="utf-8", newline="\n") as handle:
            for (payload,) in self.connection.execute("SELECT payload FROM battles ORDER BY sequence"):
                handle.write(payload)
                handle.write("\n")
        return JsonlRecordSequence(self.raw_path, self.battle_count)

    def build_snapshot(
        self,
        *,
        fetched_at: str,
        target_battles: int,
        collection_metadata: dict,
        export_raw_battles: bool = True,
    ) -> dict:
        return build_disk_backed_snapshot(
            self,
            fetched_at=fetched_at,
            target_battles=target_battles,
            collection_metadata=collection_metadata,
            export_raw_battles=export_raw_battles,
        )


__all__ = ["DiskBackedSnapshotWorkspace", "JsonlRecordSequence"]
