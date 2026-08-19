"""Meta-delta materialization for rolling snapshot groups."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


def wilson_interval(wins: int, losses: int, z: float = 1.96) -> tuple[float, float]:
    decisions = wins + losses
    if decisions <= 0:
        return 0.0, 0.0
    probability = wins / decisions
    z_squared = z * z
    denominator = 1 + z_squared / decisions
    centre = probability + z_squared / (2 * decisions)
    import math
    margin = z * math.sqrt(
        (probability * (1 - probability) + z_squared / (4 * decisions)) / decisions
    )
    return (
        max(0.0, (centre - margin) / denominator) * 100,
        min(1.0, (centre + margin) / denominator) * 100,
    )


def materialize_meta_deltas(
    connection: sqlite3.Connection,
    datasets: dict[str, dict],
    *,
    dataset_scopes: tuple[str, ...],
    scope_pairs: tuple[tuple[str, str], ...],
    interval: Callable[[int, int], tuple[float, float]] = wilson_interval,
) -> None:
    connection.execute(
        """
        CREATE TABLE meta_delta(
            current_scope TEXT NOT NULL,
            baseline_scope TEXT NOT NULL,
            category TEXT NOT NULL,
            item_id TEXT NOT NULL,
            current_sample INTEGER NOT NULL,
            baseline_sample INTEGER NOT NULL,
            current_usage_rate REAL NOT NULL,
            baseline_usage_rate REAL NOT NULL,
            usage_delta REAL NOT NULL,
            current_win_rate REAL NOT NULL,
            baseline_win_rate REAL NOT NULL,
            win_delta REAL NOT NULL,
            significant INTEGER NOT NULL,
            confidence_note TEXT NOT NULL,
            PRIMARY KEY(current_scope, baseline_scope, category, item_id)
        )
        """
    )

    def rows(table: str, scope: str, id_column: str) -> dict[str, sqlite3.Row]:
        return {
            str(row[id_column]): row
            for row in connection.execute(
                f"SELECT * FROM {table} WHERE dataset_scope=?", (scope,)
            )
        }

    def insert_pair(
        current_scope: str,
        baseline_scope: str,
        category: str,
        current_rows: dict[str, sqlite3.Row],
        baseline_rows: dict[str, sqlite3.Row],
        sample_column: str,
        threshold: int,
    ) -> None:
        for item_id in sorted(set(current_rows) | set(baseline_rows)):
            current = current_rows.get(item_id)
            baseline = baseline_rows.get(item_id)
            current_sample = int(current[sample_column]) if current is not None else 0
            baseline_sample = int(baseline[sample_column]) if baseline is not None else 0
            current_usage = float(current["usage_rate"]) if current is not None else 0.0
            baseline_usage = float(baseline["usage_rate"]) if baseline is not None else 0.0
            current_win = float(current["clean_win_rate"]) if current is not None else 0.0
            baseline_win = float(baseline["clean_win_rate"]) if baseline is not None else 0.0
            current_interval = interval(
                int(current["wins"]) if current is not None else 0,
                int(current["losses"]) if current is not None else 0,
            )
            baseline_interval = interval(
                int(baseline["wins"]) if baseline is not None else 0,
                int(baseline["losses"]) if baseline is not None else 0,
            )
            intervals_separate = (
                current_interval[0] > baseline_interval[1]
                or baseline_interval[0] > current_interval[1]
            )
            enough = current_sample >= threshold and baseline_sample >= threshold
            appeared_or_disappeared = (
                (current_sample == 0 and baseline_sample >= threshold)
                or (baseline_sample == 0 and current_sample >= threshold)
            )
            meaningful_change = abs(current_usage - baseline_usage) >= 0.5 or abs(current_win - baseline_win) >= 3.0
            significant = appeared_or_disappeared or (enough and intervals_separate and meaningful_change)
            note = (
                "significant_wilson95_and_absolute_threshold"
                if significant else
                "observed_below_significance_threshold"
            )
            connection.execute(
                "INSERT INTO meta_delta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    current_scope,
                    baseline_scope,
                    category,
                    item_id,
                    current_sample,
                    baseline_sample,
                    current_usage,
                    baseline_usage,
                    round(current_usage - baseline_usage, 6),
                    current_win,
                    baseline_win,
                    round(current_win - baseline_win, 6),
                    int(significant),
                    note,
                ),
            )

    def full_loadout_base_rows(scope: str) -> dict[str, sqlite3.Row]:
        return {
            str(row["deck_signature"]): row
            for row in connection.execute(
                """
                SELECT base_deck_signature AS deck_signature,
                       SUM(games) AS games, SUM(wins) AS wins, SUM(losses) AS losses,
                       SUM(draws) AS draws, SUM(usage_rate) AS usage_rate,
                       CASE WHEN SUM(wins)+SUM(losses)=0 THEN 0
                            ELSE SUM(wins) * 100.0 / (SUM(wins)+SUM(losses)) END AS clean_win_rate
                FROM full_loadout_stats WHERE dataset_scope=?
                GROUP BY base_deck_signature
                """,
                (scope,),
            )
        }

    levels = ("top_100", "top_200", "top_500", "top_1000", "all")
    for current_prefix, baseline_prefix in scope_pairs:
        for level in levels:
            current_scope = f"{current_prefix}_{level}"
            baseline_scope = f"{baseline_prefix}_{level}"
            if not datasets[current_scope]["ready"] or not datasets[baseline_scope]["ready"]:
                continue
            insert_pair(
                current_scope,
                baseline_scope,
                "entity",
                rows("loadout_entity_stats", current_scope, "entity_id"),
                rows("loadout_entity_stats", baseline_scope, "entity_id"),
                "appearances",
                200,
            )
            insert_pair(
                current_scope,
                baseline_scope,
                "archetype",
                rows("archetype_stats", current_scope, "archetype"),
                rows("archetype_stats", baseline_scope, "archetype"),
                "games",
                200,
            )
            insert_pair(
                current_scope,
                baseline_scope,
                "deck",
                rows("deck_stats", current_scope, "deck_signature"),
                rows("deck_stats", baseline_scope, "deck_signature"),
                "games",
                30,
            )
            datasets[current_scope]["delta_ready"] = True
    for scope in dataset_scopes:
        if not datasets[scope]["ready"] or not datasets[scope]["complete_loadout_ready"]:
            continue
        insert_pair(
            scope,
            scope,
            "base8_full_loadout_divergence",
            rows("deck_stats", scope, "deck_signature"),
            full_loadout_base_rows(scope),
            "games",
            30,
        )
    connection.execute(
        "CREATE INDEX idx_meta_delta_scope ON meta_delta(current_scope, category, significant DESC)"
    )
