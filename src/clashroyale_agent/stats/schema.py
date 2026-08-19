"""SQLite schema ownership for deterministic structured statistics."""

from __future__ import annotations

import sqlite3


def create_schema(connection: sqlite3.Connection) -> None:
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
