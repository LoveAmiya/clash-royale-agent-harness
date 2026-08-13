import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from rolling_corpus import CorpusError
from scripts.collect_rolling_corpus import (
    CollectionStatusReporter,
    _collection_status_payload,
    _parse_api_tokens,
    _publish_snapshot_if_accepted,
    _prepare_lane_stage,
    _resolve_api_token,
    _validation_policy,
    retry_failed_publication,
)


class RollingCollectionValidationPolicyTests(unittest.TestCase):
    def test_daily_policy_uses_available_leaderboard_size(self):
        policy = _validation_policy("daily_ranked", {"seed_players": 123})

        self.assertEqual(policy.ranked_player_target, 123)
        self.assertEqual(policy.required_top_rank, 100)

    def test_daily_policy_supports_a_leaderboard_smaller_than_top_100(self):
        policy = _validation_policy("daily_ranked", {"seed_players": 37})

        self.assertEqual(policy.ranked_player_target, 37)
        self.assertEqual(policy.required_top_rank, 37)

    def test_empty_daily_leaderboard_keeps_strict_defaults_to_reject_empty_batch(self):
        policy = _validation_policy("daily_ranked", {"seed_players": 0})

        self.assertEqual(policy.ranked_player_target, 1000)
        self.assertEqual(policy.required_top_rank, 100)

    def test_weekly_policy_remains_strict(self):
        policy = _validation_policy("weekly_expanded", {"seed_players": 123})

        self.assertEqual(policy.ranked_player_target, 1000)
        self.assertEqual(policy.required_top_rank, 100)
        self.assertEqual(policy.weekly_target_battles, 200_000)


class RollingCollectionTokenTests(unittest.TestCase):
    def test_token_list_accepts_json_without_exposing_values(self):
        self.assertEqual(_parse_api_tokens('["core-token", "expanded-token"]'), ("core-token", "expanded-token"))

    def test_empty_json_token_list_allows_legacy_core_fallback(self):
        with patch.dict(os.environ, {"SUPERCELL_API_TOKENS": "[]", "SUPERCELL_API_TOKEN": "legacy"}, clear=False):
            self.assertEqual(_resolve_api_token("daily_ranked"), "legacy")

    def test_modes_use_separate_token_slots(self):
        with patch.dict(os.environ, {"SUPERCELL_API_TOKENS": "core-token;expanded-token"}, clear=False):
            self.assertEqual(_resolve_api_token("daily_ranked"), "core-token")
            self.assertEqual(_resolve_api_token("weekly_expanded"), "expanded-token")

    def test_expansion_requires_a_second_token(self):
        with patch.dict(os.environ, {"SUPERCELL_API_TOKENS": "only-token", "SUPERCELL_API_TOKEN": "only-token"}, clear=False):
            with self.assertRaisesRegex(CorpusError, "second token"):
                _resolve_api_token("weekly_expanded")

    def test_legacy_core_and_single_new_token_form_two_lanes(self):
        with patch.dict(
            os.environ,
            {"SUPERCELL_API_TOKEN": "core-token", "SUPERCELL_API_TOKENS": "expanded-token"},
            clear=False,
        ):
            self.assertEqual(_resolve_api_token("daily_ranked"), "core-token")
            self.assertEqual(_resolve_api_token("weekly_expanded"), "expanded-token")


class RollingCollectionLaneStageTests(unittest.TestCase):
    def test_resumed_status_exposes_trigger_and_effective_batch_ids(self):
        payload = _collection_status_payload(
            mode="daily_ranked",
            trigger_batch_id="daily_ranked-20260813-130937",
            effective_batch_id="daily_ranked-20260813-015901",
            resumed=True,
            stage="fetching",
            status="collecting",
        )

        self.assertEqual(payload["trigger_batch_id"], "daily_ranked-20260813-130937")
        self.assertEqual(payload["batch_id"], "daily_ranked-20260813-015901")
        self.assertTrue(payload["resumed"])
        self.assertEqual(payload["stage"], "fetching")
        self.assertIn("updated_at", payload)

    def test_status_reporter_refreshes_long_running_stage_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "collection_status.daily_ranked.json"
            reporter = CollectionStatusReporter(
                status_path,
                base_fields={
                    "schema_version": 1,
                    "collection_mode": "daily_ranked",
                    "trigger_batch_id": "trigger-1",
                    "batch_id": "effective-1",
                    "resumed": True,
                },
                heartbeat_interval_seconds=0.02,
            )

            with reporter.stage("publishing", status="processing"):
                first = json.loads(status_path.read_text(encoding="utf-8"))
                time.sleep(0.06)
                second = json.loads(status_path.read_text(encoding="utf-8"))

            self.assertEqual(second["stage"], "publishing")
            self.assertEqual(second["status"], "processing")
            self.assertGreater(second["heartbeat_sequence"], first["heartbeat_sequence"])
            self.assertNotEqual(second["updated_at"], first["updated_at"])

    def test_expansion_stage_resumes_the_same_batch_after_interruption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            now = datetime(2026, 8, 10, tzinfo=timezone.utc)

            first = _prepare_lane_stage(data_dir, "weekly_expanded", "expanded-1", now)
            second = _prepare_lane_stage(data_dir, "weekly_expanded", "expanded-2", now)

            self.assertEqual(first[0], "expanded-1")
            self.assertFalse(first[3])
            self.assertEqual(second[0], "expanded-1")
            self.assertTrue(second[3])
            self.assertEqual(first[1], second[1])

    def test_network_fetch_is_ordered_before_the_main_corpus_lock(self):
        source = Path(__file__).resolve().parents[1].joinpath("scripts", "collect_rolling_corpus.py").read_text(
            encoding="utf-8"
        )
        self.assertLess(source.index("snapshot = client.fetch_snapshot("), source.index("writer_lock = CorpusWriterLock("))

    def test_merge_wait_covers_long_snapshot_publication(self):
        source = Path(__file__).resolve().parents[1].joinpath("scripts", "collect_rolling_corpus.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("_MERGE_LOCK_WAIT_SECONDS = 2 * 60 * 60", source)


class RollingCollectionPublicationRepairTests(unittest.TestCase):
    def test_rejected_batch_does_not_rebuild_or_publish_a_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("scripts.collect_rolling_corpus.build_snapshot_group") as build:
                publication, publication_error = _publish_snapshot_if_accepted(
                    object(),
                    data_dir=Path(temp_dir),
                    now=datetime(2026, 8, 11, tzinfo=timezone.utc),
                    validation_report={"passed": False, "failures": ["conflicting_battle_facts"]},
                )

            self.assertIsNone(publication)
            self.assertIsNone(publication_error)
            build.assert_not_called()

    def test_repair_publishes_accepted_facts_without_overwriting_a_newer_lane_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            corpus_dir = data_dir / "corpus"
            corpus_dir.mkdir(parents=True)
            failed = {
                "schema_version": 1,
                "status": "accepted_publication_failed",
                "batch_id": "daily-failed",
                "collection_mode": "daily_ranked",
                "publication": None,
                "publication_error": {"error_type": "OperationalError", "message": "database or disk is full"},
                "cost_boundaries": {"cloud_llm_calls": 0, "cloud_embedding_calls": 0},
            }
            (corpus_dir / "collection_status.json").write_text(json.dumps(failed), encoding="utf-8")
            newer = {
                "schema_version": 1,
                "status": "collecting",
                "batch_id": "daily-newer",
                "collection_mode": "daily_ranked",
            }
            lane_status = corpus_dir / "collection_status.daily_ranked.json"
            lane_status.write_text(json.dumps(newer), encoding="utf-8")
            manifest = {
                "snapshot_group_id": "pol-repaired",
                "datasets": {"scope": {}},
                "fully_aligned": True,
            }

            with patch("scripts.collect_rolling_corpus.build_snapshot_group", return_value=manifest):
                result = retry_failed_publication(data_dir=data_dir)

            repaired = json.loads((corpus_dir / "collection_status.json").read_text(encoding="utf-8"))
            preserved_lane = json.loads(lane_status.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "published")
            self.assertEqual(repaired["status"], "accepted")
            self.assertEqual(repaired["publication"]["snapshot_group_id"], "pol-repaired")
            self.assertIsNone(repaired["publication_error"])
            self.assertEqual(preserved_lane, newer)

    def test_repair_is_a_noop_without_a_failed_accepted_publication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"

            result = retry_failed_publication(data_dir=data_dir)

            self.assertEqual(result["status"], "not_needed")


if __name__ == "__main__":
    unittest.main()
