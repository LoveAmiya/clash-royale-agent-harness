import tempfile
import unittest
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rolling_corpus import (
    BatchValidationPolicy,
    CorpusConflictError,
    CorpusWriterBusyError,
    CorpusWriterLock,
    DATASET_SCOPES,
    RollingCorpusStore,
)


UTC = timezone.utc


def _battle(battle_id: str, *, team_card: str = "A") -> dict:
    return {
        "battle_id": battle_id,
        "battle_type": "pathOfLegend",
        "battle_time": "20260730T000000.000Z",
        "team_tag": "#TEAM",
        "opponent_tag": "#OPP",
        "team_deck": [team_card, "B", "C", "D", "E", "F", "G", "H"],
        "opponent_deck": ["I", "J", "K", "L", "M", "N", "O", "P"],
        "team_crowns": 1,
        "opponent_crowns": 0,
        "won": True,
    }


def _loadout(prefix: str, *, tower_id: int, first_evolved: bool = False) -> dict:
    cards = []
    for index in range(8):
        cards.append(
            {
                "id": str(26000000 + index),
                "name": f"{prefix}{index}",
                "level": 15 if index == 1 else 14,
                "max_level": 14,
                "evolution_level": 1 if first_evolved and index == 0 else 2 if index == 1 else 0,
                "max_evolution_level": 1,
                "elite": index == 1,
                "elite_detection": "official_evolution_level_v1",
            }
        )
    return {
        "schema_version": 1,
        "tower": {
            "id": str(tower_id),
            "name": f"Tower{tower_id}",
            "level": 15,
            "max_level": 15,
            "evolution_level": 0,
            "max_evolution_level": None,
            "elite": False,
            "elite_detection": "level_above_max_v1",
        },
        "cards": cards,
        "complete": True,
    }


def _battle_with_loadouts(battle_id: str) -> dict:
    return {
        **_battle(battle_id),
        "loadout_schema_version": 1,
        "team_loadout": _loadout("Team", tower_id=159000000, first_evolved=True),
        "opponent_loadout": _loadout("Opponent", tower_id=159000001),
    }


class RollingCorpusStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RollingCorpusStore(Path(self.temp_dir.name) / "corpus.sqlite")
        self.now = datetime(2026, 7, 30, 3, 0, tzinfo=UTC)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _accepted_batch(self, batch_id: str, completed_at: datetime, *, batch_type: str = "daily_ranked"):
        self.store.create_batch(
            batch_id,
            batch_type=batch_type,
            started_at=completed_at - timedelta(hours=1),
            leaderboard_frozen_at=completed_at - timedelta(hours=1),
        )
        self.store.accept_batch_for_test(batch_id, completed_at=completed_at)

    def test_second_writer_is_rejected_with_a_specific_busy_error(self):
        lock_path = Path(self.temp_dir.name) / "writer.lock"
        with CorpusWriterLock(lock_path):
            with self.assertRaises(CorpusWriterBusyError):
                with CorpusWriterLock(lock_path):
                    self.fail("the second writer must never acquire the lock")

    def test_battle_fact_is_global_and_observations_are_preserved(self):
        self._accepted_batch("daily-1", self.now - timedelta(days=2))
        self._accepted_batch("weekly-1", self.now - timedelta(days=1), batch_type="weekly_expanded")

        self.store.ingest_battle(
            "daily-1",
            _battle("battle-1"),
            observer_tag="#P1",
            observer_rank=87,
            observer_source="ranked_direct",
        )
        self.store.ingest_battle(
            "weekly-1",
            _battle("battle-1"),
            observer_tag="#P2",
            observer_rank=436,
            observer_source="ranked_direct",
        )

        self.assertEqual(self.store.fact_count(), 1)
        self.assertEqual(self.store.observation_count(), 2)
        self.assertEqual(self.store.minimum_rank("battle-1", window_days=7, now=self.now), 87)

    def test_reversed_observer_sides_are_the_same_canonical_fact(self):
        self._accepted_batch("daily-1", self.now - timedelta(days=1))
        direct = _battle("battle-reversed")
        reversed_view = {
            **direct,
            "team_tag": direct["opponent_tag"],
            "opponent_tag": direct["team_tag"],
            "team_deck": direct["opponent_deck"],
            "opponent_deck": direct["team_deck"],
            "team_crowns": direct["opponent_crowns"],
            "opponent_crowns": direct["team_crowns"],
            "won": False,
        }
        self.store.ingest_battle(
            "daily-1", direct, observer_tag="#TEAM", observer_rank=1, observer_source="ranked_direct"
        )
        self.store.ingest_battle(
            "daily-1", reversed_view, observer_tag="#OPP", observer_rank=2, observer_source="ranked_direct"
        )

        self.assertEqual(self.store.fact_count(), 1)
        self.assertEqual(self.store.observation_count(), 2)
        self.assertEqual(self.store.conflict_count("daily-1"), 0)

    def test_conflicting_fact_for_same_battle_id_is_quarantined(self):
        self._accepted_batch("daily-1", self.now - timedelta(days=1))
        self.store.ingest_battle(
            "daily-1",
            _battle("battle-1"),
            observer_tag="#P1",
            observer_rank=1,
            observer_source="ranked_direct",
        )

        with self.assertRaises(CorpusConflictError):
            self.store.ingest_battle(
                "daily-1",
                _battle("battle-1", team_card="DIFFERENT"),
                observer_tag="#P2",
                observer_rank=2,
                observer_source="ranked_direct",
            )
        self.assertEqual(self.store.conflict_count("daily-1"), 1)

    def test_old_base_fact_can_be_enriched_with_complete_loadouts(self):
        self._accepted_batch("daily-1", self.now - timedelta(days=1))
        self.store.ingest_battle(
            "daily-1",
            _battle("battle-loadout"),
            observer_tag="#P1",
            observer_rank=1,
            observer_source="ranked_direct",
        )
        self.store.ingest_battle(
            "daily-1",
            _battle_with_loadouts("battle-loadout"),
            observer_tag="#P2",
            observer_rank=2,
            observer_source="ranked_direct",
        )

        records = list(self.store.iter_scope_battles("7d_all", now=self.now))

        self.assertEqual(self.store.fact_count(), 1)
        self.assertEqual(records[0]["loadout_schema_version"], 1)
        self.assertTrue(records[0]["team_loadout"]["complete"])
        self.assertEqual(
            {
                records[0]["team_loadout"]["tower"]["id"],
                records[0]["opponent_loadout"]["tower"]["id"],
            },
            {"159000000", "159000001"},
        )
        self.assertEqual(self.store.conflict_count("daily-1"), 0)

    def test_conflicting_complete_loadout_is_quarantined_without_overwrite(self):
        self._accepted_batch("daily-1", self.now - timedelta(days=1))
        original = _battle_with_loadouts("battle-loadout-conflict")
        conflicting = json.loads(json.dumps(original))
        conflicting["team_loadout"]["tower"]["id"] = "159000099"
        self.store.ingest_battle(
            "daily-1",
            original,
            observer_tag="#P1",
            observer_rank=1,
            observer_source="ranked_direct",
        )

        with self.assertRaises(CorpusConflictError):
            self.store.ingest_battle(
                "daily-1",
                conflicting,
                observer_tag="#P2",
                observer_rank=2,
                observer_source="ranked_direct",
            )

        stored = self.store.connection.execute(
            "SELECT team_loadout_json, opponent_loadout_json FROM battle_loadouts WHERE battle_id=?",
            ("battle-loadout-conflict",),
        ).fetchone()
        tower_ids = {
            json.loads(stored["team_loadout_json"])["tower"]["id"],
            json.loads(stored["opponent_loadout_json"])["tower"]["id"],
        }
        self.assertEqual(tower_ids, {"159000000", "159000001"})
        self.assertEqual(self.store.conflict_count("daily-1"), 1)

    def test_complete_loadout_catalog_metadata_refresh_is_not_a_conflict(self):
        self._accepted_batch("daily-1", self.now - timedelta(days=2))
        self._accepted_batch("daily-2", self.now - timedelta(days=1))
        original = _battle_with_loadouts("battle-loadout-metadata")
        refreshed = json.loads(json.dumps(original))
        refreshed["team_loadout"]["cards"][0]["max_level"] = 15
        refreshed["team_loadout"]["cards"][0]["max_evolution_level"] = 3
        self.store.ingest_battle(
            "daily-1",
            original,
            observer_tag="#P1",
            observer_rank=1,
            observer_source="ranked_direct",
        )

        self.store.ingest_battle(
            "daily-2",
            refreshed,
            observer_tag="#P2",
            observer_rank=2,
            observer_source="ranked_direct",
        )

        stored = self.store.connection.execute(
            "SELECT team_loadout_json, opponent_loadout_json FROM battle_loadouts WHERE battle_id=?",
            ("battle-loadout-metadata",),
        ).fetchone()
        stored_cards = [
            card
            for side in ("team_loadout_json", "opponent_loadout_json")
            for card in json.loads(stored[side])["cards"]
        ]
        refreshed_card = next(
            card
            for card in stored_cards
            if card["id"] == "26000000" and card["max_evolution_level"] == 3
        )
        self.assertEqual(refreshed_card["max_level"], 15)
        self.assertEqual(refreshed_card["max_evolution_level"], 3)
        self.assertEqual(self.store.conflict_count("daily-2"), 0)

    def test_scope_membership_is_nested_and_expired_rank_does_not_stick(self):
        self._accepted_batch("old", self.now - timedelta(days=8))
        self._accepted_batch("new", self.now - timedelta(days=1))
        self.store.ingest_battle(
            "old",
            _battle("battle-1"),
            observer_tag="#TOP",
            observer_rank=50,
            observer_source="ranked_direct",
        )
        self.store.ingest_battle(
            "new",
            _battle("battle-1"),
            observer_tag="#LOWER",
            observer_rank=180,
            observer_source="ranked_direct",
        )
        self.store.ingest_battle(
            "new",
            _battle("battle-2"),
            observer_tag="#EXPANDED",
            observer_rank=None,
            observer_source="opponent_expansion",
        )

        self.assertEqual(self.store.scope_battle_ids("7d_top_100", now=self.now), [])
        self.assertEqual(self.store.scope_battle_ids("7d_top_200", now=self.now), ["battle-1"])
        self.assertEqual(self.store.scope_battle_ids("7d_all", now=self.now), ["battle-1", "battle-2"])
        self.assertEqual(self.store.minimum_rank("battle-1", window_days=7, now=self.now), 180)
        self.assertEqual(self.store.minimum_rank("battle-1", window_days=35, now=self.now), 50)

    def test_dataset_scopes_include_four_historical_week_slices(self):
        expected = {
            f"{prefix}_{level}"
            for prefix in ("7d", "d7_14", "d14_21", "d21_28", "d28_35", "35d")
            for level in ("top_100", "top_200", "top_500", "top_1000", "all")
        }

        self.assertEqual(set(DATASET_SCOPES), expected)
        self.assertEqual(len(DATASET_SCOPES), 30)

    def test_historical_week_slices_use_completed_at_offsets(self):
        self._accepted_batch("current", self.now - timedelta(days=1))
        self._accepted_batch("previous", self.now - timedelta(days=10))
        self.store.ingest_battle(
            "current",
            _battle("current-battle"),
            observer_tag="#CURRENT",
            observer_rank=50,
            observer_source="ranked_direct",
        )
        self.store.ingest_battle(
            "previous",
            _battle("previous-battle"),
            observer_tag="#PREVIOUS",
            observer_rank=50,
            observer_source="ranked_direct",
        )

        current_summary = self.store.dataset_summary("7d_top_100", now=self.now)
        previous_summary = self.store.dataset_summary("d7_14_top_100", now=self.now)

        self.assertEqual(self.store.scope_battle_ids("7d_all", now=self.now), ["current-battle"])
        self.assertEqual(self.store.scope_battle_ids("d7_14_all", now=self.now), ["previous-battle"])
        self.assertEqual(self.store.scope_battle_ids("35d_all", now=self.now), ["current-battle", "previous-battle"])
        self.assertEqual(current_summary["window_start_offset_days"], 0)
        self.assertEqual(previous_summary["window_start_offset_days"], 7)
        self.assertEqual(previous_summary["window_end_offset_days"], 14)
        self.assertEqual(previous_summary["window_kind"], "historical_slice")

    def test_legacy_batch_contributes_only_to_all_scopes(self):
        self._accepted_batch("legacy", self.now - timedelta(days=1), batch_type="legacy_weekly_full_only")
        self.store.ingest_battle(
            "legacy",
            _battle("legacy-battle"),
            observer_tag="#LEGACY",
            observer_rank=None,
            observer_source="legacy_full",
        )

        self.assertEqual(self.store.scope_battle_ids("7d_top_1000", now=self.now), [])
        self.assertEqual(self.store.scope_battle_ids("7d_all", now=self.now), ["legacy-battle"])

    def test_retention_keeps_every_weekly_batch_inside_the_35_day_window(self):
        for index in range(7):
            completed_at = self.now - timedelta(days=index * 6)
            batch_id = f"weekly-{index}"
            self._accepted_batch(batch_id, completed_at, batch_type="weekly_expanded")
            self.store.ingest_battle(
                batch_id,
                _battle(f"battle-{index}"),
                observer_tag=f"#P{index}",
                observer_rank=None,
                observer_source="opponent_expansion",
            )

        result = self.store.expire_and_prune(now=self.now)

        self.assertEqual(result["retained_weekly_batches"], 6)
        self.assertLessEqual(result["retained_daily_batches"], 35)
        self.assertEqual(result["expired_batch_ids"], ["weekly-6"])
        self.assertEqual(self.store.fact_count(), 6)

    def test_batch_gate_requires_complete_top_100_and_99_percent_top_1000(self):
        self.store.create_batch(
            "daily-gate",
            batch_type="daily_ranked",
            started_at=self.now - timedelta(hours=1),
            leaderboard_frozen_at=self.now - timedelta(hours=1),
        )
        for rank in range(1, 1001):
            status = "success" if rank <= 990 else "failed"
            self.store.record_player(
                "daily-gate",
                player_tag=f"#P{rank}",
                observer_rank=rank,
                observer_source="ranked_direct",
                request_status=status,
                attempts=1,
            )
        policy = BatchValidationPolicy(required_top_rank=100, ranked_player_target=1000, minimum_coverage=0.99)

        report = self.store.finalize_batch(
            "daily-gate",
            completed_at=self.now,
            policy=policy,
            request_count=1000,
            rate_limited=0,
            refresh_budget_exhausted=False,
            source_exhausted=False,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["top_rank_successes"], 100)
        self.assertEqual(report["ranked_successes"], 990)

    def test_one_hop_expansion_accepts_complete_source_exhaustion_below_legacy_target(self):
        self.store.create_batch(
            "expanded-complete-one-hop",
            batch_type="weekly_expanded",
            started_at=self.now - timedelta(hours=1),
            leaderboard_frozen_at=self.now - timedelta(hours=1),
        )
        for rank in range(1, 1001):
            self.store.record_player(
                "expanded-complete-one-hop",
                player_tag=f"#R{rank}",
                observer_rank=rank,
                observer_source="ranked_direct",
                request_status="success",
                attempts=1,
            )
        for index in range(1, 101):
            self.store.record_player(
                "expanded-complete-one-hop",
                player_tag=f"#E{index}",
                observer_rank=None,
                observer_source="opponent_expansion",
                request_status="success",
                attempts=1,
            )
        self.store.ingest_battle(
            "expanded-complete-one-hop",
            _battle("expanded-complete-one-hop-battle"),
            observer_tag="#R1",
            observer_rank=1,
            observer_source="ranked_direct",
        )

        report = self.store.finalize_batch(
            "expanded-complete-one-hop",
            completed_at=self.now,
            policy=BatchValidationPolicy(),
            request_count=1100,
            rate_limited=0,
            refresh_budget_exhausted=False,
            source_exhausted=True,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["expansion_successes"], 100)
        self.assertEqual(report["expansion_target"], 100)
        self.assertEqual(report["expansion_coverage"], 1.0)

    def test_one_hop_expansion_rejects_low_expansion_request_coverage(self):
        self.store.create_batch(
            "expanded-incomplete-one-hop",
            batch_type="weekly_expanded",
            started_at=self.now - timedelta(hours=1),
            leaderboard_frozen_at=self.now - timedelta(hours=1),
        )
        for rank in range(1, 1001):
            self.store.record_player(
                "expanded-incomplete-one-hop",
                player_tag=f"#R{rank}",
                observer_rank=rank,
                observer_source="ranked_direct",
                request_status="success",
                attempts=1,
            )
        for index in range(1, 101):
            self.store.record_player(
                "expanded-incomplete-one-hop",
                player_tag=f"#E{index}",
                observer_rank=None,
                observer_source="opponent_expansion",
                request_status="success" if index <= 98 else "failed",
                attempts=1,
            )
        self.store.ingest_battle(
            "expanded-incomplete-one-hop",
            _battle("expanded-incomplete-one-hop-battle"),
            observer_tag="#R1",
            observer_rank=1,
            observer_source="ranked_direct",
        )

        report = self.store.finalize_batch(
            "expanded-incomplete-one-hop",
            completed_at=self.now,
            policy=BatchValidationPolicy(),
            request_count=1100,
            rate_limited=0,
            refresh_budget_exhausted=False,
            source_exhausted=True,
        )

        self.assertFalse(report["passed"])
        self.assertIn("expansion_coverage_below_threshold", report["failures"])

    def test_rejected_batch_keeps_diagnostics_but_removes_formal_observations(self):
        self.store.create_batch(
            "daily-rejected",
            batch_type="daily_ranked",
            started_at=self.now - timedelta(hours=1),
            leaderboard_frozen_at=self.now - timedelta(hours=1),
        )
        self.store.ingest_battle(
            "daily-rejected",
            _battle("rejected-only"),
            observer_tag="#P1",
            observer_rank=1,
            observer_source="ranked_direct",
        )

        report = self.store.finalize_batch(
            "daily-rejected",
            completed_at=self.now,
            policy=BatchValidationPolicy(),
            request_count=1,
            rate_limited=0,
            refresh_budget_exhausted=False,
            source_exhausted=False,
        )

        self.assertFalse(report["passed"])
        self.assertEqual(self.store.observation_count(), 0)
        self.assertEqual(self.store.fact_count(), 0)
        batch = self.store.connection.execute(
            "SELECT status, validation_json FROM collection_batches WHERE batch_id='daily-rejected'"
        ).fetchone()
        self.assertEqual(batch["status"], "rejected")
        self.assertIn("ranked_coverage_below_threshold", batch["validation_json"])

    def test_legacy_archive_import_is_streamed_and_never_gains_ranked_membership(self):
        aggregate_path = Path(self.temp_dir.name) / "legacy.sqlite"
        source = sqlite3.connect(aggregate_path)
        source.execute(
            "CREATE TABLE battles(sequence INTEGER PRIMARY KEY AUTOINCREMENT, battle_id TEXT, payload TEXT)"
        )
        source.execute(
            "INSERT INTO battles(battle_id, payload) VALUES (?, ?)",
            ("legacy-1", json.dumps(_battle("legacy-1"), ensure_ascii=False)),
        )
        source.commit()
        source.close()

        result = self.store.import_legacy_archive(
            aggregate_path,
            batch_id="legacy-import",
            completed_at=self.now - timedelta(days=1),
        )

        self.assertTrue(result["passed"])
        self.assertEqual(self.store.scope_battle_ids("7d_all", now=self.now), ["legacy-1"])
        self.assertEqual(self.store.scope_battle_ids("7d_top_1000", now=self.now), [])

    def test_workspace_import_reports_unique_full_loadout_coverage(self):
        workspace_path = Path(self.temp_dir.name) / "workspace.sqlite"
        source = sqlite3.connect(workspace_path)
        source.executescript(
            """
            CREATE TABLE battles(sequence INTEGER PRIMARY KEY, battle_id TEXT UNIQUE, payload TEXT NOT NULL);
            CREATE TABLE battle_observations(
                battle_id TEXT, observer_tag TEXT, observer_rank INTEGER,
                observer_source TEXT, expansion_root_rank INTEGER
            );
            CREATE TABLE player_requests(
                player_tag TEXT, observer_rank INTEGER, observer_source TEXT,
                request_status TEXT, attempts INTEGER
            );
            """
        )
        record = _battle_with_loadouts("workspace-loadout")
        source.execute(
            "INSERT INTO battles VALUES (1, ?, ?)",
            (record["battle_id"], json.dumps(record)),
        )
        source.executemany(
            "INSERT INTO battle_observations VALUES (?, ?, ?, ?, ?)",
            [
                (record["battle_id"], "#P1", 1, "ranked_direct", None),
                (record["battle_id"], "#P2", 2, "ranked_direct", None),
            ],
        )
        source.execute(
            "INSERT INTO player_requests VALUES ('#P1', 1, 'ranked_direct', 'success', 1)"
        )
        source.commit()
        source.close()

        imported = self.store.import_workspace_batch(
            workspace_path,
            batch_id="weekly-loadout",
            batch_type="weekly_expanded",
            started_at=self.now,
            leaderboard_frozen_at=self.now,
            observed_at=self.now,
        )

        self.assertEqual(imported["observations_imported"], 2)
        self.assertEqual(imported["loadout_metadata_refreshes"], 0)
        self.assertEqual(imported["loadout_coverage"]["observed_battle_rows"], 1)
        self.assertEqual(imported["loadout_coverage"]["complete_battle_rows"], 1)
        self.assertEqual(imported["loadout_coverage"]["evolution_slots"], 1)
        self.assertEqual(imported["loadout_coverage"]["elite_slots"], 2)
        self.assertEqual(imported["loadout_coverage"]["unknown_special_slots"], 0)
        self.assertEqual(imported["loadout_coverage"]["slot_contract_failures"], 0)


if __name__ == "__main__":
    unittest.main()
