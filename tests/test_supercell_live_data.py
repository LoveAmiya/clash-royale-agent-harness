import json
import unittest
import types
import requests
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
from unittest.mock import AsyncMock

from support import install_test_stubs

install_test_stubs()

from supercell_live import (
    DiskBackedSnapshotWorkspace,
    PATH_OF_LEGEND_COLLECTION_SCOPE,
    PATH_OF_LEGEND_SCOPE_CONTRACT,
    SupercellAPIClient,
    build_live_snapshot,
    is_path_of_legend_battle,
    normalize_battle_record,
    probe_official_special_fields,
    select_usable_battles,
)
import runtime_multi
from answer_builder import build_card_answer
from skills.meta_evidence import build_meta_evidence_pack
from query_answering import AnswerResult


class SupercellLiveDataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # These tests exercise the legacy single-snapshot compatibility path.
        # A developer's active rolling group must not change their routing.
        rolling_manifest = patch.object(runtime_multi, "_active_snapshot_group_manifest", return_value=None)
        rolling_manifest.start()
        self.addCleanup(rolling_manifest.stop)
        live_data_enabled = patch.object(runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True)
        live_data_enabled.start()
        self.addCleanup(live_data_enabled.stop)

    def test_path_of_legend_scope_accepts_only_the_official_battle_type(self):
        self.assertTrue(is_path_of_legend_battle({"type": "pathOfLegend"}))
        self.assertFalse(is_path_of_legend_battle({"type": "PvP"}))
        self.assertFalse(is_path_of_legend_battle({"type": "clanMate"}))
        self.assertFalse(is_path_of_legend_battle({}))

    def test_special_field_probe_observes_payload_without_enabling_special_deck_mode(self):
        battle = {
            "team": [{
                "towerTroop": {"name": "Tower Princess"},
                "cards": [{"name": "Knight", "evolutionLevel": 1}],
            }],
            "opponent": [{
                "cards": [{"name": "Archers", "eliteLevel": 1}],
            }],
        }

        probe = probe_official_special_fields([battle])

        self.assertTrue(probe["tower"]["available"])
        self.assertTrue(probe["evolution"]["available"])
        self.assertTrue(probe["elite"]["available"])
        self.assertEqual(probe["deck_mode"], "base8_and_full_loadout_v1")
        self.assertEqual(probe["available_deck_modes"], ["base8", "full_loadout"])

    def test_snapshot_keeps_normalized_raw_battles_and_aggregates_deck_matchups(self):
        battle_logs = {
            "#A": [
                {
                    "battleTime": "20260725T010203.000Z",
                    "team": [{"tag": "#A", "crowns": 3, "cards": [{"name": "Electro Giant"}, {"name": "Zap"}]}],
                    "opponent": [{"tag": "#B", "crowns": 1, "cards": [{"name": "Hog Rider"}, {"name": "Fireball"}]}],
                },
                {
                    "battleTime": "20260725T020304.000Z",
                    "team": [{"tag": "#A", "crowns": 0, "cards": [{"name": "Electro Giant"}, {"name": "Zap"}]}],
                    "opponent": [{"tag": "#C", "crowns": 1, "cards": [{"name": "Hog Rider"}, {"name": "Fireball"}]}],
                },
            ]
        }

        snapshot = build_live_snapshot([{"tag": "#A"}], battle_logs, target_battles=2)

        self.assertEqual(snapshot["sample_battles"], 2)
        self.assertEqual(len(snapshot["raw_battles"]), 2)
        self.assertEqual(snapshot["raw_battles"][0]["team_deck"], ["Electro Giant", "Zap"])
        matchup = snapshot["deck_matchups"][0]
        self.assertEqual(matchup["deck_name"], "Electro Giant / Zap")
        self.assertEqual(matchup["opponent_deck_name"], "Fireball / Hog Rider")
        self.assertEqual(matchup["games"], 2)
        self.assertEqual(matchup["wins"], 1)
        self.assertEqual(matchup["win_rate"], 50.0)
        self.assertEqual(snapshot["card_deck_stats"]["Electro Giant"][0]["battles"], 2)
        self.assertEqual(snapshot["card_deck_stats"]["Electro Giant"][0]["deck_name"], "Electro Giant / Zap")

    def test_snapshot_deduplicates_a_battle_seen_from_both_players(self):
        battle_from_a = {
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 3, "cards": [{"name": "Electro Giant"}]}],
            "opponent": [{"tag": "#B", "crowns": 1, "cards": [{"name": "Hog Rider"}]}],
        }
        battle_from_b = {
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#B", "crowns": 1, "cards": [{"name": "Hog Rider"}]}],
            "opponent": [{"tag": "#A", "crowns": 3, "cards": [{"name": "Electro Giant"}]}],
        }

        snapshot = build_live_snapshot(
            [{"tag": "#A"}, {"tag": "#B"}],
            {"#A": [battle_from_a], "#B": [battle_from_b]},
            target_battles=2,
        )

        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertEqual(snapshot["shortfall_battles"], 1)
        self.assertEqual(len(snapshot["raw_battles"]), 1)
        self.assertEqual(normalize_battle_record(battle_from_a, "#A")["battle_id"], normalize_battle_record(battle_from_b, "#B")["battle_id"])

    def test_normalized_battle_preserves_tower_evolution_and_elite_loadouts(self):
        def cards(prefix: str):
            result = []
            for index in range(8):
                card = {
                    "id": 26000000 + index,
                    "name": f"{prefix}{index}",
                    "level": 13,
                    "maxLevel": 14,
                    "starLevel": 3,
                }
                if index == 0:
                    card["evolutionLevel"] = 1
                if index == 1:
                    card["evolutionLevel"] = 2
                result.append(card)
            return result

        battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{
                "tag": "#A",
                "crowns": 3,
                "cards": cards("Team"),
                "supportCards": [{"id": 159000000, "name": "Tower Princess", "level": 15}],
            }],
            "opponent": [{
                "tag": "#B",
                "crowns": 1,
                "cards": cards("Opponent"),
                "supportCards": [{"id": 159000001, "name": "Cannoneer", "level": 14}],
            }],
        }

        record = normalize_battle_record(battle, "#A")

        self.assertEqual(record["loadout_schema_version"], 1)
        self.assertEqual(record["team_loadout"]["tower"]["id"], "159000000")
        self.assertEqual(len(record["team_loadout"]["cards"]), 8)
        self.assertEqual(record["team_loadout"]["cards"][0]["evolution_level"], 1)
        self.assertFalse(record["team_loadout"]["cards"][0]["elite"])
        self.assertTrue(record["team_loadout"]["cards"][1]["elite"])
        self.assertEqual(record["team_loadout"]["cards"][1]["elite_detection"], "official_evolution_level_v1")
        self.assertEqual(record["team_loadout"]["cards"][1]["special_mode"], "elite")
        self.assertTrue(record["team_loadout"]["complete"])

    def test_special_loadout_fields_do_not_change_stable_battle_id(self):
        base_cards = [{"id": 26000000 + index, "name": f"Card{index}"} for index in range(8)]
        base = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 3, "cards": base_cards}],
            "opponent": [{"tag": "#B", "crowns": 1, "cards": base_cards}],
        }
        enriched = json.loads(json.dumps(base))
        enriched["team"][0]["supportCards"] = [{"id": 159000000, "name": "Tower Princess"}]
        enriched["team"][0]["cards"][0]["evolutionLevel"] = 1
        enriched["team"][0]["cards"][1].update({"level": 15, "maxLevel": 14})

        self.assertEqual(
            normalize_battle_record(base, "#A")["battle_id"],
            normalize_battle_record(enriched, "#A")["battle_id"],
        )

    def test_deduplication_keeps_distinct_rematches_with_the_same_players_and_decks(self):
        first_battle = {
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 3, "cards": [{"name": "Electro Giant"}]}],
            "opponent": [{"tag": "#B", "crowns": 1, "cards": [{"name": "Hog Rider"}]}],
        }
        rematch = {**first_battle, "battleTime": "20260725T011203.000Z"}

        selected = select_usable_battles([first_battle, rematch], limit=2, observer_tag="#A")

        self.assertEqual(selected, [first_battle, rematch])
        self.assertNotEqual(
            normalize_battle_record(first_battle, "#A")["battle_id"],
            normalize_battle_record(rematch, "#A")["battle_id"],
        )

    def test_build_snapshot_derives_card_and_deck_metrics_from_battle_logs(self):
        battle_logs = {
            "#A": [
                {
                    "team": [{"crowns": 3, "cards": [{"name": "Electro Giant", "elixirCost": 7}, {"name": "Zap", "elixirCost": 2}]}],
                    "opponent": [{"crowns": 1}],
                },
                {
                    "team": [{"crowns": 0, "cards": [{"name": "Electro Giant", "elixirCost": 7}, {"name": "Zap", "elixirCost": 2}]}],
                    "opponent": [{"crowns": 1}],
                },
            ]
        }

        snapshot = build_live_snapshot([{"tag": "#A", "name": "Player"}], battle_logs, fetched_at="2026-07-24T00:00:00Z")

        electro_giant = next(card for card in snapshot["cards_meta"] if card["card_name"] == "Electro Giant")
        self.assertEqual(electro_giant["usage_rate"], 100.0)
        self.assertEqual(electro_giant["win_rate"], 50.0)
        self.assertEqual(snapshot["top_decks"][0]["battles"], 2)
        self.assertEqual(snapshot["top_decks"][0]["source"], "Supercell API live sample")

    def test_client_uses_bearer_token_and_encodes_player_tag(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = []
        session = Mock()
        session.get.return_value = response
        client = SupercellAPIClient("test-token", session=session)

        client.fetch_battle_log("#ABC")

        url = session.get.call_args.args[0]
        self.assertIn("%23ABC", url)
        self.assertEqual(session.get.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(session.get.call_args.kwargs["timeout"], 5.0)

    def test_client_defaults_to_pooled_requests_session(self):
        with patch("supercell_live.requests.Session") as session_factory:
            session = Mock()
            session_factory.return_value = session

            client = SupercellAPIClient("test-token")

        session_factory.assert_called_once_with()
        self.assertIs(client.session, session)

    def test_client_honors_retry_after_before_retrying_a_rate_limited_request(self):
        limited_response = Mock(status_code=429, headers={"Retry-After": "3"})
        limited_error = requests.HTTPError("rate limited")
        limited_error.response = limited_response
        success_response = Mock()
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = []
        session = Mock()
        session.get.side_effect = [limited_error, success_response]
        waits = []
        client = SupercellAPIClient("test-token", session=session, max_retries=1, sleeper=waits.append)

        self.assertEqual(client.fetch_battle_log("#ABC"), [])
        self.assertEqual(waits, [3.0])
        self.assertEqual(client.metrics["rate_limited"], 1)
        self.assertEqual(client.metrics["retried_requests"], 1)

    def test_snapshot_reuses_fresh_cached_player_battle_log(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {"type": "pathOfLegend", "team": [{"crowns": 1, "cards": [{"name": "Zap"}]}], "opponent": [{"crowns": 0}]}
        cache = {"#ABC": (0.0, [battle])}

        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#ABC"}]), patch.object(
            client, "fetch_battle_log"
        ) as fetch_battle_log, patch("supercell_live.time.monotonic", return_value=1.0):
            snapshot = client.fetch_snapshot(
                target_battles=1,
                player_limit=1,
                battle_log_cache=cache,
                battle_log_cache_ttl_seconds=60,
            )

        fetch_battle_log.assert_not_called()
        self.assertEqual(snapshot["collection_metrics"]["cache_hits"], 1)

    def test_snapshot_stops_at_refresh_budget_and_returns_a_shortfall(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {"type": "pathOfLegend", "team": [{"crowns": 1, "cards": [{"name": "Zap"}]}], "opponent": [{"crowns": 0}]}
        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#A"}, {"tag": "#B"}]), patch.object(
            client, "fetch_battle_log", return_value=[battle]
        ), patch("supercell_live.time.monotonic", side_effect=[0.0, 0.0, 0.0, 100.0, 100.0, 100.0]):
            snapshot = client.fetch_snapshot(target_battles=10, player_limit=2, concurrency=1, max_duration_seconds=30)

        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertTrue(snapshot["collection_metrics"]["refresh_budget_exhausted"])

    def test_client_uses_only_path_of_legend_rankings_for_seeds(self):
        path_response = Mock()
        path_response.raise_for_status.return_value = None
        path_response.json.return_value = {"items": [{"tag": "#ABC", "name": "Player"}]}
        session = Mock()
        session.get.return_value = path_response
        client = SupercellAPIClient("test-token", session=session)

        players = client.fetch_global_rankings(10)

        self.assertEqual(
            players,
            [{"tag": "#ABC", "name": "Player", "rank": 1, "seed_source": "global_path_of_legend"}],
        )
        self.assertEqual(session.get.call_count, 1)
        self.assertIn("/locations/global/pathoflegend/players", session.get.call_args.args[0])
        self.assertNotIn("/rankings/players", session.get.call_args.args[0])

    def test_client_merges_global_path_and_location_rankings_as_unique_seeds(self):
        client = SupercellAPIClient("test-token", session=Mock())
        responses = [
            {"items": [{"tag": "#A", "rank": 1}, {"tag": "#B", "rank": 2}]},
            {"items": [{"id": 57000001}, {"id": "global"}]},
            {"items": [{"tag": "#B", "rank": 1}, {"tag": "#C", "rank": 2}]},
        ]

        with patch.object(client, "_get_json", side_effect=responses) as get_json:
            players = client.fetch_global_rankings(5, include_locations=True, location_limit=1)

        self.assertEqual([player["tag"] for player in players], ["#A", "#B", "#C"])
        self.assertEqual(players[0]["seed_source"], "global_path_of_legend")
        self.assertEqual(players[2]["seed_source"], "location_path_of_legend")
        self.assertIn("/locations/57000001/pathoflegend/players", get_json.call_args_list[2].args[0])

    def test_global_rankings_are_paginated_in_rank_order_until_candidate_limit(self):
        client = SupercellAPIClient("test-token", session=Mock())
        first_page = {
            "items": [{"tag": "#A", "rank": 1}, {"tag": "#B", "rank": 2}],
            "paging": {"cursors": {"after": "cursor-2"}},
        }
        second_page = {
            "items": [{"tag": "#C", "rank": 3}, {"tag": "#D", "rank": 4}],
            "paging": {"cursors": {}},
        }

        with patch.object(client, "_get_json", side_effect=[first_page, second_page]) as get_json:
            players = client.fetch_global_rankings(3)

        self.assertEqual([player["tag"] for player in players], ["#A", "#B", "#C"])
        self.assertEqual(get_json.call_args_list[0].kwargs["params"], {"limit": 3})
        self.assertEqual(get_json.call_args_list[1].kwargs["params"], {"limit": 1, "after": "cursor-2"})

    def test_snapshot_scans_leaderboard_from_rank_one_and_stops_after_target(self):
        client = SupercellAPIClient("test-token", session=Mock())

        def battle(card_name):
            return {
                "type": "pathOfLegend",
                "battleTime": f"20260725T01020{card_name[-1]}.000Z",
                "team": [{"crowns": 1, "cards": [{"name": card_name}]}],
                "opponent": [{"crowns": 0}],
            }

        players = [
            {"tag": "#A", "rank": 1},
            {"tag": "#B", "rank": 2},
            {"tag": "#C", "rank": 3},
        ]
        logs = {"#A": [battle("Card1")], "#B": [battle("Card2")], "#C": [battle("Card3")]}

        with patch.object(client, "fetch_global_rankings", return_value=players), patch.object(
            client, "fetch_battle_log", side_effect=lambda tag: logs[tag]
        ) as fetch_battle_log:
            snapshot = client.fetch_snapshot(target_battles=2, player_limit=3, battles_per_player=1, concurrency=1)

        self.assertEqual([call.args[0] for call in fetch_battle_log.call_args_list], ["#A", "#B"])
        self.assertEqual(snapshot["leaderboard_start_rank"], 1)
        self.assertEqual(snapshot["leaderboard_last_scanned_rank"], 2)
        self.assertEqual(snapshot["leaderboard_candidate_limit"], 3)
        self.assertEqual(snapshot["collection_metrics"]["player_queue_capacity"], 3)

    def test_snapshot_reports_local_progress_without_model_calls(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"crowns": 0}],
        }
        progress = []
        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#A", "rank": 1}]), patch.object(
            client, "fetch_battle_log", return_value=[battle]
        ):
            client.fetch_snapshot(
                target_battles=1,
                player_limit=1,
                concurrency=1,
                progress_callback=progress.append,
                progress_interval_seconds=0,
            )

        self.assertTrue(progress)
        self.assertEqual(progress[-1]["status"], "complete")
        self.assertEqual(progress[-1]["usable_battles"], 1)
        self.assertEqual(progress[-1]["target_battles"], 1)

    def test_snapshot_status_exposes_source_and_leaderboard_coverage(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot={
                    "snapshot_id": "official-20260725",
                    "fetched_at": "2026-07-25T00:00:00+00:00",
                    "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
                    "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
                    "shortfall_battles": 0,
                    "ranked_players": 3000,
                    "fetched_players": 822,
                    "sampled_players": 800,
                    "failed_players": 2,
                    "leaderboard_start_rank": 1,
                    "leaderboard_last_scanned_rank": 822,
                    "leaderboard_candidate_limit": 3000,
                    "collection_metrics": {"duplicates_skipped": 117},
                },
                live_refresh_status="ready",
                live_error=None,
                live_cooldown_until=0.0,
            )
        )

        status = runtime_multi.get_live_snapshot_status(app)

        self.assertEqual(status["source"], "Supercell Official API")
        self.assertEqual(status["snapshot_id"], "official-20260725")
        self.assertEqual(status["collection_scope"], "legacy_mixed_or_unverified")
        self.assertFalse(status["scope_verified"])
        self.assertEqual(status["leaderboard"]["candidate_limit"], 3000)
        self.assertEqual(status["leaderboard"]["scanned_rank_end"], 822)
        self.assertEqual(status["collection_metrics"]["duplicates_skipped"], 117)
        self.assertEqual(status["data_sources"]["schedule"], "disabled_clan_war_feature")
        self.assertEqual(status["data_sources"]["cards"], "official_weekly_snapshot")
        self.assertEqual(status["data_sources"]["rag_documents"], "official_weekly_snapshot")
        self.assertEqual(status["retention"], {"days": 14, "max_complete_snapshots": 2})

    def test_snapshot_uses_configured_player_tags_when_rankings_are_empty(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {"type": "pathOfLegend", "team": [{"crowns": 1, "cards": [{"name": "Zap"}]}], "opponent": [{"crowns": 0}]}

        with patch.object(client, "fetch_global_rankings", return_value=[]), patch.object(
            client, "fetch_battle_log", return_value=[battle]
        ) as fetch_battle_log:
            snapshot = client.fetch_snapshot(player_limit=10, battles_per_player=1, fallback_player_tags=("#ABC",))

        self.assertEqual(snapshot["sample_battles"], 1)
        fetch_battle_log.assert_called_once_with("#ABC")

    def test_snapshot_collects_until_target_and_does_not_fetch_unneeded_players(self):
        client = SupercellAPIClient("test-token", session=Mock())

        def battle(card_name):
            return {
                "type": "pathOfLegend",
                "team": [{"crowns": 1, "cards": [{"name": card_name}]}],
                "opponent": [{"crowns": 0}],
            }

        players = [{"tag": "#A"}, {"tag": "#B"}, {"tag": "#C"}]
        logs = {
            "#A": [battle("Electro Giant"), battle("Zap"), battle("Zap")],
            "#B": [battle("Electro Giant"), battle("Fireball"), battle("Fireball")],
            "#C": [battle("Unused Card")],
        }
        with patch.object(client, "fetch_global_rankings", return_value=players), patch.object(
            client, "fetch_battle_log", side_effect=lambda tag: logs[tag]
        ) as fetch_battle_log:
            snapshot = client.fetch_snapshot(
                target_battles=4,
                player_limit=3,
                battles_per_player=3,
                concurrency=1,
            )

        self.assertEqual(snapshot["sample_battles"], 4)
        self.assertEqual(snapshot["target_battles"], 4)
        self.assertEqual(snapshot["sampled_players"], 2)
        self.assertEqual(fetch_battle_log.call_args_list[-1].args[0], "#B")
        self.assertNotIn("#C", [call.args[0] for call in fetch_battle_log.call_args_list])
        electro_giant = next(card for card in snapshot["cards_meta"] if card["card_name"] == "Electro Giant")
        self.assertEqual(electro_giant["appearance_count"], 2)

    def test_snapshot_expands_exactly_one_layer_from_ranked_players(self):
        client = SupercellAPIClient("test-token", session=Mock())
        first_battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }
        second_battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010204.000Z",
            "team": [{"tag": "#B", "crowns": 1, "cards": [{"name": "Knight"}]}],
            "opponent": [{"tag": "#C", "crowns": 0, "cards": [{"name": "Archers"}]}],
        }

        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#A"}]), patch.object(
            client, "fetch_battle_log", side_effect=lambda tag: {"#A": [first_battle], "#B": [second_battle]}[tag]
        ) as fetch_battle_log:
            snapshot = client.fetch_snapshot(
                target_battles=3,
                player_limit=3,
                battles_per_player=1,
                concurrency=1,
            )

        self.assertEqual([call.args[0] for call in fetch_battle_log.call_args_list], ["#A", "#B"])
        self.assertEqual(snapshot["sample_battles"], 2)
        self.assertEqual(snapshot["collection_metrics"]["seed_players"], 1)
        self.assertEqual(snapshot["collection_metrics"]["expanded_players"], 1)
        self.assertEqual(snapshot["collection_metrics"]["queued_players"], 2)
        self.assertTrue(snapshot["collection_metrics"]["source_exhausted"])

    def test_disk_workspace_rejects_growth_past_its_byte_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "workspace byte limit"):
                DiskBackedSnapshotWorkspace(
                    Path(temp_dir),
                    target_battles=1,
                    player_limit=1,
                    battles_per_player=1,
                    max_workspace_bytes=1,
                )

    def test_daily_ranked_snapshot_never_expands_to_opponents(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {
            "type": "pathOfLegend",
            "battleTime": "20260730T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }

        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#A", "rank": 1}]), patch.object(
            client, "fetch_battle_log", return_value=[battle]
        ) as fetch_battle_log:
            snapshot = client.fetch_snapshot(
                target_battles=25,
                player_limit=1,
                seed_player_limit=1,
                battles_per_player=25,
                concurrency=1,
                collection_mode="daily_ranked",
                expand_opponents=False,
            )

        fetch_battle_log.assert_called_once_with("#A")
        self.assertEqual(snapshot["collection_metrics"]["collection_mode"], "daily_ranked")
        self.assertEqual(snapshot["collection_metrics"]["expanded_players"], 0)

    def test_ranked_tail_retry_recovers_a_transient_direct_player_failure(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            client, "fetch_global_rankings", return_value=[{"tag": "#A", "rank": 1}]
        ), patch.object(
            client,
            "fetch_battle_log",
            side_effect=[requests.ConnectionError("temporary"), [battle]],
        ) as fetch_log:
            snapshot = client.fetch_snapshot(
                target_battles=1,
                player_limit=1,
                seed_player_limit=1,
                battles_per_player=1,
                concurrency=1,
                collection_mode="daily_ranked",
                expand_opponents=False,
                ranked_tail_retry_rounds=1,
                spool_dir=Path(temp_dir),
            )

        self.assertEqual(fetch_log.call_count, 2)
        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertEqual(snapshot["failed_players"], 0)

    def test_disk_workspace_preserves_duplicate_battle_observers(self):
        client = SupercellAPIClient("test-token", session=Mock())
        direct = {
            "type": "pathOfLegend",
            "battleTime": "20260730T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }
        reversed_view = {
            "type": "pathOfLegend",
            "battleTime": "20260730T010203.000Z",
            "team": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Fireball"}]}],
            "opponent": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            client,
            "fetch_global_rankings",
            return_value=[{"tag": "#A", "rank": 1}, {"tag": "#B", "rank": 2}],
        ), patch.object(client, "fetch_battle_log", side_effect=lambda tag: {"#A": [direct], "#B": [reversed_view]}[tag]):
            snapshot = client.fetch_snapshot(
                target_battles=2,
                player_limit=2,
                seed_player_limit=2,
                battles_per_player=1,
                concurrency=1,
                expand_opponents=False,
                spool_dir=Path(temp_dir),
            )
            database = sqlite3.connect(snapshot["_aggregate_store_path"])
            observations = database.execute(
                "SELECT observer_tag, observer_rank, observer_source FROM battle_observations ORDER BY observer_rank"
            ).fetchall()
            database.close()

        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertEqual(observations, [("#A", 1, "ranked_direct"), ("#B", 2, "ranked_direct")])

    def test_snapshot_discards_non_path_of_legend_battles_before_tag_expansion(self):
        client = SupercellAPIClient("test-token", session=Mock())
        non_path_battle = {
            "type": "clanMate",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#BAD", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }
        path_battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010204.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Knight"}]}],
            "opponent": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Archers"}]}],
        }
        expanded_path_battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010205.000Z",
            "team": [{"tag": "#B", "crowns": 1, "cards": [{"name": "Arrows"}]}],
            "opponent": [{"tag": "#C", "crowns": 0, "cards": [{"name": "Bats"}]}],
        }
        logs = {"#A": [non_path_battle, path_battle], "#B": [expanded_path_battle]}

        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#A"}]), patch.object(
            client, "fetch_battle_log", side_effect=lambda tag: logs[tag]
        ) as fetch_battle_log:
            snapshot = client.fetch_snapshot(
                target_battles=2,
                player_limit=3,
                seed_player_limit=1,
                battles_per_player=1,
                concurrency=1,
            )

        self.assertEqual([call.args[0] for call in fetch_battle_log.call_args_list], ["#A", "#B"])
        self.assertEqual(snapshot["sample_battles"], 2)
        self.assertEqual(snapshot["collection_scope"], "path_of_legend")
        self.assertEqual(snapshot["collection_metrics"]["non_path_of_legend_records"], 1)
        self.assertEqual(snapshot["collection_metrics"]["seed_player_limit"], 1)

    def test_snapshot_marks_source_exhausted_when_queue_runs_dry_before_target(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"crowns": 0, "cards": [{"name": "Fireball"}]}],
        }

        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#A"}]), patch.object(
            client, "fetch_battle_log", return_value=[battle]
        ):
            snapshot = client.fetch_snapshot(
                target_battles=2,
                player_limit=2,
                battles_per_player=1,
                concurrency=1,
            )

        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertTrue(snapshot["collection_metrics"]["source_exhausted"])

    def test_snapshot_streams_normalized_battles_to_a_resumable_disk_workspace(self):
        client = SupercellAPIClient("test-token", session=Mock())
        players = [{"tag": "#A", "rank": 1}, {"tag": "#B", "rank": 2}]
        logs = {
            "#A": [
                {
                    "type": "pathOfLegend",
                    "battleTime": "20260725T010203.000Z",
                    "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
                    "opponent": [{"tag": "#X", "crowns": 0, "cards": [{"name": "Fireball"}]}],
                }
            ],
            "#B": [
                {
                    "type": "pathOfLegend",
                    "battleTime": "20260725T010204.000Z",
                    "team": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Fireball"}]}],
                    "opponent": [{"tag": "#Y", "crowns": 1, "cards": [{"name": "Zap"}]}],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            client, "fetch_global_rankings", return_value=players
        ), patch.object(client, "fetch_battle_log", side_effect=lambda tag: logs[tag]):
            snapshot = client.fetch_snapshot(
                target_battles=2,
                player_limit=2,
                battles_per_player=1,
                concurrency=1,
                spool_dir=Path(temp_dir),
            )

            self.assertNotIsInstance(snapshot["raw_battles"], list)
            self.assertEqual(len(snapshot["raw_battles"]), 2)
            self.assertEqual([record["battle_id"] for record in snapshot["raw_battles"]], [
                normalize_battle_record(logs["#A"][0], "#A")["battle_id"],
                normalize_battle_record(logs["#B"][0], "#B")["battle_id"],
            ])
            self.assertTrue(Path(snapshot["_aggregate_store_path"]).is_file())
            self.assertTrue(snapshot["collection_metrics"]["streamed_to_disk"])
            self.assertLessEqual(snapshot["collection_metrics"]["max_in_memory_battle_records"], 1)

    def test_snapshot_resumes_after_the_last_transactionally_completed_player(self):
        players = [{"tag": "#A", "rank": 1}, {"tag": "#B", "rank": 2}]
        first_battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#X", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }
        second_battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010204.000Z",
            "team": [{"tag": "#B", "crowns": 1, "cards": [{"name": "Fireball"}]}],
            "opponent": [{"tag": "#Y", "crowns": 0, "cards": [{"name": "Zap"}]}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            spool_dir = Path(temp_dir)
            workspace = DiskBackedSnapshotWorkspace(
                spool_dir,
                target_battles=2,
                player_limit=2,
                battles_per_player=1,
            )
            workspace.save_players(players)
            workspace.record_player(
                player_index=0,
                player_tag="#A",
                battles=[first_battle],
                failed=False,
                target_battles=2,
            )
            workspace.close()

            client = SupercellAPIClient("test-token", session=Mock())
            with patch.object(client, "fetch_global_rankings") as fetch_rankings, patch.object(
                client, "fetch_battle_log", return_value=[second_battle]
            ) as fetch_battle_log:
                snapshot = client.fetch_snapshot(
                    target_battles=2,
                    player_limit=2,
                    battles_per_player=1,
                    concurrency=1,
                    spool_dir=spool_dir,
                )

            fetch_rankings.assert_not_called()
            fetch_battle_log.assert_called_once_with("#B")
            self.assertEqual(snapshot["sample_battles"], 2)
            self.assertEqual(snapshot["fetched_players"], 2)
            self.assertEqual(snapshot["sampled_players"], 2)

    def test_corrupt_resume_roster_discards_unpublished_workspace_before_restarting(self):
        old_battle = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#X", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }
        new_players = [{"tag": "#C", "rank": 1}, {"tag": "#D", "rank": 2}]

        def new_battle(tag, second):
            return {
                "type": "pathOfLegend",
                "battleTime": f"20260725T01020{second}.000Z",
                "team": [{"tag": tag, "crowns": 1, "cards": [{"name": "Knight"}]}],
                "opponent": [{"tag": f"#O{second}", "crowns": 0, "cards": [{"name": "Archers"}]}],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            spool_dir = Path(temp_dir)
            workspace = DiskBackedSnapshotWorkspace(
                spool_dir,
                target_battles=2,
                player_limit=2,
                battles_per_player=1,
            )
            workspace.save_players([{"tag": "#A"}, {"tag": "#B"}])
            workspace.record_player(
                player_index=0,
                player_tag="#A",
                battles=[old_battle],
                failed=False,
                target_battles=2,
            )
            workspace.players_path.unlink()
            workspace.close()

            logs = {"#C": [new_battle("#C", 4)], "#D": [new_battle("#D", 5)]}
            client = SupercellAPIClient("test-token", session=Mock())
            with patch.object(client, "fetch_global_rankings", return_value=new_players), patch.object(
                client, "fetch_battle_log", side_effect=lambda tag: logs[tag]
            ):
                snapshot = client.fetch_snapshot(
                    target_battles=2,
                    player_limit=2,
                    battles_per_player=1,
                    concurrency=1,
                    spool_dir=spool_dir,
                )

            self.assertEqual(snapshot["sample_battles"], 2)
            self.assertEqual(
                [record["battle_id"] for record in snapshot["raw_battles"]],
                [
                    normalize_battle_record(logs["#C"][0], "#C")["battle_id"],
                    normalize_battle_record(logs["#D"][0], "#D")["battle_id"],
                ],
            )

    def test_streaming_collection_stops_immediately_when_rate_limited(self):
        client = SupercellAPIClient("test-token", session=Mock())
        players = [{"tag": "#A", "rank": 1}, {"tag": "#B", "rank": 2}]
        limited_response = Mock(status_code=429, headers={})
        limited_error = requests.HTTPError("rate limited")
        limited_error.response = limited_response

        def rate_limited(_tag):
            client.metrics["rate_limited"] += 1
            raise limited_error

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            client, "fetch_global_rankings", return_value=players
        ), patch.object(client, "fetch_battle_log", side_effect=rate_limited) as fetch_battle_log:
            with self.assertRaisesRegex(ValueError, "no usable battle-log decks"):
                client.fetch_snapshot(
                    target_battles=2,
                    player_limit=2,
                    battles_per_player=1,
                    concurrency=1,
                    spool_dir=Path(temp_dir),
                )

        fetch_battle_log.assert_called_once_with("#A")

    def test_snapshot_reports_shortfall_without_hiding_card_win_rate(self):
        battle = {
            "team": [{"crowns": 1, "cards": [{"name": "Electro Giant"}]}],
            "opponent": [{"crowns": 0}],
        }
        snapshot = build_live_snapshot(
            [{"tag": "#A"}],
            {"#A": [battle]},
            target_battles=400,
            collection_metadata={"ranked_players": 1, "sampled_players": 1, "failed_players": 0},
        )

        card = snapshot["cards_meta"][0]
        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertEqual(snapshot["target_battles"], 400)
        self.assertEqual(snapshot["shortfall_battles"], 399)
        self.assertEqual(card["appearance_count"], 1)
        self.assertEqual(card["win_rate"], 100.0)

    def test_client_treats_optional_path_of_legends_404_as_empty_rankings(self):
        global_response = Mock()
        global_response.raise_for_status.return_value = None
        global_response.json.return_value = {"items": []}
        error_response = Mock(status_code=404)
        error = requests.HTTPError("not found")
        error.response = error_response
        session = Mock()
        session.get.side_effect = [global_response, error]
        client = SupercellAPIClient("test-token", session=session)

        with patch.object(client, "_get_json", side_effect=[{"items": []}, error]):
            self.assertEqual(client.fetch_global_rankings(10), [])

    def test_select_usable_battles_skips_recent_entries_without_decks(self):
        missing_decks = {"team": [{"crowns": 1}], "opponent": [{"crowns": 0}]}
        usable = {"team": [{"crowns": 1, "cards": [{"name": "Electro Giant"}]}], "opponent": [{"crowns": 0}]}

        selected = select_usable_battles([missing_decks, missing_decks, usable], limit=1)

        self.assertEqual(selected, [usable])

    def test_strict_rolling_contract_requires_stable_id_and_exact_eight_cards(self):
        incomplete = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010203.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": "Zap"}]}],
            "opponent": [{"tag": "#B", "crowns": 0, "cards": [{"name": "Fireball"}]}],
        }
        complete = {
            "type": "pathOfLegend",
            "battleTime": "20260725T010204.000Z",
            "team": [{"tag": "#A", "crowns": 1, "cards": [{"name": f"A{i}"} for i in range(8)]}],
            "opponent": [{"tag": "#B", "crowns": 0, "cards": [{"name": f"B{i}"} for i in range(8)]}],
        }

        selected = select_usable_battles(
            [incomplete, complete],
            limit=2,
            observer_tag="#A",
            path_of_legend_only=True,
            require_complete_decks_and_stable_id=True,
        )

        self.assertEqual(selected, [complete])

    def test_empty_live_snapshot_reports_safe_record_counts(self):
        battle_logs = {"#A": [{"team": [{"crowns": 1}], "opponent": [{"crowns": 0}]}]}

        with self.assertRaisesRegex(ValueError, r"players=1, battle_records=1, deck_records=0"):
            build_live_snapshot([{"tag": "#A"}], battle_logs)

    def test_build_snapshot_accepts_deck_alias_and_single_team_object(self):
        battle_logs = {
            "#A": [
                {
                    "team": {"crowns": 2, "deck": ["Electro Giant", "Zap"]},
                    "opponent": {"crowns": 1},
                }
            ]
        }

        snapshot = build_live_snapshot([{"tag": "#A"}], battle_logs)

        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertEqual(snapshot["cards_meta"][0]["card_name"], "Electro Giant")

    def test_live_card_answer_labels_the_limited_supercell_sample(self):
        answer = build_card_answer(
            {
                "intent": "card_query",
                "card_name": "Electro Giant",
                "metrics": ["usage_rate", "win_rate"],
                "rank": None,
                "top_n": None,
            },
            [
                {
                    "card_name": "Electro Giant",
                    "usage_rate": 12.5,
                    "win_rate": 52.0,
                    "source": "Supercell API live sample",
                    "fetched_at": "2026-07-24T00:00:00Z",
                    "sample_battles": 24,
                    "appearance_count": 2,
                }
            ],
        )

        self.assertIn("Supercell API live sample", answer)
        self.assertIn("并非全球完整环境统计", answer)
        self.assertIn("样本出场：2 次", answer)
        self.assertNotIn("cards_meta.json", answer)

    def test_rolling_card_answer_labels_the_selected_official_scope(self):
        answer = build_card_answer(
            {
                "intent": "card_query",
                "card_name": "Hog Rider",
                "metrics": ["usage_rate", "win_rate"],
                "rank": None,
                "top_n": None,
            },
            [
                {
                    "card_name": "Hog Rider",
                    "usage_rate": 10.895854,
                    "win_rate": 49.534543,
                    "source": "Supercell API rolling Path of Legend corpus",
                    "dataset_scope": "7d_all",
                    "sample_battles": 937843,
                    "appearance_count": 204376,
                }
            ],
        )

        self.assertIn("Supercell API rolling Path of Legend corpus", answer)
        self.assertIn("7d_all", answer)
        self.assertIn("937843", answer)
        self.assertNotIn("静态快照", answer)
        self.assertNotIn("cards_meta.json", answer)

    def test_live_named_card_missing_from_sample_does_not_fall_back_to_ranking(self):
        answer = build_card_answer(
            {
                "intent": "card_query",
                "card_name": "Electro Giant",
                "metrics": ["usage_rate", "win_rate"],
                "rank": None,
                "top_n": None,
            },
            [
                {
                    "rank": 1,
                    "card_name": "Zap",
                    "source": "Supercell API live sample",
                    "sample_battles": 400,
                    "target_battles": 400,
                }
            ],
        )

        self.assertIn("Electro Giant", answer)
        self.assertIn("Supercell API", answer)
        self.assertIn("0/400", answer)
        self.assertNotIn("Zap", answer)

    def test_live_cards_keep_static_cards_that_are_absent_from_the_sample(self):
        live_cards = [{"card_name": "Zap", "source": "Supercell API live sample"}]
        static_cards = [
            {"card_name": "Zap", "source": "April snapshot"},
            {"card_name": "Electro Giant", "source": "April snapshot"},
        ]

        merged = runtime_multi.merge_live_card_snapshot(live_cards, static_cards)

        self.assertEqual([card["card_name"] for card in merged], ["Zap", "Electro Giant"])
        self.assertEqual(merged[1]["source"], "April snapshot")

    def test_live_card_ranking_uses_live_source_reference(self):
        answer = build_card_answer(
            {"intent": "card_query", "metric": "usage_rate", "rank": 1, "top_n": None},
            [{"rank": 1, "card_name": "Zap", "usage_rate": 25, "win_rate": 60, "clean_win_rate": 60, "source": "Supercell API live sample"}],
        )

        self.assertIn("Supercell API live sample", answer)
        self.assertNotIn("cards_meta.json", answer)

    def test_live_ranking_excludes_static_cards_marked_for_named_card_fallback(self):
        answer = build_card_answer(
            {"intent": "card_query", "metric": "usage_rate", "rank": None, "top_n": 1},
            [
                {"rank": 1, "card_name": "Zap", "usage_rate": 25, "win_rate": 60, "clean_win_rate": 60, "source": "Supercell API live sample"},
                {
                    "rank": 1,
                    "card_name": "Tower Princess",
                    "usage_rate": 99,
                    "win_rate": 60,
                    "clean_win_rate": 60,
                    "source": "April snapshot",
                    "_fallback_only": True,
                },
            ],
        )

        self.assertIn("Zap", answer)
        self.assertNotIn("Tower Princess", answer)

    def test_live_meta_evidence_excludes_named_card_fallback_records(self):
        evidence, _ = build_meta_evidence_pack(
            [],
            [],
            [
                {"rank": 1, "card_name": "Zap", "usage_rate": 25, "win_rate": 60, "clean_win_rate": 60},
                {"rank": 1, "card_name": "Tower Princess", "usage_rate": 99, "_fallback_only": True},
            ],
        )

        self.assertIn("Zap", evidence)
        self.assertNotIn("Tower Princess", evidence)

    def test_runtime_caches_a_successful_official_snapshot(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(live_snapshot=None, live_snapshot_at=0.0, live_error=None))
        snapshot = {
            "cards_meta": [{"card_name": "Electro Giant"}],
            "top_decks": [{"deck_name": "Deck"}],
            "fetched_at": "2099-01-01T00:00:00+00:00",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "collection_scope": PATH_OF_LEGEND_COLLECTION_SCOPE,
            "scope_contract": PATH_OF_LEGEND_SCOPE_CONTRACT,
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "RUNTIME_ROLE", "collector"
        ), patch.object(
            runtime_multi, "SupercellAPIClient"
        ) as client_class, patch.object(runtime_multi, "publish_daily_snapshot", side_effect=lambda value, *_: value):
            client_class.return_value.fetch_snapshot.return_value = snapshot

            self.assertEqual(runtime_multi.ensure_live_snapshot(app), snapshot)
            self.assertEqual(runtime_multi.ensure_live_snapshot(app), snapshot)

        client_class.return_value.fetch_snapshot.assert_called_once()

    def test_runtime_restores_a_complete_published_snapshot_before_calling_the_api(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
                cards_meta_data=[],
                top_decks_data=[],
            )
        )
        snapshot = {
            "snapshot_id": "official-yesterday",
            "fetched_at": "2026-07-23T00:00:00+00:00",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "cards_meta": [{"card_name": "Zap", "source": "Supercell API live sample"}],
            "top_decks": [{"deck_name": "Deck", "source": "Supercell API live sample"}],
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }

        with patch.object(runtime_multi, "load_published_snapshot", return_value=snapshot):
            restored = runtime_multi.restore_published_snapshot(app)

        self.assertEqual(restored["snapshot_id"], "official-yesterday")
        self.assertEqual(app.state.live_snapshot["snapshot_id"], "official-yesterday")
        self.assertEqual(app.state.cards_meta_data[0]["card_name"], "Zap")
        self.assertEqual(app.state.live_last_refresh_attempt["status"], "restored")
        self.assertEqual(app.state.live_last_refresh_attempt["sample_battles"], runtime_multi.DAILY_TARGET_BATTLES)

    def test_collector_restart_loads_only_the_compact_snapshot_summary(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
                cards_meta_data=[],
                top_decks_data=[],
            )
        )
        summary = {
            "snapshot_id": "official-summary",
            "fetched_at": "2026-07-28T00:00:00+00:00",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "raw_battles": [],
            "raw_battles_storage": {
                "record_count": runtime_multi.DAILY_TARGET_BATTLES,
                "loaded": False,
            },
            "cards_meta": [],
            "top_decks": [],
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }

        with patch.object(runtime_multi, "RUNTIME_ROLE", "collector"), patch.object(
            runtime_multi, "load_published_snapshot_summary", return_value=summary
        ) as load_summary, patch.object(runtime_multi, "load_published_snapshot") as load_full:
            restored = runtime_multi.restore_published_snapshot(app)

        self.assertEqual(restored["snapshot_id"], "official-summary")
        load_summary.assert_called_once_with(runtime_multi.DATA_DIR)
        load_full.assert_not_called()

    def test_runtime_does_not_publish_a_partial_weekly_collection(self):
        previous = {
            "snapshot_id": "official-previous",
            "fetched_at": "2026-07-15T00:00:00+00:00",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "cards_meta": [{"card_name": "Zap"}],
            "top_decks": [{"deck_name": "Deck"}],
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }
        partial = {
            **previous,
            "snapshot_id": "partial",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES - 1,
            "shortfall_battles": 1,
        }
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=previous,
                live_snapshot_at=0.0,
                live_error=None,
                live_refresh_lock=None,
                live_refresh_status="ready",
            )
        )

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SupercellAPIClient"
        ) as client_class, patch.object(runtime_multi, "publish_daily_snapshot") as publish:
            client_class.return_value.fetch_snapshot.return_value = partial

            result = runtime_multi.ensure_live_snapshot(app)

        self.assertEqual(result, previous)
        self.assertEqual(app.state.live_snapshot, previous)
        self.assertEqual(app.state.live_refresh_status, "cooldown")
        self.assertGreater(app.state.live_cooldown_until, 0)
        self.assertEqual(app.state.live_last_refresh_attempt["status"], "incomplete")
        self.assertEqual(app.state.live_last_refresh_attempt["sample_battles"], runtime_multi.DAILY_TARGET_BATTLES - 1)
        self.assertEqual(app.state.live_last_refresh_attempt["shortfall_battles"], 1)
        publish.assert_not_called()

        status = runtime_multi.get_live_snapshot_status(app)
        self.assertEqual(status["snapshot_status"], "cooldown")
        self.assertGreater(status["cooldown_remaining_seconds"], 0)
        self.assertEqual(status["last_refresh_attempt"]["status"], "incomplete")

    def test_runtime_marks_source_exhausted_without_empty_cooldown_loop(self):
        partial = {
            "snapshot_id": "source-exhausted",
            "fetched_at": "2026-07-28T00:00:00+00:00",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES - 1,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 1,
            "cards_meta": [{"card_name": "Zap"}],
            "top_decks": [{"deck_name": "Deck"}],
            "collection_metrics": {"source_exhausted": True, "refresh_budget_exhausted": False, "rate_limited": 0},
        }
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
                live_refresh_lock=None,
                live_refresh_status="ready",
            )
        )

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SupercellAPIClient"
        ) as client_class, patch.object(runtime_multi, "publish_daily_snapshot") as publish:
            client_class.return_value.fetch_snapshot.return_value = partial

            result = runtime_multi.ensure_live_snapshot(app)

        self.assertIsNone(result)
        self.assertEqual(app.state.live_refresh_status, "source_exhausted")
        self.assertGreater(app.state.live_cooldown_until, 0)
        self.assertEqual(app.state.live_last_refresh_attempt["status"], "source_exhausted")
        publish.assert_not_called()

    def test_runtime_passes_configured_fallback_tags_to_official_client(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(live_snapshot=None, live_snapshot_at=0.0, live_error=None))
        snapshot = {
            "cards_meta": [{"card_name": "Zap"}],
            "top_decks": [{"deck_name": "Deck"}],
            "fetched_at": "2099-01-01T00:00:00+00:00",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SUPERCELL_FALLBACK_PLAYER_TAGS", ("#ABC",), create=True
        ), patch.object(runtime_multi, "SUPERCELL_LEADERBOARD_PLAYERS", 100, create=True
        ), patch.object(runtime_multi, "SUPERCELL_BATTLES_PER_PLAYER", 25, create=True), patch.object(
            runtime_multi, "SUPERCELL_FETCH_CONCURRENCY", 8, create=True
        ), patch.object(runtime_multi, "SupercellAPIClient") as client_class, patch.object(
            runtime_multi, "publish_daily_snapshot", side_effect=lambda value, *_: value
        ):
            client_class.return_value.fetch_snapshot.return_value = snapshot

            runtime_multi.ensure_live_snapshot(app)

        kwargs = client_class.return_value.fetch_snapshot.call_args.kwargs
        self.assertEqual(kwargs["fallback_player_tags"], ("#ABC",))
        self.assertEqual(kwargs["target_battles"], runtime_multi.DAILY_TARGET_BATTLES)
        self.assertEqual(kwargs["player_limit"], 100)
        self.assertEqual(kwargs["seed_player_limit"], runtime_multi.SUPERCELL_POL_SEED_PLAYERS)
        self.assertEqual(kwargs["battles_per_player"], 25)
        self.assertEqual(kwargs["concurrency"], 8)
        self.assertEqual(kwargs["spool_dir"], runtime_multi.DATA_DIR / "snapshot_work")

    def test_runtime_uses_the_fixed_daily_sample_target(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
                live_sample_target_battles=2000,
            )
        )
        snapshot = {
            "cards_meta": [],
            "top_decks": [],
            "fetched_at": "2099-01-01T00:00:00+00:00",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SupercellAPIClient"
        ) as client_class, patch.object(runtime_multi, "publish_daily_snapshot", side_effect=lambda value, *_: value):
            client_class.return_value.fetch_snapshot.return_value = snapshot
            runtime_multi.ensure_live_snapshot(app)

        self.assertEqual(client_class.return_value.fetch_snapshot.call_args.kwargs["target_battles"], runtime_multi.DAILY_TARGET_BATTLES)

    def test_configuring_live_sample_target_is_disabled_for_the_daily_snapshot(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot={"cards_meta": [{"card_name": "Zap"}]},
                live_snapshot_at=123.0,
                live_error="previous error",
            )
        )

        with self.assertRaises(Exception) as error:
            runtime_multi.configure_live_sample_target(app, 1000)

        self.assertEqual(getattr(error.exception, "status_code", None), 409)
        self.assertIsNotNone(app.state.live_snapshot)

    def test_configuring_live_sample_target_rejects_out_of_range_value(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        with self.assertRaises(Exception) as error:
            runtime_multi.configure_live_sample_target(app, 20001)

        self.assertEqual(getattr(error.exception, "status_code", None), 409)

    def test_runtime_keeps_a_safe_actionable_live_snapshot_error(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(live_snapshot=None, live_snapshot_at=0.0, live_error=None))

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SupercellAPIClient"
        ) as client_class:
            client_class.return_value.fetch_snapshot.side_effect = ValueError("official API returned no usable battle-log decks")

            self.assertIsNone(runtime_multi.ensure_live_snapshot(app))

        self.assertEqual(app.state.live_error, "ValueError: official API returned no usable battle-log decks")

    async def test_runtime_parses_without_triggering_live_collection_on_the_request_path(self):
        order = []
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[], schedule_data=[], top_decks_data=[], retriever=None, live_snapshot=None, live_snapshot_at=0.0, live_error=None
            )
        )
        result = AnswerResult("ok", "trace", {"intent": "card_query"}, None, "CardMetaSkill", "direct", {})

        async def parse(*_args):
            order.append("parse")
            return {"intent": "card_query", "card_name": "Electro Giant", "rank": None, "top_n": None}

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True
        ), patch.object(runtime_multi, "parse_user_query", AsyncMock(side_effect=parse)), patch.object(
            runtime_multi.asyncio, "to_thread", AsyncMock()
        ) as to_thread, patch.object(
            runtime_multi, "EXTERNAL_API_REQUIRED", False
        ), patch.object(runtime_multi, "answer_query", AsyncMock(return_value=result)):
            await runtime_multi.build_answer("question", app)

        self.assertEqual(order, ["parse"])
        to_thread.assert_not_awaited()

    async def test_strict_external_api_mode_uses_only_supercell_cards(self):
        official_snapshot = {
            "snapshot_id": "official-20260725",
            "cards_meta": [{"card_name": "Zap", "source": "Supercell API live sample"}],
            "top_decks": [],
            "card_deck_stats": {},
            "fetched_at": "now",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "sampled_players": 1000,
            "fetched_players": 1000,
            "failed_players": 1,
        }
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[
                    {"card_name": "Zap", "source": "April snapshot"},
                    {"card_name": "Electro Giant", "source": "April snapshot"},
                ],
                schedule_data=[],
                top_decks_data=[],
                retriever=None,
                live_snapshot=official_snapshot,
                live_snapshot_at=0.0,
                live_error=None,
            )
        )
        result = AnswerResult("ok", "trace", {"intent": "card_query"}, None, "CardMetaSkill", "direct", {})
        captured = {}

        async def answer_query(**kwargs):
            captured.update(kwargs)
            return result

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi, "SUPERCELL_API_TOKEN", "test-token"
        ), patch.object(
            runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True
        ), patch.object(runtime_multi, "EXTERNAL_API_REQUIRED", True, create=True), patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(return_value={"intent": "card_query", "card_name": "Zap", "rank": None, "top_n": None, "parse_source": "llm_parser"}),
        ), patch.object(runtime_multi, "answer_query", AsyncMock(side_effect=answer_query)):
            actual = await runtime_multi.build_answer("Zap usage", app)

        self.assertEqual([item["card_name"] for item in captured["cards_meta_data"]], ["Zap"])
        self.assertEqual(actual.metadata["live_data"]["static_card_fallback_count"], 0)
        self.assertEqual(actual.metadata["live_data"]["target_battles"], runtime_multi.DAILY_TARGET_BATTLES)
        self.assertEqual(actual.metadata["live_data"]["sampled_players"], 1000)
        self.assertEqual(actual.metadata["live_data"]["failed_players"], 1)

    async def test_strict_schedule_query_does_not_wait_for_official_snapshot(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[],
                bootstrap_cards_meta_data=[{"card_name": "Zap", "source": "April snapshot"}],
                schedule_data=[{"round": 1, "opponent": "Team A"}],
                top_decks_data=[],
                retriever=None,
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
                rag_status="not_required",
                rag_snapshot_id=None,
            )
        )
        result = AnswerResult("ok", "trace", {"intent": "schedule_query"}, None, "ScheduleSkill", "direct", {})

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi, "SUPERCELL_API_TOKEN", "test-token"
        ), patch.object(runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True), patch.object(
            runtime_multi, "EXTERNAL_API_REQUIRED", True, create=True
        ), patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(return_value={"intent": "schedule_query", "parse_source": "llm_parser"}),
        ), patch.object(runtime_multi, "ensure_live_snapshot") as ensure_snapshot, patch.object(
            runtime_multi, "answer_query", AsyncMock(return_value=result)
        ) as answer_query:
            actual = await runtime_multi.build_answer("第一轮打谁", app)

        ensure_snapshot.assert_not_called()
        self.assertEqual(actual.metadata["data_context"]["cards"], "not_used")
        self.assertEqual(answer_query.await_args.kwargs["schedule_data"], [{"round": 1, "opponent": "Team A"}])
        self.assertEqual(answer_query.await_args.kwargs["cards_meta_data"], [])

    async def test_strict_mixed_query_keeps_schedule_and_uses_official_snapshot(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[],
                bootstrap_cards_meta_data=[{"card_name": "Zap", "source": "April snapshot"}],
                schedule_data=[{"round": 1, "opponent": "Team A"}],
                top_decks_data=[],
                retriever=None,
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
                rag_status="not_required",
                rag_snapshot_id=None,
            )
        )
        result = AnswerResult("ok", "trace", {"intent": "multi_intent"}, None, "MultiIntent", "mixed", {})
        official_snapshot = {
            "snapshot_id": "official-20260725",
            "cards_meta": [{"card_name": "Zap", "source": "Supercell Official API"}],
            "top_decks": [{"deck_name": "Official deck"}],
            "card_deck_stats": {
                "Zap": [
                    {
                        "deck_name": "Zap / Official deck",
                        "battles": 18,
                        "sample_win_rate": 55.6,
                    }
                ]
            },
            "fetched_at": "now",
            "sample_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "target_battles": runtime_multi.DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "sampled_players": 1000,
            "fetched_players": 1000,
            "failed_players": 0,
        }
        app.state.live_snapshot = official_snapshot

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi, "SUPERCELL_API_TOKEN", "test-token"
        ), patch.object(runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True), patch.object(
            runtime_multi, "EXTERNAL_API_REQUIRED", True, create=True
        ), patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(
                return_value={
                    "intent": "multi_intent",
                    "parse_source": "llm_parser",
                    "subqueries": [
                        {"id": "q1", "intent": "schedule_query"},
                        {"id": "q2", "intent": "card_query", "card_name": "Zap"},
                    ],
                }
            ),
        ), patch.object(runtime_multi, "answer_query", AsyncMock(return_value=result)
        ) as answer_query:
            actual = await runtime_multi.build_answer("第一轮打谁，Zap 使用率", app)

        self.assertEqual(answer_query.await_args.kwargs["schedule_data"], [{"round": 1, "opponent": "Team A"}])
        self.assertEqual(answer_query.await_args.kwargs["cards_meta_data"], official_snapshot["cards_meta"])
        self.assertEqual(answer_query.await_args.kwargs["top_decks_data"], official_snapshot["top_decks"])
        self.assertEqual(answer_query.await_args.kwargs["card_deck_stats"], official_snapshot["card_deck_stats"])
        self.assertEqual(actual.metadata["data_context"]["cards"], "official_weekly_snapshot")
        self.assertEqual(actual.metadata["data_context"]["schedule"], "disabled_clan_war_feature")
        self.assertEqual(actual.metadata["live_data"]["snapshot_id"], "official-20260725")

    async def test_strict_external_api_mode_stops_when_supercell_is_unavailable(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[{"card_name": "Zap", "source": "April snapshot"}],
                schedule_data=[],
                top_decks_data=[],
                retriever=None,
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error="RequestException",
            )
        )

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi, "SUPERCELL_API_TOKEN", "test-token"
        ), patch.object(
            runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True
        ), patch.object(runtime_multi, "EXTERNAL_API_REQUIRED", True, create=True), patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(return_value={"intent": "card_query", "card_name": "Zap", "rank": None, "top_n": None, "parse_source": "llm_parser"}),
        ), patch.object(runtime_multi.asyncio, "to_thread", AsyncMock(return_value=None)), patch.object(
            runtime_multi, "answer_query", AsyncMock()
        ) as answer_query:
            result = await runtime_multi.build_answer("Zap usage", app)

        answer_query.assert_not_awaited()
        self.assertEqual(result.mode, "unavailable")
        self.assertEqual(result.metadata["live_data"]["status"], "unavailable")

    async def test_strict_external_api_mode_rejects_non_model_parser_result(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[], schedule_data=[], top_decks_data=[], retriever=None,
                live_snapshot=None, live_snapshot_at=0.0, live_error=None,
            )
        )

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi, "EXTERNAL_API_REQUIRED", True, create=True
        ), patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(return_value={"intent": "card_query", "card_name": "Zap", "parse_source": "local_rule"}),
        ), patch.object(runtime_multi, "answer_query", AsyncMock()) as answer_query:
            result = await runtime_multi.build_answer("Zap usage", app)

        answer_query.assert_not_awaited()
        self.assertEqual(result.mode, "unavailable")
        self.assertEqual(result.metadata["parser_api"]["status"], "unavailable")

    async def test_strict_mode_continues_with_validated_fallback_after_model_parser_timeout(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[], bootstrap_cards_meta_data=[], schedule_data=[], top_decks_data=[],
                card_deck_stats_data={}, retriever=None, live_snapshot=None, live_snapshot_at=0.0,
                live_error=None, rag_status="ready", rag_snapshot_id="snapshot-1",
                rag_docs_fingerprint="fingerprint-1",
            )
        )
        answer = AnswerResult(
            "基于证据的环境回答",
            "trace",
            {"intent": "meta_analysis_query"},
            None,
            "EvidenceSynthesisSkill",
            "rag_synthesis",
            {},
        )
        parsed = {
            "intent": "meta_analysis_query",
            "parse_source": "validated_fallback",
            "parse_confidence": "high",
            "model_parser_attempted": True,
            "model_parser_status": "timeout",
        }

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi, "EXTERNAL_API_REQUIRED", True, create=True
        ), patch.object(runtime_multi, "query_requires_official_snapshot", return_value=False), patch.object(
            runtime_multi, "parse_user_query", AsyncMock(return_value=parsed)
        ), patch.object(runtime_multi, "answer_query", AsyncMock(return_value=answer)) as answer_query:
            result = await runtime_multi.build_answer("分析当前环境", app)

        answer_query.assert_awaited_once()
        self.assertEqual(result.answer, "基于证据的环境回答")
        self.assertEqual(result.metadata["parser_api"]["status"], "degraded")
        self.assertEqual(result.metadata["parser_api"]["model_status"], "timeout")


if __name__ == "__main__":
    unittest.main()
