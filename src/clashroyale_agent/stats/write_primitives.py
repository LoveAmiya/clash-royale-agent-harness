"""SQLite aggregate upserts for deterministic structured statistics."""

from __future__ import annotations

import json
import sqlite3

from battle_loadout import loadout_payload


def upsert_deck(
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


def upsert_matchup(
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


def upsert_full_loadout(
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


def upsert_full_matchup(
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
