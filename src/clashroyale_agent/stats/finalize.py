"""Finalization writes for structured statistics tables."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable


def finalize_fixed_card_tables(connection: sqlite3.Connection, card_counts: dict, teammates: dict, opponents: dict, side_records: int, *, wilson_lower_bound: Callable[[int, int], float]) -> None:
    appearances = sorted(values[0] for values in card_counts.values())
    universe = len(appearances)
    for card_name, values in sorted(card_counts.items()):
        games, wins, losses, draws = values
        decisions = wins + losses
        clean = wins / decisions * 100 if decisions else 0.0
        usage = games / side_records * 100 if side_records else 0.0
        percentile = sum(1 for count in appearances if count <= games) / universe if universe else 0.0
        wilson = wilson_lower_bound(wins, losses)
        confidence = min(1.0, math.sqrt(decisions / 5000))
        rating = 100 * (0.65 * wilson + 0.20 * percentile + 0.15 * confidence)
        connection.execute("INSERT INTO card_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (card_name, games, wins, losses, draws, round(usage, 6), round(clean, 6), round(clean - 50, 6), round(wilson * 100, 6), round(percentile * 100, 6), round(confidence * 100, 6), round(rating, 6)))
    connection.executemany("INSERT INTO card_teammates VALUES (?, ?, ?, ?, ?, ?)", ((card, teammate, *values) for (card, teammate), values in sorted(teammates.items())))
    connection.executemany("INSERT INTO card_opponents VALUES (?, ?, ?, ?, ?, ?)", ((card, opponent, *values) for (card, opponent), values in sorted(opponents.items())))


def finalize_decks(connection: sqlite3.Connection, side_records: int) -> None:
    connection.execute("""
        UPDATE deck_stats SET
            usage_rate=ROUND(games * 100.0 / ?, 6),
            clean_win_rate=CASE WHEN wins+losses=0 THEN 0 ELSE ROUND(wins * 100.0 / (wins+losses), 6) END,
            net_win_rate=CASE WHEN wins+losses=0 THEN -50 ELSE ROUND(wins * 100.0 / (wins+losses) - 50, 6) END
        """, (side_records,))


def finalize_archetypes(connection: sqlite3.Connection, archetype_counts: dict, archetype_matchups: dict, side_records: int, *, family: Callable[[str], str]) -> None:
    note = "Feature-weighted deck label; classification is heuristic, statistics are observed."
    for archetype, values in sorted(archetype_counts.items()):
        games, wins, losses, draws = values
        decisions = wins + losses
        clean = wins / decisions * 100 if decisions else 0.0
        connection.execute("INSERT INTO archetype_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (archetype, games, wins, losses, draws, round(games / side_records * 100, 6) if side_records else 0.0, round(clean, 6), round(clean - 50, 6), f"feature-weighted-v2/{family(archetype)}", note))
    connection.executemany("INSERT INTO archetype_matchups VALUES (?, ?, ?, ?, ?, ?)", ((archetype, opponent, *values) for (archetype, opponent), values in sorted(archetype_matchups.items())))


__all__ = ["finalize_fixed_card_tables", "finalize_decks", "finalize_archetypes"]
