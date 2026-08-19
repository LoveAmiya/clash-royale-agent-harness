"""Full-loadout feature aggregation and SQLite table finalization."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable

from battle_loadout import loadout_payload


def increment_loadout_features(
    tower_counts: dict,
    evolution_counts: dict,
    elite_counts: dict,
    loadout_card_counts: dict,
    entity_counts: dict,
    loadout: dict,
    result: tuple[int, int, int],
    *,
    increment: Callable[[dict, object, tuple[int, int, int]], None],
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
            increment(evolution_counts, key, result)
        if card.get("elite") is True:
            key = (str(card["id"]), str(card.get("name") or card["id"]))
            increment(elite_counts, key, result)


def finalize_full_loadouts(
    connection: sqlite3.Connection,
    full_side_records: int,
    tower_counts: dict,
    evolution_counts: dict,
    elite_counts: dict,
    loadout_card_counts: dict,
    entity_counts: dict,
    *,
    wilson_lower_bound: Callable[[int, int], float],
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
        ((card_id, values[0], *values[1:]) for card_id, values in sorted(loadout_card_counts.items())),
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
        wilson = wilson_lower_bound(wins, losses)
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
