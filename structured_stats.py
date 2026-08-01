"""Build deterministic two-sided statistics from a published snapshot archive."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from battle_loadout import canonical_loadout, full_loadout_signature, loadout_payload
from deck_archetypes import CLASSIFIER_VERSION, archetype_family, classify_deck


SCHEMA_VERSION = 5
RATING_FORMULA_VERSION = "wilson65_usage20_confidence15_v1"
_SAFE_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class StructuredStatsError(ValueError):
    """Raised when a trustworthy structured index cannot be built."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StructuredStatsError(f"cannot read JSON file: {path.name}") from exc
    if not isinstance(value, dict):
        raise StructuredStatsError(f"JSON file must contain an object: {path.name}")
    return value


def _snapshot_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _SAFE_SNAPSHOT_ID.fullmatch(normalized):
        raise StructuredStatsError("invalid snapshot_id")
    return normalized


def _publish_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        backup = destination.with_name(f".{destination.name}.previous-{time.time_ns()}")
        os.replace(destination, backup)
        try:
            os.replace(source, destination)
        except Exception:
            os.replace(backup, destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 5 or destination.exists():
                raise
            time.sleep(0.05 * (attempt + 1))


def _deck(cards: object) -> tuple[str, ...]:
    if not isinstance(cards, list) or len(cards) != 8:
        return ()
    normalized = tuple(sorted(str(card).strip() for card in cards if isinstance(card, str) and card.strip()))
    if len(normalized) != 8 or len(set(normalized)) != 8:
        return ()
    return normalized


def _signature(deck: tuple[str, ...]) -> str:
    return json.dumps(deck, ensure_ascii=False, separators=(",", ":"))


def _result(crowns: int, opponent_crowns: int) -> tuple[int, int, int]:
    if crowns > opponent_crowns:
        return 1, 0, 0
    if crowns < opponent_crowns:
        return 0, 1, 0
    return 0, 0, 1


def _wilson_lower_bound(wins: int, losses: int, z: float = 1.96) -> float:
    decisions = wins + losses
    if decisions <= 0:
        return 0.0
    probability = wins / decisions
    z_squared = z * z
    denominator = 1 + z_squared / decisions
    centre = probability + z_squared / (2 * decisions)
    margin = z * math.sqrt(
        (probability * (1 - probability) + z_squared / (4 * decisions)) / decisions
    )
    return max(0.0, (centre - margin) / denominator)


def _increment(counter: dict, key: object, result: tuple[int, int, int], games: int = 1) -> None:
    values = counter[key]
    values[0] += games
    values[1] += result[0]
    values[2] += result[1]
    values[3] += result[2]


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE card_stats(
            card_name TEXT PRIMARY KEY,
            appearances INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            usage_rate REAL NOT NULL,
            clean_win_rate REAL NOT NULL,
            net_win_rate REAL NOT NULL,
            wilson_lower_bound REAL NOT NULL,
            usage_percentile REAL NOT NULL,
            sample_confidence REAL NOT NULL,
            rating REAL NOT NULL
        );
        CREATE TABLE card_teammates(
            card_name TEXT NOT NULL,
            teammate_name TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            PRIMARY KEY(card_name, teammate_name)
        );
        CREATE TABLE card_opponents(
            card_name TEXT NOT NULL,
            opponent_name TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            PRIMARY KEY(card_name, opponent_name)
        );
        CREATE TABLE deck_stats(
            deck_signature TEXT PRIMARY KEY,
            deck_json TEXT NOT NULL,
            archetype TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            crowns INTEGER NOT NULL,
            usage_rate REAL NOT NULL DEFAULT 0,
            clean_win_rate REAL NOT NULL DEFAULT 0,
            net_win_rate REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE matchup_stats(
            deck_a_signature TEXT NOT NULL,
            deck_b_signature TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins_a INTEGER NOT NULL,
            wins_b INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            crowns_a INTEGER NOT NULL,
            crowns_b INTEGER NOT NULL,
            latest_battle_time TEXT,
            PRIMARY KEY(deck_a_signature, deck_b_signature)
        );
        CREATE TABLE full_loadout_stats(
            loadout_signature TEXT PRIMARY KEY,
            loadout_json TEXT NOT NULL,
            base_deck_signature TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            crowns INTEGER NOT NULL,
            usage_rate REAL NOT NULL DEFAULT 0,
            clean_win_rate REAL NOT NULL DEFAULT 0,
            net_win_rate REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE full_loadout_matchup_stats(
            loadout_a_signature TEXT NOT NULL,
            loadout_b_signature TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins_a INTEGER NOT NULL,
            wins_b INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            crowns_a INTEGER NOT NULL,
            crowns_b INTEGER NOT NULL,
            latest_battle_time TEXT,
            PRIMARY KEY(loadout_a_signature, loadout_b_signature)
        );
        CREATE TABLE tower_stats(
            tower_id TEXT PRIMARY KEY,
            tower_json TEXT NOT NULL,
            appearances INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            usage_rate REAL NOT NULL DEFAULT 0,
            clean_win_rate REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE evolution_stats(
            card_id TEXT NOT NULL,
            card_name TEXT NOT NULL,
            evolution_level INTEGER NOT NULL,
            appearances INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            usage_rate REAL NOT NULL DEFAULT 0,
            clean_win_rate REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(card_id, evolution_level)
        );
        CREATE TABLE elite_stats(
            card_id TEXT PRIMARY KEY,
            card_name TEXT NOT NULL,
            appearances INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            usage_rate REAL NOT NULL DEFAULT 0,
            clean_win_rate REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE loadout_card_catalog(
            card_id TEXT PRIMARY KEY,
            card_name TEXT NOT NULL,
            appearances INTEGER NOT NULL,
            evolution_appearances INTEGER NOT NULL,
            elite_appearances INTEGER NOT NULL
        );
        CREATE TABLE loadout_entity_stats(
            entity_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            card_id TEXT,
            card_name TEXT,
            tower_id TEXT,
            entity_json TEXT NOT NULL,
            special_state TEXT NOT NULL,
            appearances INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            usage_rate REAL NOT NULL,
            clean_win_rate REAL NOT NULL,
            net_win_rate REAL NOT NULL,
            wilson_lower_bound REAL NOT NULL,
            usage_percentile REAL NOT NULL,
            sample_confidence REAL NOT NULL,
            rating REAL NOT NULL
        );
        CREATE TABLE archetype_stats(
            archetype TEXT PRIMARY KEY,
            games INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            usage_rate REAL NOT NULL,
            clean_win_rate REAL NOT NULL,
            net_win_rate REAL NOT NULL,
            classification TEXT NOT NULL,
            confidence_note TEXT NOT NULL
        );
        CREATE TABLE archetype_matchups(
            archetype TEXT NOT NULL,
            opponent_archetype TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            PRIMARY KEY(archetype, opponent_archetype)
        );
        CREATE TABLE archetype_decks(
            archetype TEXT NOT NULL,
            deck_signature TEXT NOT NULL,
            games INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            draws INTEGER NOT NULL,
            PRIMARY KEY(archetype, deck_signature)
        );
        CREATE INDEX idx_card_teammates_games ON card_teammates(card_name, games DESC);
        CREATE INDEX idx_card_opponents_games ON card_opponents(card_name, games DESC);
        CREATE INDEX idx_deck_stats_games ON deck_stats(games DESC);
        CREATE INDEX idx_matchup_deck_b ON matchup_stats(deck_b_signature, games DESC);
        CREATE INDEX idx_full_loadout_games ON full_loadout_stats(games DESC);
        CREATE INDEX idx_full_matchup_b ON full_loadout_matchup_stats(loadout_b_signature, games DESC);
        CREATE INDEX idx_tower_appearances ON tower_stats(appearances DESC);
        CREATE INDEX idx_evolution_appearances ON evolution_stats(appearances DESC);
        CREATE INDEX idx_elite_appearances ON elite_stats(appearances DESC);
        CREATE INDEX idx_loadout_card_name ON loadout_card_catalog(card_name);
        CREATE INDEX idx_loadout_entity_usage ON loadout_entity_stats(usage_rate DESC);
        CREATE INDEX idx_loadout_entity_rating ON loadout_entity_stats(rating DESC);
        CREATE INDEX idx_archetype_stats_games ON archetype_stats(games DESC);
        CREATE INDEX idx_archetype_decks_games ON archetype_decks(archetype, games DESC);
        """
    )


def _upsert_deck(
    connection: sqlite3.Connection,
    signature: str,
    deck: tuple[str, ...],
    archetype: str,
    result: tuple[int, int, int],
    crowns: int,
) -> None:
    connection.execute(
        """
        INSERT INTO deck_stats(
            deck_signature, deck_json, archetype, games, wins, losses, draws, crowns
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(deck_signature) DO UPDATE SET
            games=games+1, wins=wins+excluded.wins, losses=losses+excluded.losses,
            draws=draws+excluded.draws, crowns=crowns+excluded.crowns
        """,
        (signature, json.dumps(deck, ensure_ascii=False), archetype, *result, crowns),
    )
    connection.execute(
        """
        INSERT INTO archetype_decks(archetype, deck_signature, games, wins, losses, draws)
        VALUES (?, ?, 1, ?, ?, ?)
        ON CONFLICT(archetype, deck_signature) DO UPDATE SET
            games=games+1, wins=wins+excluded.wins, losses=losses+excluded.losses,
            draws=draws+excluded.draws
        """,
        (archetype, signature, *result),
    )


def _upsert_matchup(
    connection: sqlite3.Connection,
    team_signature: str,
    opponent_signature: str,
    team_result: tuple[int, int, int],
    team_crowns: int,
    opponent_crowns: int,
    battle_time: object,
) -> None:
    if team_signature <= opponent_signature:
        deck_a, deck_b = team_signature, opponent_signature
        wins_a, wins_b = team_result[0], team_result[1]
        crowns_a, crowns_b = team_crowns, opponent_crowns
    else:
        deck_a, deck_b = opponent_signature, team_signature
        wins_a, wins_b = team_result[1], team_result[0]
        crowns_a, crowns_b = opponent_crowns, team_crowns
    latest = str(battle_time).strip() if battle_time else None
    connection.execute(
        """
        INSERT INTO matchup_stats(
            deck_a_signature, deck_b_signature, games, wins_a, wins_b, draws,
            crowns_a, crowns_b, latest_battle_time
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(deck_a_signature, deck_b_signature) DO UPDATE SET
            games=games+1, wins_a=wins_a+excluded.wins_a, wins_b=wins_b+excluded.wins_b,
            draws=draws+excluded.draws, crowns_a=crowns_a+excluded.crowns_a,
            crowns_b=crowns_b+excluded.crowns_b,
            latest_battle_time=CASE
                WHEN excluded.latest_battle_time IS NULL THEN latest_battle_time
                WHEN latest_battle_time IS NULL OR excluded.latest_battle_time > latest_battle_time
                THEN excluded.latest_battle_time ELSE latest_battle_time END
        """,
        (deck_a, deck_b, wins_a, wins_b, team_result[2], crowns_a, crowns_b, latest),
    )


def _upsert_full_loadout(
    connection: sqlite3.Connection,
    signature: str,
    loadout: dict,
    base_deck_signature: str,
    result: tuple[int, int, int],
    crowns: int,
) -> None:
    connection.execute(
        """
        INSERT INTO full_loadout_stats(
            loadout_signature, loadout_json, base_deck_signature,
            games, wins, losses, draws, crowns
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(loadout_signature) DO UPDATE SET
            games=games+1, wins=wins+excluded.wins, losses=losses+excluded.losses,
            draws=draws+excluded.draws, crowns=crowns+excluded.crowns
        """,
        (signature, loadout_payload(loadout), base_deck_signature, *result, crowns),
    )


def _upsert_full_matchup(
    connection: sqlite3.Connection,
    team_signature: str,
    opponent_signature: str,
    team_result: tuple[int, int, int],
    team_crowns: int,
    opponent_crowns: int,
    battle_time: object,
) -> None:
    if team_signature <= opponent_signature:
        loadout_a, loadout_b = team_signature, opponent_signature
        wins_a, wins_b = team_result[0], team_result[1]
        crowns_a, crowns_b = team_crowns, opponent_crowns
    else:
        loadout_a, loadout_b = opponent_signature, team_signature
        wins_a, wins_b = team_result[1], team_result[0]
        crowns_a, crowns_b = opponent_crowns, team_crowns
    latest = str(battle_time).strip() if battle_time else None
    connection.execute(
        """
        INSERT INTO full_loadout_matchup_stats(
            loadout_a_signature, loadout_b_signature, games, wins_a, wins_b, draws,
            crowns_a, crowns_b, latest_battle_time
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(loadout_a_signature, loadout_b_signature) DO UPDATE SET
            games=games+1, wins_a=wins_a+excluded.wins_a, wins_b=wins_b+excluded.wins_b,
            draws=draws+excluded.draws, crowns_a=crowns_a+excluded.crowns_a,
            crowns_b=crowns_b+excluded.crowns_b,
            latest_battle_time=CASE
                WHEN excluded.latest_battle_time IS NULL THEN latest_battle_time
                WHEN latest_battle_time IS NULL OR excluded.latest_battle_time > latest_battle_time
                THEN excluded.latest_battle_time ELSE latest_battle_time END
        """,
        (loadout_a, loadout_b, wins_a, wins_b, team_result[2], crowns_a, crowns_b, latest),
    )


def _increment_loadout_features(
    tower_counts: dict,
    evolution_counts: dict,
    elite_counts: dict,
    loadout_card_counts: dict,
    entity_counts: dict,
    loadout: dict,
    result: tuple[int, int, int],
) -> None:
    tower = loadout["tower"]
    tower_key = str(tower["id"])
    if tower_key not in tower_counts:
        tower_counts[tower_key] = [tower, 0, 0, 0, 0]
    values = tower_counts[tower_key]
    values[1] += 1
    values[2] += result[0]
    values[3] += result[1]
    values[4] += result[2]
    tower_entity_id = f"tower:{tower_key}"
    if tower_entity_id not in entity_counts:
        entity_counts[tower_entity_id] = ["tower", None, None, tower_key, tower, "tower", 0, 0, 0, 0]
    tower_entity = entity_counts[tower_entity_id]
    tower_entity[6] += 1
    tower_entity[7] += result[0]
    tower_entity[8] += result[1]
    tower_entity[9] += result[2]
    for card in loadout["cards"]:
        catalog_key = str(card["id"])
        card_name = str(card.get("name") or card["id"])
        if catalog_key not in loadout_card_counts:
            loadout_card_counts[catalog_key] = [card_name, 0, 0, 0]
        catalog_values = loadout_card_counts[catalog_key]
        catalog_values[0] = min(catalog_values[0], card_name)
        catalog_values[1] += 1
        catalog_values[2] += int(int(card.get("evolution_level") or 0) == 1)
        catalog_values[3] += int(card.get("elite") is True)
        evolution_level = int(card.get("evolution_level") or 0)
        special_state = "elite" if card.get("elite") is True else (
            "evolution" if evolution_level == 1 else "ordinary"
        )
        entity_id = f"card:{catalog_key}:{special_state}"
        if entity_id not in entity_counts:
            entity_counts[entity_id] = [
                "card", catalog_key, card_name, None, card, special_state, 0, 0, 0, 0
            ]
        entity = entity_counts[entity_id]
        entity[6] += 1
        entity[7] += result[0]
        entity[8] += result[1]
        entity[9] += result[2]
        if evolution_level == 1:
            key = (str(card["id"]), str(card.get("name") or card["id"]), int(card["evolution_level"]))
            _increment(evolution_counts, key, result)
        if card.get("elite") is True:
            key = (str(card["id"]), str(card.get("name") or card["id"]))
            _increment(elite_counts, key, result)


def _finalize_full_loadouts(
    connection: sqlite3.Connection,
    full_side_records: int,
    tower_counts: dict,
    evolution_counts: dict,
    elite_counts: dict,
    loadout_card_counts: dict,
    entity_counts: dict,
) -> None:
    connection.execute(
        """
        UPDATE full_loadout_stats SET
            usage_rate=ROUND(games * 100.0 / ?, 6),
            clean_win_rate=CASE WHEN wins+losses=0 THEN 0 ELSE ROUND(wins * 100.0 / (wins+losses), 6) END,
            net_win_rate=CASE WHEN wins+losses=0 THEN -50 ELSE ROUND(wins * 100.0 / (wins+losses) - 50, 6) END
        """,
        (full_side_records,),
    )
    for tower_id, values in sorted(tower_counts.items()):
        tower, appearances, wins, losses, draws = values
        decisions = wins + losses
        connection.execute(
            "INSERT INTO tower_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tower_id,
                loadout_payload(tower),
                appearances,
                wins,
                losses,
                draws,
                round(appearances * 100 / full_side_records, 6) if full_side_records else 0.0,
                round(wins * 100 / decisions, 6) if decisions else 0.0,
            ),
        )
    for (card_id, card_name, evolution_level), values in sorted(evolution_counts.items()):
        appearances, wins, losses, draws = values
        decisions = wins + losses
        connection.execute(
            "INSERT INTO evolution_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card_id, card_name, evolution_level, appearances, wins, losses, draws,
                round(appearances * 100 / full_side_records, 6) if full_side_records else 0.0,
                round(wins * 100 / decisions, 6) if decisions else 0.0,
            ),
        )
    for (card_id, card_name), values in sorted(elite_counts.items()):
        appearances, wins, losses, draws = values
        decisions = wins + losses
        connection.execute(
            "INSERT INTO elite_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card_id, card_name, appearances, wins, losses, draws,
                round(appearances * 100 / full_side_records, 6) if full_side_records else 0.0,
                round(wins * 100 / decisions, 6) if decisions else 0.0,
            ),
        )
    connection.executemany(
        "INSERT INTO loadout_card_catalog VALUES (?, ?, ?, ?, ?)",
        (
            (card_id, values[0], *values[1:])
            for card_id, values in sorted(loadout_card_counts.items())
        ),
    )
    appearances_universe = sorted(values[6] for values in entity_counts.values())
    universe = len(appearances_universe)
    for entity_id, values in sorted(entity_counts.items()):
        (
            entity_type,
            card_id,
            card_name,
            tower_id,
            entity_payload,
            special_state,
            appearances,
            wins,
            losses,
            draws,
        ) = values
        decisions = wins + losses
        clean = wins / decisions * 100 if decisions else 0.0
        usage = appearances / full_side_records * 100 if full_side_records else 0.0
        percentile = (
            sum(1 for count in appearances_universe if count <= appearances) / universe
            if universe else 0.0
        )
        wilson = _wilson_lower_bound(wins, losses)
        confidence = min(1.0, math.sqrt(decisions / 5000))
        rating = 100 * (0.65 * wilson + 0.20 * percentile + 0.15 * confidence)
        connection.execute(
            "INSERT INTO loadout_entity_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entity_id,
                entity_type,
                card_id,
                card_name,
                tower_id,
                loadout_payload(entity_payload),
                special_state,
                appearances,
                wins,
                losses,
                draws,
                round(usage, 6),
                round(clean, 6),
                round(clean - 50, 6),
                round(wilson * 100, 6),
                round(percentile * 100, 6),
                round(confidence * 100, 6),
                round(rating, 6),
            ),
        )


def _finalize_fixed_card_tables(
    connection: sqlite3.Connection,
    card_counts: dict,
    teammates: dict,
    opponents: dict,
    side_records: int,
) -> None:
    appearances = sorted(values[0] for values in card_counts.values())
    universe = len(appearances)
    for card_name, values in sorted(card_counts.items()):
        games, wins, losses, draws = values
        decisions = wins + losses
        clean = wins / decisions * 100 if decisions else 0.0
        usage = games / side_records * 100 if side_records else 0.0
        percentile = sum(1 for count in appearances if count <= games) / universe if universe else 0.0
        wilson = _wilson_lower_bound(wins, losses)
        confidence = min(1.0, math.sqrt(decisions / 5000))
        rating = 100 * (0.65 * wilson + 0.20 * percentile + 0.15 * confidence)
        connection.execute(
            "INSERT INTO card_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card_name,
                games,
                wins,
                losses,
                draws,
                round(usage, 6),
                round(clean, 6),
                round(clean - 50, 6),
                round(wilson * 100, 6),
                round(percentile * 100, 6),
                round(confidence * 100, 6),
                round(rating, 6),
            ),
        )
    connection.executemany(
        "INSERT INTO card_teammates VALUES (?, ?, ?, ?, ?, ?)",
        ((card, teammate, *values) for (card, teammate), values in sorted(teammates.items())),
    )
    connection.executemany(
        "INSERT INTO card_opponents VALUES (?, ?, ?, ?, ?, ?)",
        ((card, opponent, *values) for (card, opponent), values in sorted(opponents.items())),
    )


def _finalize_decks(connection: sqlite3.Connection, side_records: int) -> None:
    connection.execute(
        """
        UPDATE deck_stats SET
            usage_rate=ROUND(games * 100.0 / ?, 6),
            clean_win_rate=CASE WHEN wins+losses=0 THEN 0 ELSE ROUND(wins * 100.0 / (wins+losses), 6) END,
            net_win_rate=CASE WHEN wins+losses=0 THEN -50 ELSE ROUND(wins * 100.0 / (wins+losses) - 50, 6) END
        """,
        (side_records,),
    )


def _finalize_archetypes(
    connection: sqlite3.Connection,
    archetype_counts: dict,
    archetype_matchups: dict,
    side_records: int,
) -> None:
    note = "Feature-weighted deck label; classification is heuristic, statistics are observed."
    for archetype, values in sorted(archetype_counts.items()):
        games, wins, losses, draws = values
        decisions = wins + losses
        clean = wins / decisions * 100 if decisions else 0.0
        connection.execute(
            "INSERT INTO archetype_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                archetype,
                games,
                wins,
                losses,
                draws,
                round(games / side_records * 100, 6) if side_records else 0.0,
                round(clean, 6),
                round(clean - 50, 6),
                f"feature-weighted-v2/{archetype_family(archetype)}",
                note,
            ),
        )
    connection.executemany(
        "INSERT INTO archetype_matchups VALUES (?, ?, ?, ?, ?, ?)",
        (
            (archetype, opponent, *values)
            for (archetype, opponent), values in sorted(archetype_matchups.items())
        ),
    )


def _existing_manifest(destination: Path, source_hash: str) -> dict | None:
    manifest_path = destination / "manifest.json"
    stats_path = destination / "stats.sqlite"
    if not manifest_path.is_file() or not stats_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return None
    if manifest.get("filters", {}).get("archetype_classifier_version") != CLASSIFIER_VERSION:
        return None
    if manifest.get("source_aggregates_sha256") != source_hash:
        return None
    if manifest.get("stats_sqlite_sha256") != _sha256(stats_path):
        return None
    return manifest


def build_structured_stats(data_dir: Path, snapshot_id: str) -> dict:
    """Build a snapshot-scoped exact-8-card, two-sided structured index."""
    data_dir = Path(data_dir)
    snapshot_id = _snapshot_id(snapshot_id)
    archive = data_dir / "snapshot_archives" / snapshot_id
    archive_manifest = _read_json(archive / "manifest.json")
    summary = _read_json(archive / "collector_snapshot.json")
    if archive_manifest.get("complete") is not True or archive_manifest.get("snapshot_id") != snapshot_id:
        raise StructuredStatsError("snapshot archive is incomplete or mismatched")
    if summary.get("snapshot_id") != snapshot_id:
        raise StructuredStatsError("collector summary identity mismatch")
    source_path = archive / "aggregates.sqlite"
    if not source_path.is_file():
        raise StructuredStatsError("exact aggregate store is missing")
    source_hash = _sha256(source_path)
    destination = data_dir / "structured_stats" / snapshot_id
    if destination.exists():
        existing = _existing_manifest(destination, source_hash)
        if existing is not None:
            return existing
        try:
            stale_manifest = _read_json(destination / "manifest.json")
        except StructuredStatsError:
            raise StructuredStatsError("existing structured index is incomplete or stale") from None
        if (
            stale_manifest.get("snapshot_id") != snapshot_id
            or stale_manifest.get("source_aggregates_sha256") != source_hash
        ):
            raise StructuredStatsError("existing structured index is incomplete or stale")

    root = data_dir / "structured_stats"
    root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=root))
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(temp_dir / "stats.sqlite")
    card_counts = defaultdict(lambda: [0, 0, 0, 0])
    teammates = defaultdict(lambda: [0, 0, 0, 0])
    opponents = defaultdict(lambda: [0, 0, 0, 0])
    archetype_counts = defaultdict(lambda: [0, 0, 0, 0])
    archetype_matchups = defaultdict(lambda: [0, 0, 0, 0])
    tower_counts: dict = {}
    evolution_counts = defaultdict(lambda: [0, 0, 0, 0])
    elite_counts = defaultdict(lambda: [0, 0, 0, 0])
    loadout_card_counts: dict = {}
    entity_counts: dict = {}
    source_battles = 0
    included_battles = 0
    excluded = 0
    full_loadout_battles = 0
    excluded_incomplete_loadouts = 0
    try:
        _schema(target)
        for (payload,) in source.execute("SELECT payload FROM battles ORDER BY sequence"):
            source_battles += 1
            battle = json.loads(payload)
            team_deck = _deck(battle.get("team_deck"))
            opponent_deck = _deck(battle.get("opponent_deck"))
            if not team_deck or not opponent_deck:
                excluded += 1
                continue
            included_battles += 1
            team_crowns = int(battle.get("team_crowns") or 0)
            opponent_crowns = int(battle.get("opponent_crowns") or 0)
            team_result = _result(team_crowns, opponent_crowns)
            opponent_result = _result(opponent_crowns, team_crowns)
            team_signature = _signature(team_deck)
            opponent_signature = _signature(opponent_deck)
            team_archetype = classify_deck(team_deck).name
            opponent_archetype = classify_deck(opponent_deck).name

            for deck, opposing_deck, result, signature, archetype, opposing_archetype, crowns in (
                (
                    team_deck,
                    opponent_deck,
                    team_result,
                    team_signature,
                    team_archetype,
                    opponent_archetype,
                    team_crowns,
                ),
                (
                    opponent_deck,
                    team_deck,
                    opponent_result,
                    opponent_signature,
                    opponent_archetype,
                    team_archetype,
                    opponent_crowns,
                ),
            ):
                _upsert_deck(target, signature, deck, archetype, result, crowns)
                _increment(archetype_counts, archetype, result)
                _increment(archetype_matchups, (archetype, opposing_archetype), result)
                for card in deck:
                    _increment(card_counts, card, result)
                    for teammate in deck:
                        if teammate != card:
                            _increment(teammates, (card, teammate), result)
                    for opposing_card in opposing_deck:
                        _increment(opponents, (card, opposing_card), result)

            _upsert_matchup(
                target,
                team_signature,
                opponent_signature,
                team_result,
                team_crowns,
                opponent_crowns,
                battle.get("battle_time"),
            )
            team_loadout = canonical_loadout(battle.get("team_loadout"))
            opponent_loadout = canonical_loadout(battle.get("opponent_loadout"))
            team_loadout_signature = full_loadout_signature(team_loadout)
            opponent_loadout_signature = full_loadout_signature(opponent_loadout)
            if team_loadout_signature and opponent_loadout_signature:
                full_loadout_battles += 1
                for loadout, loadout_signature, base_signature, result, crowns in (
                    (
                        team_loadout,
                        team_loadout_signature,
                        team_signature,
                        team_result,
                        team_crowns,
                    ),
                    (
                        opponent_loadout,
                        opponent_loadout_signature,
                        opponent_signature,
                        opponent_result,
                        opponent_crowns,
                    ),
                ):
                    _upsert_full_loadout(
                        target, loadout_signature, loadout, base_signature, result, crowns
                    )
                    _increment_loadout_features(
                        tower_counts,
                        evolution_counts,
                        elite_counts,
                        loadout_card_counts,
                        entity_counts,
                        loadout,
                        result,
                    )
                _upsert_full_matchup(
                    target,
                    team_loadout_signature,
                    opponent_loadout_signature,
                    team_result,
                    team_crowns,
                    opponent_crowns,
                    battle.get("battle_time"),
                )
            else:
                excluded_incomplete_loadouts += 1
            if included_battles % 1000 == 0:
                target.commit()

        declared = int(summary.get("sample_battles") or 0)
        if source_battles != declared:
            raise StructuredStatsError(
                f"source battle count mismatch: declared={declared} actual={source_battles}"
            )
        side_records = included_battles * 2
        _finalize_fixed_card_tables(target, card_counts, teammates, opponents, side_records)
        _finalize_decks(target, side_records)
        _finalize_archetypes(target, archetype_counts, archetype_matchups, side_records)
        full_side_records = full_loadout_battles * 2
        _finalize_full_loadouts(
            target,
            full_side_records,
            tower_counts,
            evolution_counts,
            elite_counts,
            loadout_card_counts,
            entity_counts,
        )
        counts = {
            "source_battles": source_battles,
            "included_battles": included_battles,
            "excluded_incomplete_decks": excluded,
            "side_records": side_records,
            "full_loadout_battles": full_loadout_battles,
            "full_loadout_side_records": full_side_records,
            "excluded_incomplete_loadouts": excluded_incomplete_loadouts,
            "cards": len(card_counts),
            "card_teammate_rows": len(teammates),
            "card_opponent_rows": len(opponents),
            "decks": int(target.execute("SELECT COUNT(*) FROM deck_stats").fetchone()[0]),
            "matchups": int(target.execute("SELECT COUNT(*) FROM matchup_stats").fetchone()[0]),
            "full_loadouts": int(target.execute("SELECT COUNT(*) FROM full_loadout_stats").fetchone()[0]),
            "full_loadout_matchups": int(
                target.execute("SELECT COUNT(*) FROM full_loadout_matchup_stats").fetchone()[0]
            ),
            "towers": len(tower_counts),
            "evolution_rows": len(evolution_counts),
            "elite_rows": len(elite_counts),
            "loadout_cards": len(loadout_card_counts),
            "loadout_entities": len(entity_counts),
            "archetypes": len(archetype_counts),
            "archetype_matchups": len(archetype_matchups),
        }
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "fetched_at": summary.get("fetched_at"),
            "source_battles": source_battles,
            "included_battles": included_battles,
            "excluded_incomplete_decks": excluded,
            "side_records": side_records,
            "full_loadout_battles": full_loadout_battles,
            "full_loadout_side_records": full_side_records,
            "excluded_incomplete_loadouts": excluded_incomplete_loadouts,
            "rating_formula_version": RATING_FORMULA_VERSION,
            "archetype_classifier_version": CLASSIFIER_VERSION,
        }
        target.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            ((key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()),
        )
        target.commit()
        target.close()
        target = None
        stats_path = temp_dir / "stats.sqlite"
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source": "Supercell API live sample",
            "fetched_at": summary.get("fetched_at"),
            "source_aggregates_sha256": source_hash,
            "stats_sqlite_sha256": _sha256(stats_path),
            "counts": counts,
            "filters": {
                "deck_contract": "exactly_8_unique_cards_on_both_sides",
                "full_loadout_contract": "official_tower_plus_8_card_ids_with_evolution_and_elite_state_v1",
                "excluded_source_battles": excluded,
                "excluded_incomplete_loadouts": excluded_incomplete_loadouts,
                "archetype_classifier_version": CLASSIFIER_VERSION,
            },
            "metrics": {
                "clean_win_rate": "wins / (wins + losses); draws excluded",
                "net_win_rate": "clean_win_rate - 50 percentage points",
                "usage_rate": "appearances_or_games / included_side_records",
                "rating_formula_version": RATING_FORMULA_VERSION,
                "rating": "65% Wilson lower bound + 20% usage percentile + 15% sample confidence",
            },
            "cost_boundaries": {
                "supercell_requests": 0,
                "cloud_llm_calls": 0,
                "cloud_embedding_calls": 0,
                "local_embedding_calls": 0,
            },
        }
        _write_json(temp_dir / "manifest.json", manifest)
        _publish_directory(temp_dir, destination)
        return manifest
    except Exception:
        if target is not None:
            target.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    finally:
        source.close()
