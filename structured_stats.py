"""Build deterministic two-sided statistics from a published snapshot archive."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import app_config as _app_config  # noqa: F401 - bootstrap src package imports

from battle_loadout import canonical_loadout, full_loadout_signature
from deck_archetypes import CLASSIFIER_VERSION, archetype_family, classify_deck
try:
    from clashroyale_agent.stats.finalize import (
        finalize_archetypes as finalize_archetypes_orchestrated,
        finalize_decks as finalize_decks_orchestrated,
        finalize_fixed_card_tables as finalize_fixed_card_tables_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.finalize import (
        finalize_archetypes as finalize_archetypes_orchestrated,
        finalize_decks as finalize_decks_orchestrated,
        finalize_fixed_card_tables as finalize_fixed_card_tables_orchestrated,
    )
try:
    from clashroyale_agent.stats.math_primitives import increment as _increment_orchestrated, result as _result_orchestrated, wilson_lower_bound as _wilson_lower_bound_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.math_primitives import increment as _increment_orchestrated, result as _result_orchestrated, wilson_lower_bound as _wilson_lower_bound_orchestrated
try:
    from clashroyale_agent.stats.schema import create_schema as _create_schema_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.schema import create_schema as _create_schema_orchestrated
try:
    from clashroyale_agent.stats.write_primitives import (
        upsert_deck as _upsert_deck_orchestrated,
        upsert_full_loadout as _upsert_full_loadout_orchestrated,
        upsert_full_matchup as _upsert_full_matchup_orchestrated,
        upsert_matchup as _upsert_matchup_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.write_primitives import (
        upsert_deck as _upsert_deck_orchestrated,
        upsert_full_loadout as _upsert_full_loadout_orchestrated,
        upsert_full_matchup as _upsert_full_matchup_orchestrated,
        upsert_matchup as _upsert_matchup_orchestrated,
    )
try:
    from clashroyale_agent.stats.loadout_stats import (
        finalize_full_loadouts as _finalize_full_loadouts_orchestrated,
        increment_loadout_features as _increment_loadout_features_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.loadout_stats import (
        finalize_full_loadouts as _finalize_full_loadouts_orchestrated,
        increment_loadout_features as _increment_loadout_features_orchestrated,
    )
try:
    from clashroyale_agent.stats.build_primitives import (
        SAFE_SNAPSHOT_ID as _safe_snapshot_id,
        publish_directory as _publish_directory_orchestrated,
        read_json as _read_json_orchestrated,
        sha256 as _sha256_orchestrated,
        snapshot_id as _snapshot_id_orchestrated,
        write_json as _write_json_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.build_primitives import (
        SAFE_SNAPSHOT_ID as _safe_snapshot_id,
        publish_directory as _publish_directory_orchestrated,
        read_json as _read_json_orchestrated,
        sha256 as _sha256_orchestrated,
        snapshot_id as _snapshot_id_orchestrated,
        write_json as _write_json_orchestrated,
    )


SCHEMA_VERSION = 5
RATING_FORMULA_VERSION = "wilson65_usage20_confidence15_v1"
_SAFE_SNAPSHOT_ID = _safe_snapshot_id


class StructuredStatsError(ValueError):
    """Raised when a trustworthy structured index cannot be built."""


def _sha256(path: Path) -> str:
    return _sha256_orchestrated(path)


def _write_json(path: Path, value: object) -> None:
    _write_json_orchestrated(path, value)


def _read_json(path: Path) -> dict:
    return _read_json_orchestrated(path, StructuredStatsError)


def _snapshot_id(value: str) -> str:
    return _snapshot_id_orchestrated(value, StructuredStatsError)


def _publish_directory(source: Path, destination: Path) -> None:
    _publish_directory_orchestrated(source, destination)


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
    return _result_orchestrated(crowns, opponent_crowns)


def _wilson_lower_bound(wins: int, losses: int, z: float = 1.96) -> float:
    return _wilson_lower_bound_orchestrated(wins, losses, z)


def _increment(counter: dict, key: object, result: tuple[int, int, int], games: int = 1) -> None:
    _increment_orchestrated(counter, key, result, games)


def _schema(connection: sqlite3.Connection) -> None:
    _create_schema_orchestrated(connection)


def _upsert_deck(
    connection: sqlite3.Connection,
    signature: str,
    deck: tuple[str, ...],
    archetype: str,
    result: tuple[int, int, int],
    crowns: int,
) -> None:
    _upsert_deck_orchestrated(connection, signature, deck, archetype, result, crowns)


def _upsert_matchup(
    connection: sqlite3.Connection,
    team_signature: str,
    opponent_signature: str,
    team_result: tuple[int, int, int],
    team_crowns: int,
    opponent_crowns: int,
    battle_time: object,
) -> None:
    _upsert_matchup_orchestrated(
        connection,
        team_signature,
        opponent_signature,
        team_result,
        team_crowns,
        opponent_crowns,
        battle_time,
    )


def _upsert_full_loadout(
    connection: sqlite3.Connection,
    signature: str,
    loadout: dict,
    base_deck_signature: str,
    result: tuple[int, int, int],
    crowns: int,
) -> None:
    _upsert_full_loadout_orchestrated(
        connection, signature, loadout, base_deck_signature, result, crowns
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
    _upsert_full_matchup_orchestrated(
        connection,
        team_signature,
        opponent_signature,
        team_result,
        team_crowns,
        opponent_crowns,
        battle_time,
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
    _increment_loadout_features_orchestrated(
        tower_counts,
        evolution_counts,
        elite_counts,
        loadout_card_counts,
        entity_counts,
        loadout,
        result,
        increment=_increment,
    )


def _finalize_full_loadouts(
    connection: sqlite3.Connection,
    full_side_records: int,
    tower_counts: dict,
    evolution_counts: dict,
    elite_counts: dict,
    loadout_card_counts: dict,
    entity_counts: dict,
) -> None:
    _finalize_full_loadouts_orchestrated(
        connection,
        full_side_records,
        tower_counts,
        evolution_counts,
        elite_counts,
        loadout_card_counts,
        entity_counts,
        wilson_lower_bound=_wilson_lower_bound,
    )


def _finalize_fixed_card_tables(
    connection: sqlite3.Connection,
    card_counts: dict,
    teammates: dict,
    opponents: dict,
    side_records: int,
) -> None:
    return finalize_fixed_card_tables_orchestrated(
        connection, card_counts, teammates, opponents, side_records,
        wilson_lower_bound=_wilson_lower_bound,
    )


def _finalize_decks(connection: sqlite3.Connection, side_records: int) -> None:
    return finalize_decks_orchestrated(connection, side_records)


def _finalize_archetypes(
    connection: sqlite3.Connection,
    archetype_counts: dict,
    archetype_matchups: dict,
    side_records: int,
) -> None:
    return finalize_archetypes_orchestrated(
        connection, archetype_counts, archetype_matchups, side_records,
        family=archetype_family,
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
