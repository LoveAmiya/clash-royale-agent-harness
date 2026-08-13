"""Bounded adapter for official Clash Royale API leaderboard battle logs."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from typing import Callable, Iterator
from urllib.parse import quote

import requests

from battle_loadout import LOADOUT_SCHEMA_VERSION, normalize_side_loadout


SUPERCELL_API_BASE_URL = "https://api.clashroyale.com/v1"
SUPERCELL_SOURCE_URL = "https://developer.clashroyale.com/"
CARD_DECK_VARIANTS_PER_CARD = 20
MAX_PUBLISHED_DECK_MATCHUPS = 20_000
MAX_SPECIAL_FIELD_PROBE_BATTLES = 100
MAX_RESUMABLE_WORKSPACE_AGE_SECONDS = 14 * 24 * 60 * 60
MAX_RANKING_SEED_LOCATIONS = 80
PATH_OF_LEGEND_BATTLE_TYPE = "pathOfLegend"
PATH_OF_LEGEND_COLLECTION_SCOPE = "path_of_legend"
PATH_OF_LEGEND_SCOPE_CONTRACT = "path_of_legend_only_v1"
logger = logging.getLogger(__name__)


def _normalize_player_tag(value: object) -> str:
    return value.strip().upper() if isinstance(value, str) and value.strip() else ""


def _append_unique_player(players: list[dict], seen_tags: set[str], player: dict, *, source: str) -> bool:
    tag = _normalize_player_tag(player.get("tag") if isinstance(player, dict) else None)
    if not tag or tag in seen_tags:
        return False
    record = dict(player)
    record["tag"] = tag
    record.setdefault("seed_source", source)
    seen_tags.add(tag)
    players.append(record)
    return True


def is_path_of_legend_battle(battle: object) -> bool:
    """Return whether an official battle-log item belongs to Path of Legend."""
    if not isinstance(battle, dict):
        return False
    battle_type = battle.get("type")
    return isinstance(battle_type, str) and battle_type.strip().casefold() == PATH_OF_LEGEND_BATTLE_TYPE.casefold()


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
        total_battles = self.battle_count
        if not total_battles:
            raise ValueError("official API returned no usable battle-log decks")
        cards_meta = []
        distinct_cards = int(self.connection.execute("SELECT COUNT(*) FROM card_stats").fetchone()[0])
        if distinct_cards > 256:
            raise ValueError("official snapshot contains an unsafe number of distinct card names")
        card_rows = self.connection.execute(
            "SELECT card_name, appearances, wins FROM card_stats ORDER BY appearances DESC, card_name"
        ).fetchall()
        for rank, (card_name, appearances, wins) in enumerate(card_rows, start=1):
            win_rate = round(wins / appearances * 100, 1) if appearances else 0.0
            cards_meta.append(
                {
                    "rank": rank,
                    "card_name": card_name,
                    "rating": 0,
                    "usage_rate": round(appearances / total_battles * 100, 1),
                    "usage_delta": 0.0,
                    "win_rate": win_rate,
                    "win_delta": 0.0,
                    "clean_win_rate": win_rate,
                    "mode": "Official Path of Legend battle-log sample",
                    "source": "Supercell API live sample",
                    "source_url": SUPERCELL_SOURCE_URL,
                    "fetched_at": fetched_at,
                    "sample_battles": total_battles,
                    "target_battles": target_battles,
                    "appearance_count": appearances,
                }
            )

        top_decks = []
        deck_rows = self.connection.execute(
            """
            SELECT deck_json, battles, wins, elixir_total, elixir_samples
            FROM deck_stats ORDER BY battles DESC, deck_json LIMIT 30
            """
        ).fetchall()
        for rank, (deck_json, battles, wins, elixir_total, elixir_samples) in enumerate(deck_rows, start=1):
            deck = json.loads(deck_json)
            top_decks.append(
                {
                    "rank": rank,
                    "player_name": "Global Path of Legend sample",
                    "clan_name": "Official Supercell API",
                    "deck_name": " / ".join(deck),
                    "avg_elixir": round(elixir_total / elixir_samples, 1) if elixir_samples else None,
                    "battles": battles,
                    "trophies": None,
                    "last_ladder_battle": fetched_at,
                    "cards": deck,
                    "sample_win_rate": round(wins / battles * 100, 1) if battles else 0.0,
                    "source": "Supercell API live sample",
                    "source_url": SUPERCELL_SOURCE_URL,
                    "fetched_at": fetched_at,
                    "sample_battles": total_battles,
                    "target_battles": target_battles,
                }
            )

        card_deck_stats: dict[str, list[dict]] = {}
        for card_name, _, _ in card_rows:
            variants = self.connection.execute(
                """
                SELECT decks.deck_json, decks.battles, decks.wins
                FROM deck_cards AS cards
                JOIN deck_stats AS decks ON decks.deck_key = cards.deck_key
                WHERE cards.card_name = ?
                ORDER BY decks.battles DESC, decks.deck_json
                LIMIT ?
                """,
                (card_name, CARD_DECK_VARIANTS_PER_CARD),
            ).fetchall()
            card_deck_stats[card_name] = [
                {
                    "deck_name": " / ".join(deck := json.loads(deck_json)),
                    "cards": deck,
                    "battles": battles,
                    "sample_win_rate": round(wins / battles * 100, 1),
                    "source": "Supercell API live sample",
                    "source_url": SUPERCELL_SOURCE_URL,
                    "fetched_at": fetched_at,
                    "sample_battles": total_battles,
                    "target_battles": target_battles,
                }
                for deck_json, battles, wins in variants
            ]

        matchup_total = int(self.connection.execute("SELECT COUNT(*) FROM matchup_stats").fetchone()[0])
        matchup_rows = self.connection.execute(
            """
            SELECT decks.deck_json, matchups.opponent_json, matchups.games, matchups.wins
            FROM matchup_stats AS matchups
            JOIN deck_stats AS decks ON decks.deck_key = matchups.deck_key
            ORDER BY matchups.games DESC, decks.deck_json, matchups.opponent_json
            LIMIT ?
            """,
            (MAX_PUBLISHED_DECK_MATCHUPS,),
        ).fetchall()
        deck_matchups = []
        for deck_json, opponent_json, games, wins in matchup_rows:
            deck = json.loads(deck_json)
            opponent_deck = json.loads(opponent_json)
            deck_matchups.append(
                {
                    "deck_name": " / ".join(deck),
                    "opponent_deck_name": " / ".join(opponent_deck),
                    "games": games,
                    "wins": wins,
                    "win_rate": round(wins / games * 100, 1) if games else 0.0,
                    "source": "Supercell API live sample",
                    "source_url": SUPERCELL_SOURCE_URL,
                    "fetched_at": fetched_at,
                    "sample_battles": total_battles,
                    "target_battles": target_battles,
                }
            )

        deck_profile_opponents: dict[str, list[dict]] = {}
        profile_rows = self.connection.execute(
            "SELECT deck_key, deck_json FROM deck_stats WHERE battles >= 20 ORDER BY battles DESC, deck_json LIMIT 150"
        ).fetchall()
        for deck_key, deck_json in profile_rows:
            deck_name = " / ".join(sorted(json.loads(deck_json)))
            opponent_rows = self.connection.execute(
                """
                SELECT opponent_json, games
                FROM matchup_stats
                WHERE deck_key = ?
                ORDER BY games DESC, opponent_json
                LIMIT 3
                """,
                (deck_key,),
            ).fetchall()
            deck_profile_opponents[deck_name] = [
                {
                    "opponent_deck_name": " / ".join(sorted(json.loads(opponent_json))),
                    "games": games,
                }
                for opponent_json, games in opponent_rows
            ]

        probe_battles = [
            json.loads(payload)
            for (payload,) in self.connection.execute("SELECT payload FROM probe_battles ORDER BY sequence")
        ]
        raw_records = self.export_raw_records() if export_raw_battles else ()
        metrics = dict(collection_metadata)
        metrics.update(
            {
                "streamed_to_disk": True,
                "resumable_workspace": True,
                "max_in_memory_battle_records": self.metadata_int("max_in_memory_battle_records"),
                "workspace_bytes": self.assert_storage_budget(),
                "exact_matchups_stored": matchup_total,
                "observation_count": self.observation_count,
                "published_matchups": len(deck_matchups),
                "matchups_truncated": max(0, matchup_total - len(deck_matchups)),
            }
        )
        return {
            "cards_meta": cards_meta,
            "top_decks": top_decks,
            "card_deck_stats": card_deck_stats,
            "deck_matchups": deck_matchups,
            "deck_profile_opponents": deck_profile_opponents,
            "raw_battles": raw_records,
            "special_fields_probe": probe_official_special_fields(probe_battles),
            "fetched_at": fetched_at,
            "sample_battles": total_battles,
            "target_battles": target_battles,
            "shortfall_battles": max(target_battles - total_battles, 0),
            "ranked_players": collection_metadata.get("ranked_players", 0),
            "fetched_players": collection_metadata.get("fetched_players", self.processed_players),
            "sampled_players": collection_metadata.get("sampled_players", 0),
            "failed_players": collection_metadata.get("failed_players", 0),
            "usable_battles": total_battles,
            "collection_scope": collection_metadata.get("collection_scope", PATH_OF_LEGEND_COLLECTION_SCOPE),
            "scope_contract": collection_metadata.get("scope_contract", PATH_OF_LEGEND_SCOPE_CONTRACT),
            "scope_verified": bool(collection_metadata.get("scope_verified")),
            "leaderboard_candidate_limit": collection_metadata.get("leaderboard_candidate_limit"),
            "leaderboard_start_rank": collection_metadata.get("leaderboard_start_rank"),
            "leaderboard_last_scanned_rank": collection_metadata.get("leaderboard_last_scanned_rank"),
            "collection_metrics": metrics,
            "_aggregate_store_path": str(self.database_path),
            "_streaming_work_dir": str(self.path),
        }


class SupercellAPIClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 5.0,
        session=requests,
        max_retries: int = 0,
        requests_per_second: float = 0.0,
        sleeper=time.sleep,
    ):
        if not token:
            raise ValueError("SUPERCELL_API_TOKEN is required")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.session = session
        self.max_retries = max(0, max_retries)
        self.requests_per_second = max(0.0, requests_per_second)
        self.sleeper = sleeper
        self.metrics = defaultdict(float)
        self._pacer_lock = threading.Lock()
        self._next_request_at = 0.0
        self._cooldown_until = 0.0

    def _wait_for_request_slot(self) -> None:
        if self.requests_per_second <= 0:
            return
        with self._pacer_lock:
            now = time.monotonic()
            start_at = max(now, self._next_request_at, self._cooldown_until)
            self._next_request_at = start_at + 1.0 / self.requests_per_second
        wait_seconds = max(0.0, start_at - now)
        if wait_seconds:
            self.metrics["throttle_wait_seconds"] += wait_seconds
            self.sleeper(wait_seconds)

    def _apply_cooldown(self, seconds: float) -> None:
        with self._pacer_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + max(0.0, seconds))

    @staticmethod
    def _retry_after_seconds(response, attempt: int) -> float:
        headers = getattr(response, "headers", {}) or {}
        try:
            return max(0.0, float(headers.get("Retry-After", "")))
        except (TypeError, ValueError):
            return float(2**attempt)

    def _get_json(self, path: str, *, params: dict | None = None):
        for attempt in range(self.max_retries + 1):
            try:
                self._wait_for_request_slot()
                self.metrics["request_count"] += 1
                response = self.session.get(
                    f"{SUPERCELL_API_BASE_URL}{path}",
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                    params=params,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise ValueError("official API returned an unsupported JSON payload")
                self.metrics["successful_requests"] += 1
                return payload
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                if getattr(response, "status_code", None) == 429:
                    self.metrics["rate_limited"] += 1
                    delay = self._retry_after_seconds(response, attempt)
                    self._apply_cooldown(delay)
                else:
                    delay = float(2**attempt)
                if attempt >= self.max_retries:
                    self.metrics["failed_requests"] += 1
                    raise
            except requests.RequestException:
                delay = float(2**attempt)
                if attempt >= self.max_retries:
                    self.metrics["failed_requests"] += 1
                    raise
            self.metrics["retried_requests"] += 1
            self.metrics["retry_wait_seconds"] += delay
            self.sleeper(delay)

    def fetch_locations(self) -> list[dict]:
        payload = self._get_json("/locations")
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("official API locations response has no items list")
        return [item for item in items if isinstance(item, dict)]

    def fetch_global_rankings(
        self,
        limit: int,
        *,
        include_locations: bool = False,
        location_limit: int = MAX_RANKING_SEED_LOCATIONS,
    ) -> list[dict]:
        """Return unique Path of Legend player seeds from official leaderboard endpoints."""
        players: list[dict] = []
        seen_tags: set[str] = set()

        def add_from_path(path: str, source: str) -> None:
            if len(players) >= limit:
                return
            try:
                for player in self._fetch_rankings_path(path, limit - len(players)):
                    if _append_unique_player(players, seen_tags, player, source=source) and len(players) >= limit:
                        return
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) == 404:
                    return
                raise

        add_from_path("/locations/global/pathoflegend/players", "global_path_of_legend")
        if not include_locations or len(players) >= limit:
            return players

        try:
            locations = self.fetch_locations()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("official location seed expansion failed: %s", type(exc).__name__)
            return players

        scanned_locations = 0
        for location in locations:
            if len(players) >= limit or scanned_locations >= max(0, int(location_limit)):
                break
            location_id = location.get("id")
            if location_id in (None, "", "global"):
                continue
            location_key = str(location_id).strip()
            if not location_key:
                continue
            scanned_locations += 1
            add_from_path(f"/locations/{quote(location_key, safe='')}/pathoflegend/players", "location_path_of_legend")
        self.metrics["ranking_locations_scanned"] += scanned_locations
        return players

    def _fetch_rankings_path(self, path: str, limit: int) -> list[dict]:
        """Fetch ranking pages without reordering or requesting lower ranks prematurely."""
        players: list[dict] = []
        seen_tags: set[str] = set()
        after: str | None = None
        page_size = min(1000, limit)

        while len(players) < limit:
            params = {"limit": min(page_size, limit - len(players))}
            if after:
                params["after"] = after
            payload = self._get_json(path, params=params)
            self.metrics["ranking_pages"] += 1
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError("official API rankings response has no items list")

            for item in items:
                tag = item.get("tag") if isinstance(item, dict) else None
                normalized_tag = tag.strip().upper() if isinstance(tag, str) else ""
                if not normalized_tag or normalized_tag in seen_tags:
                    continue
                seen_tags.add(normalized_tag)
                ranked_item = dict(item)
                ranked_item.setdefault("rank", len(players) + 1)
                players.append(ranked_item)
                if len(players) >= limit:
                    return players

            paging = payload.get("paging") if isinstance(payload, dict) else None
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            next_after = cursors.get("after") if isinstance(cursors, dict) else None
            if not isinstance(next_after, str) or not next_after.strip() or next_after == after:
                break
            after = next_after
        return players

    def fetch_battle_log(self, player_tag: str) -> list[dict]:
        payload = self._get_json(f"/players/{quote(player_tag, safe='')}/battlelog")
        if not isinstance(payload, list):
            raise ValueError("official API battle log response is not a list")
        return [item for item in payload if isinstance(item, dict)]

    def fetch_snapshot(
        self,
        *,
        target_battles: int = 400,
        player_limit: int = 1000,
        seed_player_limit: int = 1000,
        battles_per_player: int = 25,
        concurrency: int = 8,
        fallback_player_tags: tuple[str, ...] = (),
        battle_log_cache: dict[str, tuple[float, list[dict]]] | None = None,
        battle_log_cache_ttl_seconds: float = 0.0,
        max_duration_seconds: float | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        progress_interval_seconds: float = 60.0,
        spool_dir: Path | None = None,
        collection_mode: str = "weekly_expanded",
        expand_opponents: bool = True,
        strict_battle_contract: bool = False,
        ranked_tail_retry_rounds: int = 0,
        max_workspace_bytes: int | None = None,
        export_raw_battles: bool = True,
    ) -> dict:
        if target_battles < 1:
            raise ValueError("target_battles must be at least 1")
        if player_limit < 1:
            raise ValueError("player_limit must be at least 1")
        if seed_player_limit < 1:
            raise ValueError("seed_player_limit must be at least 1")
        if battles_per_player < 1:
            raise ValueError("battles_per_player must be at least 1")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if collection_mode not in {"daily_ranked", "weekly_expanded"}:
            raise ValueError("collection_mode must be daily_ranked or weekly_expanded")
        if collection_mode == "daily_ranked" and expand_opponents:
            raise ValueError("daily_ranked collection cannot expand opponent tags")

        started_at = time.monotonic()
        self.metrics.clear()
        workspace = (
            DiskBackedSnapshotWorkspace(
                spool_dir,
                target_battles=target_battles,
                player_limit=player_limit,
                battles_per_player=battles_per_player,
                seed_player_limit=seed_player_limit,
                collection_mode=collection_mode,
                max_workspace_bytes=max_workspace_bytes,
            )
            if spool_dir is not None
            else None
        )
        players = workspace.load_players() if workspace is not None else None
        if workspace is not None and players is None and workspace.processed_players:
            workspace.discard()
            workspace = DiskBackedSnapshotWorkspace(
                spool_dir,
                target_battles=target_battles,
                player_limit=player_limit,
                battles_per_player=battles_per_player,
                seed_player_limit=seed_player_limit,
                collection_mode=collection_mode,
                max_workspace_bytes=max_workspace_bytes,
            )
        if players is None:
            players = self.fetch_global_rankings(min(player_limit, seed_player_limit), include_locations=False)
            if not players:
                players = [{"tag": tag} for tag in fallback_player_tags]
            if workspace is not None:
                workspace.save_players(players)
        battle_logs = {} if workspace is None else None
        failed_players = workspace.metadata_int("failed_players") if workspace is not None else 0
        fetched_players = workspace.processed_players if workspace is not None else 0
        sampled_players = workspace.metadata_int("sampled_players") if workspace is not None else 0
        usable_battles = workspace.battle_count if workspace is not None else 0
        seen_battle_ids: set[str] | None = set() if workspace is None else None
        selection_metrics: defaultdict[str, int] = defaultdict(int)
        refresh_budget_exhausted = False
        expanded_players = sum(1 for player in players if player.get("seed_source") == "opponent_battlelog")
        initial_seed_players = max(0, len(players) - expanded_players)
        source_exhausted = False
        queued_player_tags = {
            _normalize_player_tag(player.get("tag"))
            for player in players
            if isinstance(player, dict) and _normalize_player_tag(player.get("tag"))
        }
        last_progress_at = started_at

        def report_progress(*, force: bool = False, final: bool = False) -> None:
            nonlocal last_progress_at
            if progress_callback is None:
                return
            progress_now = time.monotonic()
            if not force and progress_now - last_progress_at < max(0.0, progress_interval_seconds):
                return
            last_progress_at = progress_now
            if refresh_budget_exhausted:
                status = "budget_exhausted"
            elif usable_battles >= target_battles:
                status = "complete"
            elif source_exhausted:
                status = "source_exhausted"
            elif final:
                status = "incomplete"
            else:
                status = "collecting"
            try:
                progress_callback(
                    {
                        "status": status,
                        "target_battles": target_battles,
                        "usable_battles": usable_battles,
                        "fetched_players": fetched_players,
                        "sampled_players": sampled_players,
                        "candidate_players": len(players),
                        "queued_players": len(players),
                        "seed_players": initial_seed_players,
                        "expanded_players": expanded_players,
                        "failed_players": failed_players,
                        "request_count": int(self.metrics["request_count"]),
                        "rate_limited": int(self.metrics["rate_limited"]),
                        "source_exhausted": source_exhausted,
                        "elapsed_seconds": round(progress_now - started_at, 1),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                # Progress reporting must never interrupt official collection.
                logger.warning("snapshot progress callback failed", exc_info=True)

        start = workspace.processed_players if workspace is not None else 0
        while start < len(players):
            if max_duration_seconds is not None and time.monotonic() - started_at >= max_duration_seconds:
                refresh_budget_exhausted = True
                break
            batch = players[start : start + concurrency]
            if not batch:
                break
            batch_positions = {
                _normalize_player_tag(player.get("tag")): start + offset
                for offset, player in enumerate(batch)
                if isinstance(player, dict)
            }
            cached = []
            to_fetch = []
            cache_now = time.monotonic()
            for player in batch:
                tag = player["tag"]
                cache_item = (battle_log_cache or {}).get(tag)
                if cache_item and cache_now - cache_item[0] < battle_log_cache_ttl_seconds:
                    cached.append((player, cache_item[1], None))
                    self.metrics["cache_hits"] += 1
                else:
                    to_fetch.append(player)

            if not to_fetch:
                fetched = cached
            elif concurrency == 1:
                fetched = list(cached)
                for player in to_fetch:
                    try:
                        fetched.append((player, self.fetch_battle_log(player["tag"]), None))
                    except (requests.RequestException, ValueError) as exc:
                        fetched.append((player, [], exc))
            else:
                with ThreadPoolExecutor(max_workers=min(concurrency, len(to_fetch))) as executor:
                    futures = [(player, executor.submit(self.fetch_battle_log, player["tag"])) for player in to_fetch]
                    fetched = list(cached)
                    for player, future in futures:
                        try:
                            fetched.append((player, future.result(), None))
                        except (requests.RequestException, ValueError) as exc:
                            fetched.append((player, [], exc))

            for player, battles, error in fetched:
                fetched_players += 1
                if error is not None:
                    failed_players += 1
                    if workspace is not None:
                        player_index = batch_positions.get(_normalize_player_tag(player.get("tag")), start)
                        is_expanded = player.get("seed_source") == "opponent_battlelog"
                        workspace.record_player(
                            player_index=player_index,
                            player_tag=str(player.get("tag") or ""),
                            battles=[],
                            failed=True,
                            target_battles=target_battles,
                            observer_rank=None if is_expanded else _official_player_rank(player),
                            observer_source="opponent_expansion" if is_expanded else "ranked_direct",
                            expansion_root_rank=player.get("expansion_root_rank"),
                        )
                    continue
                selection_metrics["raw_battle_records"] += len(battles)
                # Recent battle-log entries can be deckless event records. Filter first so
                # a short prefix does not discard an otherwise usable player sample.
                selected = select_usable_battles(
                    battles,
                    battles_per_player,
                    seen_battle_ids=seen_battle_ids,
                    observer_tag=player.get("tag"),
                    selection_metrics=selection_metrics,
                    path_of_legend_only=True,
                    require_complete_decks_and_stable_id=strict_battle_contract,
                )
                if workspace is not None:
                    player_index = batch_positions.get(_normalize_player_tag(player.get("tag")), start)
                    is_expanded = player.get("seed_source") == "opponent_battlelog"
                    accepted = workspace.record_player(
                        player_index=player_index,
                        player_tag=str(player.get("tag") or ""),
                        battles=selected,
                        failed=error is not None,
                        target_battles=target_battles,
                        observer_rank=None if is_expanded else _official_player_rank(player),
                        observer_source="opponent_expansion" if is_expanded else "ranked_direct",
                        expansion_root_rank=player.get("expansion_root_rank"),
                    )
                    usable_battles += accepted
                    sampled_players += int(accepted > 0)
                elif selected:
                    if battle_log_cache is not None:
                        battle_log_cache[player["tag"]] = (time.monotonic(), battles)
                    sampled_players += 1
                    usable_battles += len(selected)
                    battle_logs[player["tag"]] = selected
                if (
                    expand_opponents
                    and selected
                    and player.get("seed_source") != "opponent_battlelog"
                    and len(players) < player_limit
                ):
                    added = 0
                    if player.get("seed_source") == "opponent_battlelog":
                        expansion_root_rank = player.get("expansion_root_rank")
                    else:
                        expansion_root_rank = _official_player_rank(player)
                    for opponent_tag in opponent_tags_from_battles(selected, observer_tag=player.get("tag")):
                        if len(players) >= player_limit:
                            break
                        if opponent_tag in queued_player_tags:
                            continue
                        queued_player_tags.add(opponent_tag)
                        players.append(
                            {
                                "tag": opponent_tag,
                                "seed_source": "opponent_battlelog",
                                "expansion_root_rank": expansion_root_rank,
                            }
                        )
                        added += 1
                    if added:
                        expanded_players += added
                        if workspace is not None:
                            workspace.save_players(players)

            report_progress()

            if self.metrics["rate_limited"]:
                if workspace is not None:
                    workspace.mark_rate_limited(int(self.metrics["rate_limited"]))
                break

            if usable_battles >= target_battles:
                break
            start += len(batch)

        if workspace is not None and not self.metrics["rate_limited"]:
            for _round in range(max(0, int(ranked_tail_retry_rounds))):
                failed_ranked = workspace.failed_ranked_players()
                if not failed_ranked:
                    break
                for player in failed_ranked:
                    if max_duration_seconds is not None and time.monotonic() - started_at >= max_duration_seconds:
                        refresh_budget_exhausted = True
                        break
                    try:
                        battles = self.fetch_battle_log(player["tag"])
                    except (requests.RequestException, ValueError):
                        if self.metrics["rate_limited"]:
                            workspace.mark_rate_limited(int(self.metrics["rate_limited"]))
                            break
                        continue
                    selection_metrics["raw_battle_records"] += len(battles)
                    selected = select_usable_battles(
                        battles,
                        battles_per_player,
                        observer_tag=player["tag"],
                        selection_metrics=selection_metrics,
                        path_of_legend_only=True,
                        require_complete_decks_and_stable_id=strict_battle_contract,
                    )
                    accepted = workspace.record_player(
                        player_index=max(0, int(player.get("rank") or 1) - 1),
                        player_tag=player["tag"],
                        battles=selected,
                        failed=False,
                        target_battles=target_battles,
                        observer_rank=player.get("rank"),
                        observer_source="ranked_direct",
                    )
                    usable_battles += accepted
                    sampled_players += int(accepted > 0)
                    fetched_players += 1
                    if self.metrics["rate_limited"]:
                        workspace.mark_rate_limited(int(self.metrics["rate_limited"]))
                        break
                if refresh_budget_exhausted or self.metrics["rate_limited"]:
                    break
            failed_players = workspace.failed_player_count
            report_progress(force=True)

        source_exhausted = (
            usable_battles < target_battles
            and not refresh_budget_exhausted
            and not self.metrics["rate_limited"]
            and start >= len(players)
        )

        report_progress(force=True, final=True)

        collection_metadata = {
                "ranked_players": initial_seed_players,
                "fetched_players": fetched_players,
                "sampled_players": sampled_players,
                "failed_players": failed_players,
                "usable_battles": usable_battles,
                "collection_mode": collection_mode,
                "expand_opponents": bool(expand_opponents),
                "collection_scope": PATH_OF_LEGEND_COLLECTION_SCOPE,
                "scope_contract": PATH_OF_LEGEND_SCOPE_CONTRACT,
                "scope_verified": bool(strict_battle_contract),
                "seed_player_limit": min(player_limit, seed_player_limit),
                "seed_players": initial_seed_players,
                "queued_players": len(players),
                "expanded_players": expanded_players,
                "source_exhausted": source_exhausted,
                "leaderboard_candidate_limit": min(player_limit, seed_player_limit),
                "player_queue_capacity": player_limit,
                "leaderboard_start_rank": _ranking_position(players, 0),
                "leaderboard_last_scanned_rank": _ranking_position(
                    players, min(fetched_players, initial_seed_players) - 1
                ),
                "raw_battle_records": int(selection_metrics["raw_battle_records"]),
                "inspected_battle_records": int(selection_metrics["inspected_battle_records"]),
                "duplicates_skipped": int(selection_metrics["duplicates_skipped"]),
                "deckless_or_invalid_records": int(selection_metrics["deckless_or_invalid_records"]),
                "non_path_of_legend_records": int(selection_metrics["non_path_of_legend_records"]),
                "collection_duration_seconds": round(time.monotonic() - started_at, 3),
                "refresh_budget_exhausted": refresh_budget_exhausted,
                "request_count": int(self.metrics["request_count"]),
                "successful_requests": int(self.metrics["successful_requests"]),
                "failed_requests": int(self.metrics["failed_requests"]),
                "rate_limited": int(self.metrics["rate_limited"]),
                "retried_requests": int(self.metrics["retried_requests"]),
                "cache_hits": int(self.metrics["cache_hits"]),
                "throttle_wait_seconds": round(self.metrics["throttle_wait_seconds"], 3),
                "retry_wait_seconds": round(self.metrics["retry_wait_seconds"], 3),
                "ranking_pages": int(self.metrics["ranking_pages"]),
            }
        if workspace is not None:
            try:
                return workspace.build_snapshot(
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    target_battles=target_battles,
                    collection_metadata=collection_metadata,
                    export_raw_battles=export_raw_battles,
                )
            finally:
                workspace.close()
        return build_live_snapshot(
            players,
            battle_logs or {},
            target_battles=target_battles,
            collection_metadata=collection_metadata,
        )


def _ranking_position(players: list[dict], index: int) -> int | None:
    if index < 0 or index >= len(players):
        return None
    rank = players[index].get("rank") if isinstance(players[index], dict) else None
    try:
        return int(rank)
    except (TypeError, ValueError):
        return index + 1


def _official_player_rank(player: object) -> int | None:
    if not isinstance(player, dict):
        return None
    try:
        rank = int(player.get("rank"))
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def _team_member(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _team_cards(battle: dict) -> list[dict]:
    return _side_cards(_team_member(battle.get("team")))


def _opponent_cards(battle: dict) -> list[dict]:
    return _side_cards(_team_member(battle.get("opponent")))


def _side_cards(member: dict | None) -> list[dict]:
    team = member
    if team is None:
        return []

    cards = team.get("cards")
    if not isinstance(cards, list):
        cards = team.get("deck")
    if not isinstance(cards, list):
        return []

    normalized = []
    for card in cards:
        if isinstance(card, dict) and isinstance(card.get("name"), str):
            normalized.append(card)
        elif isinstance(card, str) and card.strip():
            normalized.append({"name": card.strip()})
    return normalized


def _deck_signature(cards: list[dict]) -> tuple[str, ...]:
    return tuple(sorted(str(card["name"]).strip() for card in cards if str(card.get("name", "")).strip()))


def _side_tag(member: dict | None) -> str | None:
    value = member.get("tag") if isinstance(member, dict) else None
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _crowns(member: dict | None) -> int:
    try:
        return int((member or {}).get("crowns", 0) or 0)
    except (TypeError, ValueError):
        return 0


def normalize_battle_record(battle: dict, observer_tag: str | None = None) -> dict | None:
    """Create a source-preserving, order-independent record for one battle."""
    if not isinstance(battle, dict):
        return None
    team = _team_member(battle.get("team"))
    opponent = _team_member(battle.get("opponent"))
    team_cards = _team_cards(battle)
    if not team_cards:
        return None
    opponent_cards = _opponent_cards(battle)
    team_deck = _deck_signature(team_cards)
    opponent_deck = _deck_signature(opponent_cards)
    team_tag = _side_tag(team) or (observer_tag.strip().upper() if isinstance(observer_tag, str) and observer_tag.strip() else None)
    opponent_tag = _side_tag(opponent)
    timestamp = str(battle.get("battleTime") or battle.get("battle_time") or "")
    team_loadout = normalize_side_loadout(team)
    opponent_loadout = normalize_side_loadout(opponent)

    # A player can appear in the global ranking alongside their opponent. The
    # same battle then appears twice with sides reversed, so the fingerprint is
    # deliberately independent of the observer's side.
    sides = sorted(
        (
            (team_tag or "", team_deck, _crowns(team)),
            (opponent_tag or "", opponent_deck, _crowns(opponent)),
        ),
        key=repr,
    )
    # battleTime is required for cross-player deduplication. If it is absent,
    # the record remains usable but is deliberately not globally deduplicated:
    # identical decks and crowns alone do not prove it is the same battle.
    battle_id = None
    if timestamp:
        fingerprint = repr((timestamp, sides)).encode("utf-8")
        battle_id = hashlib.sha256(fingerprint).hexdigest()[:24]
    return {
        "battle_id": battle_id,
        "battle_type": battle.get("type"),
        "battle_time": timestamp or None,
        "team_tag": team_tag,
        "opponent_tag": opponent_tag,
        "team_deck": list(team_deck),
        "opponent_deck": list(opponent_deck),
        "loadout_schema_version": LOADOUT_SCHEMA_VERSION,
        "team_loadout": team_loadout,
        "opponent_loadout": opponent_loadout,
        "team_crowns": _crowns(team),
        "opponent_crowns": _crowns(opponent),
        "won": _crowns(team) > _crowns(opponent),
    }


def opponent_tags_from_battles(battles: list[dict], *, observer_tag: str | None = None) -> list[str]:
    """Return unique opponent tags observed in selected battle-log records."""
    observer = _normalize_player_tag(observer_tag)
    tags: list[str] = []
    seen: set[str] = set()
    for battle in battles:
        if not is_path_of_legend_battle(battle):
            continue
        record = normalize_battle_record(battle, observer)
        if not record:
            continue
        for tag in (record.get("opponent_tag"), record.get("team_tag")):
            normalized = _normalize_player_tag(tag)
            if not normalized or normalized == observer or normalized in seen:
                continue
            seen.add(normalized)
            tags.append(normalized)
    return tags


def select_usable_battles(
    battles: list[dict],
    limit: int,
    *,
    seen_battle_ids: set[str] | None = None,
    observer_tag: str | None = None,
    selection_metrics: dict[str, int] | None = None,
    path_of_legend_only: bool = False,
    require_complete_decks_and_stable_id: bool = False,
) -> list[dict]:
    """Keep bounded, unique entries that satisfy the requested battle scope."""
    usable = []
    seen = seen_battle_ids if seen_battle_ids is not None else set()
    for battle in battles:
        if selection_metrics is not None:
            selection_metrics["inspected_battle_records"] = selection_metrics.get("inspected_battle_records", 0) + 1
        if path_of_legend_only and not is_path_of_legend_battle(battle):
            if selection_metrics is not None:
                selection_metrics["non_path_of_legend_records"] = (
                    selection_metrics.get("non_path_of_legend_records", 0) + 1
                )
            continue
        record = normalize_battle_record(battle, observer_tag)
        if record is None:
            if selection_metrics is not None:
                selection_metrics["deckless_or_invalid_records"] = selection_metrics.get("deckless_or_invalid_records", 0) + 1
            continue
        if require_complete_decks_and_stable_id and (
            not record.get("battle_id")
            or not record.get("battle_time")
            or len(record.get("team_deck") or ()) != 8
            or len(record.get("opponent_deck") or ()) != 8
        ):
            if selection_metrics is not None:
                selection_metrics["deckless_or_invalid_records"] = (
                    selection_metrics.get("deckless_or_invalid_records", 0) + 1
                )
            continue
        battle_id = record["battle_id"]
        if battle_id is not None and battle_id in seen:
            if selection_metrics is not None:
                selection_metrics["duplicates_skipped"] = selection_metrics.get("duplicates_skipped", 0) + 1
            continue
        if battle_id is not None:
            seen.add(battle_id)
        usable.append(battle)
        if len(usable) >= limit:
            break
    return usable


def _is_win(battle: dict) -> bool:
    team = _team_member(battle.get("team"))
    opponent = _team_member(battle.get("opponent"))
    if team is None or opponent is None:
        return False
    try:
        return int(team.get("crowns", 0) or 0) > int(opponent.get("crowns", 0) or 0)
    except (TypeError, ValueError):
        return False


def probe_official_special_fields(battles: list[dict]) -> dict:
    """Report special-deck fields actually present in official battle payloads.

    The probe records field names and deterministic normalization coverage.
    Elite state uses explicit official fields when present, otherwise the
    versioned level-above-max rule used by the stored loadout contract.
    """
    tower_fields: Counter[str] = Counter()
    evolution_fields: Counter[str] = Counter()
    elite_fields: Counter[str] = Counter()
    side_records_checked = 0
    card_records_checked = 0
    complete_loadout_sides = 0

    def observe_field(field_name: object) -> None:
        name = str(field_name)
        lowered = name.lower()
        if "tower" in lowered or "supportcard" in lowered:
            tower_fields[name] += 1
        if "evolution" in lowered:
            evolution_fields[name] += 1
        if "elite" in lowered:
            elite_fields[name] += 1

    for battle in battles:
        if not isinstance(battle, dict):
            continue
        for side_name in ("team", "opponent"):
            member = _team_member(battle.get(side_name))
            if member is None:
                continue
            side_records_checked += 1
            complete_loadout_sides += int(normalize_side_loadout(member)["complete"])
            for field_name in member:
                observe_field(field_name)
            cards = member.get("cards")
            if not isinstance(cards, list):
                cards = member.get("deck")
            for card in cards if isinstance(cards, list) else []:
                if not isinstance(card, dict):
                    continue
                card_records_checked += 1
                for field_name in card:
                    observe_field(field_name)

    def result(counter: Counter[str]) -> dict:
        return {
            "available": bool(counter),
            "observed_fields": dict(sorted(counter.items())),
        }

    return {
        "schema_version": 1,
        "deck_mode": "base8_and_full_loadout_v1",
        "available_deck_modes": ["base8", "full_loadout"],
        "battle_records_checked": sum(1 for battle in battles if isinstance(battle, dict)),
        "side_records_checked": side_records_checked,
        "card_records_checked": card_records_checked,
        "complete_loadout_sides": complete_loadout_sides,
        "tower": result(tower_fields),
        "evolution": result(evolution_fields),
        "elite": result(elite_fields),
    }


def build_live_snapshot(
    players: list[dict],
    battle_logs: dict[str, list[dict]],
    *,
    fetched_at: str | None = None,
    target_battles: int | None = None,
    collection_metadata: dict | None = None,
) -> dict:
    """Derive labelled sample metrics from public leaderboard battle logs."""
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    card_usage: Counter[str] = Counter()
    card_wins: Counter[str] = Counter()
    deck_usage: Counter[tuple[str, ...]] = Counter()
    deck_wins: Counter[tuple[str, ...]] = Counter()
    deck_elixir: dict[tuple[str, ...], list[float]] = defaultdict(list)
    matchup_games: Counter[tuple[tuple[str, ...], tuple[str, ...]]] = Counter()
    matchup_wins: Counter[tuple[tuple[str, ...], tuple[str, ...]]] = Counter()
    raw_battles: list[dict] = []
    seen_battle_ids: set[str] = set()
    total_battles = 0
    battle_records = 0
    deck_records = 0
    reached_target = False
    probe_battles: list[dict] = []

    for player in players:
        tag = player.get("tag")
        for battle in battle_logs.get(tag, []):
            battle_records += 1
            record = normalize_battle_record(battle, tag)
            battle_id = record.get("battle_id") if record else None
            if record is None or (battle_id is not None and battle_id in seen_battle_ids):
                continue
            if battle_id is not None:
                seen_battle_ids.add(battle_id)
            cards = _team_cards(battle)
            deck_records += 1
            total_battles += 1
            probe_battles.append(battle)
            raw_battles.append(record)
            names = tuple(record["team_deck"])
            won = bool(record["won"])
            deck_usage[names] += 1
            deck_wins[names] += int(won)
            costs = [float(card["elixirCost"]) for card in cards if isinstance(card.get("elixirCost"), (int, float))]
            if costs:
                deck_elixir[names].append(sum(costs) / len(costs))
            for card_name in names:
                card_usage[card_name] += 1
                card_wins[card_name] += int(won)
            opponent_deck = tuple(record["opponent_deck"])
            if opponent_deck:
                matchup_key = (names, opponent_deck)
                matchup_games[matchup_key] += 1
                matchup_wins[matchup_key] += int(won)
            if target_battles is not None and total_battles >= target_battles:
                reached_target = True
                break
        if reached_target:
            break

    cards_meta = []
    for rank, (card_name, usage) in enumerate(card_usage.most_common(), start=1):
        wins = card_wins[card_name]
        cards_meta.append(
            {
                "rank": rank,
                "card_name": card_name,
                "rating": 0,
                "usage_rate": round(usage / total_battles * 100, 1) if total_battles else 0.0,
                "usage_delta": 0.0,
                "win_rate": round(wins / usage * 100, 1) if usage else 0.0,
                "win_delta": 0.0,
                "clean_win_rate": round(wins / usage * 100, 1) if usage else 0.0,
                "mode": "Official Path of Legend battle-log sample",
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": total_battles,
                "target_battles": target_battles or total_battles,
                "appearance_count": usage,
            }
        )

    top_decks = []
    for rank, (deck, battles) in enumerate(deck_usage.most_common(30), start=1):
        top_decks.append(
            {
                "rank": rank,
                "player_name": "Global Path of Legend sample",
                "clan_name": "Official Supercell API",
                "deck_name": " / ".join(deck),
                "avg_elixir": round(sum(deck_elixir[deck]) / len(deck_elixir[deck]), 1) if deck_elixir[deck] else None,
                "battles": battles,
                "trophies": None,
                "last_ladder_battle": fetched_at,
                "cards": list(deck),
                "sample_win_rate": round(deck_wins[deck] / battles * 100, 1) if battles else 0.0,
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": total_battles,
                "target_battles": target_battles or total_battles,
            }
        )

    deck_matchups = []
    for (deck, opponent_deck), games in sorted(matchup_games.items(), key=lambda item: item[1], reverse=True):
        wins = matchup_wins[(deck, opponent_deck)]
        deck_matchups.append(
            {
                "deck_name": " / ".join(deck),
                "opponent_deck_name": " / ".join(opponent_deck),
                "games": games,
                "wins": wins,
                "win_rate": round(wins / games * 100, 1) if games else 0.0,
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": total_battles,
                "target_battles": target_battles or total_battles,
            }
        )

    if not total_battles:
        raise ValueError(
            "official API returned no usable battle-log decks "
            f"(players={len(players)}, battle_records={battle_records}, deck_records={deck_records})"
        )
    collection_metadata = collection_metadata or {}
    target = target_battles or total_battles
    card_deck_stats = build_card_deck_stats(
        raw_battles,
        fetched_at=fetched_at,
        sample_battles=total_battles,
        target_battles=target,
    )
    return {
        "cards_meta": cards_meta,
        "top_decks": top_decks,
        "card_deck_stats": card_deck_stats,
        "deck_matchups": deck_matchups,
        "raw_battles": raw_battles,
        "special_fields_probe": probe_official_special_fields(probe_battles),
        "fetched_at": fetched_at,
        "sample_battles": total_battles,
        "target_battles": target,
        "shortfall_battles": max(target - total_battles, 0),
        "ranked_players": collection_metadata.get("ranked_players", len(players)),
        "fetched_players": collection_metadata.get("fetched_players", len(battle_logs)),
        "sampled_players": collection_metadata.get("sampled_players", len([items for items in battle_logs.values() if items])),
        "failed_players": collection_metadata.get("failed_players", 0),
        "usable_battles": collection_metadata.get("usable_battles", total_battles),
        "collection_scope": collection_metadata.get("collection_scope"),
        "scope_contract": collection_metadata.get("scope_contract"),
        "scope_verified": bool(collection_metadata.get("scope_verified")),
        "leaderboard_candidate_limit": collection_metadata.get("leaderboard_candidate_limit"),
        "leaderboard_start_rank": collection_metadata.get("leaderboard_start_rank"),
        "leaderboard_last_scanned_rank": collection_metadata.get("leaderboard_last_scanned_rank"),
        "collection_metrics": {
            key: value
            for key, value in collection_metadata.items()
            if key
            not in {
                "ranked_players",
                "fetched_players",
                "sampled_players",
                "failed_players",
                "usable_battles",
                "collection_scope",
                "scope_contract",
                "leaderboard_candidate_limit",
                "leaderboard_start_rank",
                "leaderboard_last_scanned_rank",
            }
        },
    }


def build_card_deck_stats(
    raw_battles: list[dict],
    *,
    fetched_at: str,
    sample_battles: int,
    target_battles: int,
    variants_per_card: int = CARD_DECK_VARIANTS_PER_CARD,
) -> dict[str, list[dict]]:
    """Aggregate the most observed exact decks containing each card.

    ``top_decks`` deliberately keeps only the global top 30 exact decks. That
    cannot answer a card-filtered question when a card has many viable build
    variants, so this index is derived from every normalized battle in the same
    official snapshot.
    """
    deck_usage: Counter[tuple[str, ...]] = Counter()
    deck_wins: Counter[tuple[str, ...]] = Counter()
    decks_by_card: defaultdict[str, set[tuple[str, ...]]] = defaultdict(set)

    for record in raw_battles:
        if not isinstance(record, dict):
            continue
        deck = tuple(str(card).strip() for card in record.get("team_deck", []) if isinstance(card, str) and card.strip())
        if not deck:
            continue
        deck_usage[deck] += 1
        deck_wins[deck] += int(bool(record.get("won")))
        for card_name in deck:
            decks_by_card[card_name].add(deck)

    result: dict[str, list[dict]] = {}
    for card_name, decks in decks_by_card.items():
        ranked = sorted(decks, key=lambda deck: (-deck_usage[deck], deck))[:variants_per_card]
        result[card_name] = [
            {
                "deck_name": " / ".join(deck),
                "cards": list(deck),
                "battles": deck_usage[deck],
                "sample_win_rate": round(deck_wins[deck] / deck_usage[deck] * 100, 1),
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": sample_battles,
                "target_battles": target_battles,
            }
            for deck in ranked
        ]
    return result
