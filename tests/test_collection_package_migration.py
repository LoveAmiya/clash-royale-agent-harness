from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

import requests

import app_config  # noqa: F401 - initializes the src package path for root runs.
import battle_loadout
import rolling_corpus
import rolling_materializer
import scripts.collect_rolling_corpus as rolling_collection_script
import supercell_preflight
from clashroyale_agent.collection.api_client import OfficialAPIRequester
from clashroyale_agent.collection import battle_parser
from clashroyale_agent.collection import loadout_normalization
from clashroyale_agent.collection import preflight
from clashroyale_agent.collection import rolling_collector as packaged_rolling_collector
from clashroyale_agent.collection import rolling_corpus as packaged_rolling_corpus
from clashroyale_agent.collection import rolling_materializer as packaged_rolling_materializer
from supercell_live import SupercellAPIClient, normalize_battle_record, select_usable_battles


class CollectionPackageMigrationTests(unittest.TestCase):
    def test_legacy_module_is_packaged_module_alias(self) -> None:
        self.assertIs(supercell_preflight, preflight)

    def test_rolling_collection_script_is_packaged_module_alias(self) -> None:
        self.assertIs(rolling_collection_script, packaged_rolling_collector)
        self.assertIs(rolling_collection_script.collect, packaged_rolling_collector.collect)
        self.assertIs(rolling_collection_script.retry_failed_publication, packaged_rolling_collector.retry_failed_publication)

    def test_public_entrypoints_keep_identity(self) -> None:
        self.assertIs(supercell_preflight.run_preflight, preflight.run_preflight)
        self.assertIs(supercell_preflight.main, preflight.main)

    def test_supercell_client_uses_packaged_requester_base(self) -> None:
        self.assertTrue(issubclass(SupercellAPIClient, OfficialAPIRequester))

    def test_battle_parser_keeps_legacy_root_exports(self) -> None:
        self.assertIs(normalize_battle_record, battle_parser.normalize_battle_record)
        self.assertIs(select_usable_battles, battle_parser.select_usable_battles)

    def test_loadout_normalization_keeps_legacy_root_exports(self) -> None:
        self.assertIs(battle_loadout.normalize_side_loadout, loadout_normalization.normalize_side_loadout)
        self.assertIs(battle_loadout.full_loadout_signature, loadout_normalization.full_loadout_signature)
        self.assertEqual(battle_loadout.LOADOUT_SCHEMA_VERSION, loadout_normalization.LOADOUT_SCHEMA_VERSION)

    def test_rolling_modules_keep_legacy_root_exports(self) -> None:
        self.assertIs(rolling_corpus, packaged_rolling_corpus)
        self.assertIs(rolling_materializer, packaged_rolling_materializer)
        self.assertIs(rolling_corpus.RollingCorpusStore, packaged_rolling_corpus.RollingCorpusStore)
        self.assertIs(rolling_corpus.CorpusWriterLock, packaged_rolling_corpus.CorpusWriterLock)
        self.assertEqual(rolling_corpus.DATASET_SCOPES, packaged_rolling_corpus.DATASET_SCOPES)
        self.assertIs(rolling_materializer.build_snapshot_group, packaged_rolling_materializer.build_snapshot_group)

    def test_packaged_battle_parser_preserves_synthetic_selection_metrics(self) -> None:
        invalid = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010202.000Z",
            "team": [{"tag": "#A", "crowns": 0}],
            "opponent": [{"tag": "#B", "crowns": 1}],
        }
        complete = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 3, "cards": [{"name": f"A{i}"} for i in range(8)]}],
            "opponent": [{"tag": "#B", "crowns": 1, "cards": [{"name": f"B{i}"} for i in range(8)]}],
        }
        reversed_view = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#B", "crowns": 1, "cards": [{"name": f"B{i}"} for i in range(8)]}],
            "opponent": [{"tag": "#A", "crowns": 3, "cards": [{"name": f"A{i}"} for i in range(8)]}],
        }
        metrics: dict[str, int] = {}

        selected = battle_parser.select_usable_battles(
            [invalid, complete, reversed_view],
            limit=3,
            observer_tag="#A",
            selection_metrics=metrics,
            path_of_legend_only=True,
            require_complete_decks_and_stable_id=True,
        )

        self.assertEqual(selected, [complete])
        self.assertEqual(metrics["inspected_battle_records"], 3)
        self.assertEqual(metrics["deckless_or_invalid_records"], 1)
        self.assertEqual(metrics["duplicates_skipped"], 1)
        self.assertEqual(
            battle_parser.normalize_battle_record(complete, "#A")["battle_id"],
            battle_parser.normalize_battle_record(reversed_view, "#B")["battle_id"],
        )

    def test_packaged_requester_uses_bearer_token_and_timeout(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}
        session = Mock()
        session.get.return_value = response

        requester = OfficialAPIRequester(
            "test-token",
            base_url="https://example.test/v1",
            session=session,
            timeout_seconds=7.0,
        )

        self.assertEqual(requester.get_json("/players/%23ABC"), {"ok": True})
        self.assertEqual(session.get.call_args.args[0], "https://example.test/v1/players/%23ABC")
        self.assertEqual(session.get.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(session.get.call_args.kwargs["timeout"], 7.0)

    def test_packaged_requester_honors_retry_after_for_rate_limits(self) -> None:
        limited_response = Mock(status_code=429, headers={"Retry-After": "2"})
        limited_error = requests.HTTPError("rate limited")
        limited_error.response = limited_response
        success_response = Mock()
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = []
        session = Mock()
        session.get.side_effect = [limited_error, success_response]
        waits = []

        requester = OfficialAPIRequester(
            "test-token",
            session=session,
            max_retries=1,
            sleeper=waits.append,
        )

        self.assertEqual(requester.get_json("/players/%23ABC/battlelog"), [])
        self.assertEqual(waits, [2.0])
        self.assertEqual(requester.metrics["rate_limited"], 1)
        self.assertEqual(requester.metrics["retried_requests"], 1)

    def test_root_client_preserves_legacy_session_factory_patch_point(self) -> None:
        with patch("supercell_live.requests.Session") as session_factory:
            session = Mock()
            session_factory.return_value = session

            client = SupercellAPIClient("test-token")

        session_factory.assert_called_once_with()
        self.assertIs(client.session, session)


if __name__ == "__main__":
    unittest.main()
