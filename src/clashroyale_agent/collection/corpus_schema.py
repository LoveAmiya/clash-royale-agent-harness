"""SQLite schema ownership for the rolling corpus fact store."""

from __future__ import annotations

import sqlite3


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS collection_batches(
            batch_id TEXT PRIMARY KEY,
            batch_type TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            leaderboard_frozen_at TEXT NOT NULL,
            completed_at TEXT,
            expires_at TEXT,
            request_count INTEGER NOT NULL DEFAULT 0,
            rate_limited INTEGER NOT NULL DEFAULT 0,
            refresh_budget_exhausted INTEGER NOT NULL DEFAULT 0,
            source_exhausted INTEGER NOT NULL DEFAULT 0,
            ranked_successes INTEGER NOT NULL DEFAULT 0,
            ranked_target INTEGER NOT NULL DEFAULT 1000,
            top_rank_successes INTEGER NOT NULL DEFAULT 0,
            top_rank_target INTEGER NOT NULL DEFAULT 100,
            coverage REAL NOT NULL DEFAULT 0,
            unique_battles INTEGER NOT NULL DEFAULT 0,
            validation_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS battles(
            battle_id TEXT PRIMARY KEY,
            battle_time TEXT NOT NULL,
            payload TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS battle_observations(
            batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id) ON DELETE CASCADE,
            battle_id TEXT NOT NULL REFERENCES battles(battle_id) ON DELETE CASCADE,
            observer_tag TEXT NOT NULL,
            observer_rank INTEGER,
            observer_source TEXT NOT NULL,
            expansion_root_rank INTEGER,
            observed_at TEXT NOT NULL,
            PRIMARY KEY(batch_id, battle_id, observer_tag, observer_source)
        );
        CREATE TABLE IF NOT EXISTS battle_loadouts(
            battle_id TEXT PRIMARY KEY REFERENCES battles(battle_id) ON DELETE CASCADE,
            schema_version INTEGER NOT NULL,
            team_loadout_json TEXT,
            opponent_loadout_json TEXT,
            complete INTEGER NOT NULL DEFAULT 0,
            loadout_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS batch_players(
            batch_id TEXT NOT NULL REFERENCES collection_batches(batch_id) ON DELETE CASCADE,
            player_tag TEXT NOT NULL,
            observer_rank INTEGER,
            observer_source TEXT NOT NULL,
            request_status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            PRIMARY KEY(batch_id, player_tag)
        );
        CREATE TABLE IF NOT EXISTS corpus_conflicts(
            conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            battle_id TEXT NOT NULL,
            existing_hash TEXT NOT NULL,
            incoming_hash TEXT NOT NULL,
            detected_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS publication_generations(
            generation_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            published_at TEXT,
            manifest_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_batches_status_completed
            ON collection_batches(status, completed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_observations_battle
            ON battle_observations(battle_id, observer_rank);
        CREATE INDEX IF NOT EXISTS idx_observations_rank
            ON battle_observations(observer_source, observer_rank, batch_id);
        CREATE INDEX IF NOT EXISTS idx_loadouts_complete
            ON battle_loadouts(complete, battle_id);
        """
    )
    connection.commit()
