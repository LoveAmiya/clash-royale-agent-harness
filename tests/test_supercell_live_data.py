import unittest
import types
import requests
from unittest.mock import Mock
from unittest.mock import patch
from unittest.mock import AsyncMock

from support import install_test_stubs

install_test_stubs()

from supercell_live import SupercellAPIClient, build_live_snapshot, normalize_battle_record, select_usable_battles
import runtime_multi
from answer_builder import build_card_answer
from skills.meta_evidence import build_meta_evidence_pack
from query_answering import AnswerResult


class SupercellLiveDataTests(unittest.IsolatedAsyncioTestCase):
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
        battle = {"team": [{"crowns": 1, "cards": [{"name": "Zap"}]}], "opponent": [{"crowns": 0}]}
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
        battle = {"team": [{"crowns": 1, "cards": [{"name": "Zap"}]}], "opponent": [{"crowns": 0}]}
        with patch.object(client, "fetch_global_rankings", return_value=[{"tag": "#A"}, {"tag": "#B"}]), patch.object(
            client, "fetch_battle_log", return_value=[battle]
        ), patch("supercell_live.time.monotonic", side_effect=[0.0, 0.0, 0.0, 100.0, 100.0, 100.0]):
            snapshot = client.fetch_snapshot(target_battles=10, player_limit=2, concurrency=1, max_duration_seconds=30)

        self.assertEqual(snapshot["sample_battles"], 1)
        self.assertTrue(snapshot["collection_metrics"]["refresh_budget_exhausted"])

    def test_client_tries_path_of_legends_when_global_rankings_are_empty(self):
        global_response = Mock()
        global_response.raise_for_status.return_value = None
        global_response.json.return_value = {"items": []}
        path_response = Mock()
        path_response.raise_for_status.return_value = None
        path_response.json.return_value = {"items": [{"tag": "#ABC", "name": "Player"}]}
        session = Mock()
        session.get.side_effect = [global_response, path_response]
        client = SupercellAPIClient("test-token", session=session)

        players = client.fetch_global_rankings(10)

        self.assertEqual(players, [{"tag": "#ABC", "name": "Player"}])
        self.assertIn("/locations/global/pathoflegend/players", session.get.call_args_list[1].args[0])

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

    def test_snapshot_status_exposes_source_and_leaderboard_coverage(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot={
                    "snapshot_id": "official-20260725",
                    "fetched_at": "2026-07-25T00:00:00+00:00",
                    "sample_battles": 20_000,
                    "target_battles": 20_000,
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
        self.assertEqual(status["leaderboard"]["candidate_limit"], 3000)
        self.assertEqual(status["leaderboard"]["scanned_rank_end"], 822)
        self.assertEqual(status["collection_metrics"]["duplicates_skipped"], 117)
        self.assertEqual(status["data_sources"]["schedule"], "local_schedule_json")
        self.assertEqual(status["data_sources"]["cards"], "official_daily_snapshot")
        self.assertEqual(status["data_sources"]["rag_documents"], "official_daily_snapshot")

    def test_snapshot_uses_configured_player_tags_when_rankings_are_empty(self):
        client = SupercellAPIClient("test-token", session=Mock())
        battle = {"team": [{"crowns": 1, "cards": [{"name": "Zap"}]}], "opponent": [{"crowns": 0}]}

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
        self.assertIn("not global meta", answer)
        self.assertIn("样本出场：2 次", answer)
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
            "sample_battles": 20000,
            "target_battles": 20000,
            "shortfall_battles": 0,
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
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
            "sample_battles": 20000,
            "target_battles": 20000,
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

    def test_runtime_does_not_publish_a_partial_daily_collection(self):
        previous = {
            "snapshot_id": "official-previous",
            "fetched_at": "2026-07-23T00:00:00+00:00",
            "sample_battles": 20000,
            "target_battles": 20000,
            "shortfall_battles": 0,
            "cards_meta": [{"card_name": "Zap"}],
            "top_decks": [{"deck_name": "Deck"}],
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }
        partial = {
            **previous,
            "snapshot_id": "partial",
            "sample_battles": 19999,
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
        self.assertEqual(app.state.live_refresh_status, "stale")
        publish.assert_not_called()

    def test_runtime_passes_configured_fallback_tags_to_official_client(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(live_snapshot=None, live_snapshot_at=0.0, live_error=None))
        snapshot = {
            "cards_meta": [{"card_name": "Zap"}],
            "top_decks": [{"deck_name": "Deck"}],
            "fetched_at": "2099-01-01T00:00:00+00:00",
            "sample_battles": 20000,
            "target_battles": 20000,
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
        self.assertEqual(kwargs["target_battles"], 20000)
        self.assertEqual(kwargs["player_limit"], 100)
        self.assertEqual(kwargs["battles_per_player"], 25)
        self.assertEqual(kwargs["concurrency"], 8)

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
            "sample_battles": 20000,
            "target_battles": 20000,
            "shortfall_battles": 0,
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SupercellAPIClient"
        ) as client_class, patch.object(runtime_multi, "publish_daily_snapshot", side_effect=lambda value, *_: value):
            client_class.return_value.fetch_snapshot.return_value = snapshot
            runtime_multi.ensure_live_snapshot(app)

        self.assertEqual(client_class.return_value.fetch_snapshot.call_args.kwargs["target_battles"], 20000)

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

    async def test_runtime_parses_with_the_model_before_fetching_live_data(self):
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

        async def run_thread(*_args):
            order.append("live")
            return {"cards_meta": [], "top_decks": [], "fetched_at": "now", "sample_battles": 3}

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True
        ), patch.object(runtime_multi, "parse_user_query", AsyncMock(side_effect=parse)), patch.object(
            runtime_multi.asyncio, "to_thread", AsyncMock(side_effect=run_thread)
        ), patch.object(runtime_multi, "answer_query", AsyncMock(return_value=result)):
            await runtime_multi.build_answer("question", app)

        self.assertEqual(order, ["parse", "live"])

    async def test_strict_external_api_mode_uses_only_supercell_cards(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[
                    {"card_name": "Zap", "source": "April snapshot"},
                    {"card_name": "Electro Giant", "source": "April snapshot"},
                ],
                schedule_data=[],
                top_decks_data=[],
                retriever=None,
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
            )
        )
        result = AnswerResult("ok", "trace", {"intent": "card_query"}, None, "CardMetaSkill", "direct", {})
        captured = {}

        async def answer_query(**kwargs):
            captured.update(kwargs)
            return result

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
            runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", True
        ), patch.object(runtime_multi, "EXTERNAL_API_REQUIRED", True, create=True), patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(return_value={"intent": "card_query", "card_name": "Zap", "rank": None, "top_n": None, "parse_source": "llm_parser"}),
        ), patch.object(
            runtime_multi.asyncio,
            "to_thread",
            AsyncMock(return_value={
                "cards_meta": [{"card_name": "Zap", "source": "Supercell API live sample"}],
                "top_decks": [],
                "fetched_at": "now",
                "sample_battles": 400,
                "target_battles": 400,
                "shortfall_battles": 0,
                "sampled_players": 16,
                "fetched_players": 16,
                "failed_players": 1,
            }),
        ), patch.object(runtime_multi, "answer_query", AsyncMock(side_effect=answer_query)):
            actual = await runtime_multi.build_answer("Zap usage", app)

        self.assertEqual([item["card_name"] for item in captured["cards_meta_data"]], ["Zap"])
        self.assertEqual(actual.metadata["live_data"]["static_card_fallback_count"], 0)
        self.assertEqual(actual.metadata["live_data"]["target_battles"], 400)
        self.assertEqual(actual.metadata["live_data"]["sampled_players"], 16)
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
            "sample_battles": 20000,
            "target_battles": 20000,
            "shortfall_battles": 0,
            "sampled_players": 1000,
            "fetched_players": 1000,
            "failed_players": 0,
        }

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
        ), patch.object(runtime_multi.asyncio, "to_thread", AsyncMock(return_value=official_snapshot)), patch.object(
            runtime_multi, "answer_query", AsyncMock(return_value=result)
        ) as answer_query:
            actual = await runtime_multi.build_answer("第一轮打谁，Zap 使用率", app)

        self.assertEqual(answer_query.await_args.kwargs["schedule_data"], [{"round": 1, "opponent": "Team A"}])
        self.assertEqual(answer_query.await_args.kwargs["cards_meta_data"], official_snapshot["cards_meta"])
        self.assertEqual(answer_query.await_args.kwargs["top_decks_data"], official_snapshot["top_decks"])
        self.assertEqual(answer_query.await_args.kwargs["card_deck_stats"], official_snapshot["card_deck_stats"])
        self.assertEqual(actual.metadata["data_context"]["cards"], "official_daily_snapshot")
        self.assertEqual(actual.metadata["data_context"]["schedule"], "local_schedule_json")

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

        with patch.object(runtime_multi, "SUPERCELL_API_TOKEN", "test-token"), patch.object(
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


if __name__ == "__main__":
    unittest.main()
