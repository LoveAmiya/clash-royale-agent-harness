"""Read-only SQLite context and provenance projection for structured queries."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


TABLES = (
    "card_stats", "card_teammates", "card_opponents", "deck_stats", "matchup_stats",
    "full_loadout_stats", "full_loadout_matchup_stats", "tower_stats", "evolution_stats",
    "elite_stats", "loadout_card_catalog", "loadout_entity_stats", "archetype_stats",
    "archetype_matchups", "archetype_decks",
)


@contextmanager
def readonly_connection(database_path: Path, dataset_scope: str | None) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if dataset_scope is not None:
            for table in TABLES:
                columns = [str(row[1]) for row in connection.execute(f'PRAGMA main.table_info("{table}")') if str(row[1]) != "dataset_scope"]
                if not columns:
                    continue
                selected = ",".join(f'"{column}"' for column in columns)
                scope_literal = dataset_scope.replace("'", "''")
                connection.execute(f'CREATE TEMP VIEW "{table}" AS SELECT {selected} FROM main."{table}" WHERE dataset_scope=\'{scope_literal}\'')
        yield connection
    finally:
        connection.close()


def provenance(*, dataset: dict | None, manifest: dict, snapshot_group_id: str | None, snapshot_id: str, dataset_scope: str | None) -> dict:
    if dataset is not None:
        counts = dataset["structured_counts"]
        return {
            "snapshot_group_id": snapshot_group_id, "snapshot_id": snapshot_id, "dataset_scope": dataset_scope,
            "window_started_at": dataset.get("window_started_at"), "window_ended_at": dataset.get("window_ended_at"),
            "unique_battles": dataset.get("unique_battles"), "weekly_batch_count": dataset.get("weekly_batch_count"),
            "daily_batch_count": dataset.get("daily_batch_count"), "ranked_coverage": dataset.get("ranked_coverage"),
            "missing_collection_dates": dataset.get("missing_collection_dates", []), "source": "Supercell API rolling Path of Legend corpus",
            "total_sample_battles": counts["source_battles"], "included_battles": counts["included_battles"],
            "excluded_incomplete_decks": counts["excluded_incomplete_decks"], "side_records": counts["side_records"],
            "full_loadout_battles": counts.get("full_loadout_battles", 0), "full_loadout_side_records": counts.get("full_loadout_side_records", 0),
            "excluded_incomplete_loadouts": counts.get("excluded_incomplete_loadouts", 0),
            "structured_index_fingerprint": manifest.get("structured_stats_fingerprint"),
            "deck_contract": "exactly_8_unique_cards_on_both_sides",
        }
    counts = manifest["counts"]
    return {
        "snapshot_id": snapshot_id, "fetched_at": manifest.get("fetched_at"), "source": manifest.get("source"),
        "total_sample_battles": counts["source_battles"], "included_battles": counts["included_battles"],
        "excluded_incomplete_decks": counts["excluded_incomplete_decks"], "side_records": counts["side_records"],
        "full_loadout_battles": counts.get("full_loadout_battles", 0), "full_loadout_side_records": counts.get("full_loadout_side_records", 0),
        "excluded_incomplete_loadouts": counts.get("excluded_incomplete_loadouts", 0),
        "structured_index_fingerprint": manifest.get("stats_sqlite_sha256"),
        "deck_contract": manifest.get("filters", {}).get("deck_contract"),
    }


__all__ = ["provenance", "readonly_connection"]
