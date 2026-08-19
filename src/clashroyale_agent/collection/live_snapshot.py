from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json

from clashroyale_agent.collection.api_client import SUPERCELL_SOURCE_URL
from clashroyale_agent.collection.battle_parser import (
    normalize_battle_record,
    team_cards as _team_cards,
    team_member as _team_member,
)
from clashroyale_agent.collection.loadout_normalization import normalize_side_loadout


CARD_DECK_VARIANTS_PER_CARD = 20
MAX_PUBLISHED_DECK_MATCHUPS = 20_000
MAX_SPECIAL_FIELD_PROBE_BATTLES = 100
MAX_RESUMABLE_WORKSPACE_AGE_SECONDS = 14 * 24 * 60 * 60
MAX_RANKING_SEED_LOCATIONS = 80
PATH_OF_LEGEND_COLLECTION_SCOPE = "path_of_legend"
PATH_OF_LEGEND_SCOPE_CONTRACT = "path_of_legend_only_v1"


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

def build_disk_backed_snapshot(
    workspace,
    *,
    fetched_at: str,
    target_battles: int,
    collection_metadata: dict,
    export_raw_battles: bool = True,
) -> dict:
    total_battles = workspace.battle_count
    if not total_battles:
        raise ValueError("official API returned no usable battle-log decks")
    cards_meta = []
    distinct_cards = int(workspace.connection.execute("SELECT COUNT(*) FROM card_stats").fetchone()[0])
    if distinct_cards > 256:
        raise ValueError("official snapshot contains an unsafe number of distinct card names")
    card_rows = workspace.connection.execute(
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
    deck_rows = workspace.connection.execute(
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
        variants = workspace.connection.execute(
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

    matchup_total = int(workspace.connection.execute("SELECT COUNT(*) FROM matchup_stats").fetchone()[0])
    matchup_rows = workspace.connection.execute(
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
    profile_rows = workspace.connection.execute(
        "SELECT deck_key, deck_json FROM deck_stats WHERE battles >= 20 ORDER BY battles DESC, deck_json LIMIT 150"
    ).fetchall()
    for deck_key, deck_json in profile_rows:
        deck_name = " / ".join(sorted(json.loads(deck_json)))
        opponent_rows = workspace.connection.execute(
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
        for (payload,) in workspace.connection.execute("SELECT payload FROM probe_battles ORDER BY sequence")
    ]
    raw_records = workspace.export_raw_records() if export_raw_battles else ()
    metrics = dict(collection_metadata)
    metrics.update(
        {
            "streamed_to_disk": True,
            "resumable_workspace": True,
            "max_in_memory_battle_records": workspace.metadata_int("max_in_memory_battle_records"),
            "workspace_bytes": workspace.assert_storage_budget(),
            "exact_matchups_stored": matchup_total,
            "observation_count": workspace.observation_count,
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
        "fetched_players": collection_metadata.get("fetched_players", workspace.processed_players),
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
        "_aggregate_store_path": str(workspace.database_path),
        "_streaming_work_dir": str(workspace.path),
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


__all__ = [
    "CARD_DECK_VARIANTS_PER_CARD",
    "MAX_PUBLISHED_DECK_MATCHUPS",
    "MAX_RANKING_SEED_LOCATIONS",
    "MAX_RESUMABLE_WORKSPACE_AGE_SECONDS",
    "MAX_SPECIAL_FIELD_PROBE_BATTLES",
    "PATH_OF_LEGEND_COLLECTION_SCOPE",
    "PATH_OF_LEGEND_SCOPE_CONTRACT",
    "build_card_deck_stats",
    "build_disk_backed_snapshot",
    "build_live_snapshot",
    "probe_official_special_fields",
]
