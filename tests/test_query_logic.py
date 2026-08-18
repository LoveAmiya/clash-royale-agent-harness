import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import install_test_stubs, sample_cards, sample_decks, sample_schedule

install_test_stubs()

import app_config
import query_answering
import runtime_multi
from fastapi import FastAPI
from answer_builder import build_card_answer, build_deck_answer, build_named_card_metrics_answer, build_schedule_answer
from runtime_multi import build_chat_model, lifespan, query_needs_rag
from query_parser import fallback_parse_query, normalize_parsed_query


class QueryLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = sample_schedule()
        cls.deck_data = sample_decks()
        cls.card_data = sample_cards()

    def test_schedule_query_parses_round(self):
        parsed = fallback_parse_query("我们第五轮打谁", self.card_data)
        self.assertEqual(parsed["intent"], "schedule_query")
        self.assertEqual(parsed["round"], 5)
        self.assertIsNone(parsed["top_n"])

    def test_deck_query_parses_top_n(self):
        parsed = fallback_parse_query("热门卡组前3名", self.card_data)
        self.assertEqual(parsed["intent"], "deck_query")
        self.assertEqual(parsed["top_n"], 3)
        self.assertEqual(parsed["metric"], "usage_rate")

    def test_schedule_query_parses_date(self):
        parsed = fallback_parse_query("5月22日我们打谁", self.card_data)
        self.assertEqual(parsed["intent"], "schedule_query")
        self.assertEqual(parsed["date"], "2026-05-22")

    def test_card_query_parses_card_and_metric(self):
        parsed = fallback_parse_query("火球的胜率是多少", self.card_data)
        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["card_name"], "Fireball")
        self.assertEqual(parsed["metric"], "win_rate")
        self.assertIsNone(parsed["top_n"])

    def test_explicit_card_forms_and_towers_select_loadout_entities(self):
        evolved = fallback_parse_query("觉醒骑士的使用率是多少", self.card_data)
        elite = fallback_parse_query("精英火球的胜率是多少", self.card_data)
        tower = fallback_parse_query("飞刀塔目前表现如何", self.card_data)

        self.assertEqual(evolved["entity_mode"], "loadout_entity")
        self.assertEqual(evolved["special_state"], "evolution")
        self.assertEqual(elite["special_state"], "elite")
        self.assertEqual(tower["entity_type"], "tower")
        self.assertFalse(query_needs_rag(evolved))

    def test_official_elite_barbarians_name_is_not_an_elite_form_modifier(self):
        ordinary = fallback_parse_query("Elite Barbarians usage rate", self.card_data)

        self.assertEqual(ordinary["card_name"], "Elite Barbarians")
        self.assertEqual(ordinary["entity_mode"], "base8")
        self.assertIsNone(ordinary["special_state"])

    def test_meta_analysis_parses_baby_dragon_alias_and_qualitative_question(self):
        parsed = fallback_parse_query("绿龙在当前环境里的定位是什么？适合搭配哪些核心卡，主要怕什么？", self.card_data)

        self.assertEqual(parsed["intent"], "meta_analysis_query")
        self.assertEqual(parsed["card_name"], "Baby Dragon")

    def test_current_environment_deck_questions_route_to_evidence_synthesis(self):
        for question in [
            "当前环境以什么进攻风格和卡组构筑为主？",
            "当前环境以什么卡组为主？",
        ]:
            with self.subTest(question=question):
                parsed = fallback_parse_query(question, self.card_data)
                self.assertEqual(parsed["intent"], "meta_analysis_query")

    def test_environment_change_question_selects_precomputed_meta_delta(self):
        parsed = fallback_parse_query("最近环境发生了什么变化？", self.card_data)

        self.assertEqual(parsed["intent"], "meta_analysis_query")
        self.assertEqual(parsed["analysis_type"], "meta_delta")

    def test_match_preparation_keeps_tactical_user_question(self):
        parsed = fallback_parse_query("如果对手最近常用空军卡组，我们应该优先准备哪些防守和反制方案？", self.card_data)

        self.assertEqual(parsed["intent"], "match_preparation_query")

    def test_card_query_parses_usage_rank_target(self):
        parsed = fallback_parse_query("使用率第三的卡牌是什么", self.card_data)
        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["metric"], "usage_rate")
        self.assertEqual(parsed["rank"], 3)
        self.assertIsNone(parsed["top_n"])

    def test_card_query_parses_win_rate_rank_target(self):
        parsed = fallback_parse_query("胜率第三的卡牌是什么", self.card_data)
        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["metric"], "win_rate")
        self.assertEqual(parsed["rank"], 3)
        self.assertIsNone(parsed["top_n"])

    def test_english_deck_ranking_parses_top_n(self):
        parsed = fallback_parse_query("top 3 deck ranking", self.card_data)
        self.assertEqual(parsed["intent"], "deck_query")
        self.assertEqual(parsed["top_n"], 3)

    def test_english_card_ranking_position_routes_to_rank_lookup(self):
        parsed = fallback_parse_query("The Log ranking position", self.card_data)
        self.assertEqual(parsed["intent"], "card_rank_lookup_query")
        self.assertEqual(parsed["card_name"], "The Log")

    def test_english_card_comparison_preserves_requested_win_rate(self):
        parsed = fallback_parse_query("Fireball vs Poison win rate", self.card_data)

        self.assertEqual(parsed["intent"], "card_compare_query")
        self.assertEqual(parsed["card_names"], ["Fireball", "Poison"])
        self.assertEqual(parsed["compare_metric"], "win_rate")

    def test_short_english_card_aliases_do_not_match_inside_unrelated_words(self):
        for question in [
            "tell me a joke about programming",
            "summarize a chemistry paper",
            "what is the price of bitcoin",
        ]:
            with self.subTest(question=question):
                parsed = fallback_parse_query(question, self.card_data)
                self.assertEqual(parsed["intent"], "reject")
                self.assertIsNone(parsed["card_name"])

    def test_normalize_parsed_query_casts_rank(self):
        parsed = {
            "intent": "card_query",
            "metric": "win_rate",
            "rank": "3",
            "top_n": None,
            "card_name": None,
            "round": None,
            "ask_players": False,
        }
        normalized = normalize_parsed_query(parsed, "胜率第三的卡牌是什么", self.card_data)
        self.assertEqual(normalized["intent"], "card_query")
        self.assertEqual(normalized["rank"], 3)

    def test_normalize_model_card_alias_returns_single_card_metrics(self):
        parsed = {
            "intent": "card_query",
            "metric": "win_rate",
            "compare_metric": None,
            "rank": None,
            "top_n": 10,
            "card_name": "\u706b\u7403",
            "card_names": None,
            "round": None,
            "date": None,
            "ask_players": False,
        }

        normalized = normalize_parsed_query(parsed, "\u706b\u7403\u7684\u80dc\u7387\u662f\u591a\u5c11", self.card_data)

        self.assertEqual(normalized["card_name"], "Fireball")
        self.assertIsNone(normalized["rank"])
        self.assertIsNone(normalized["top_n"])

    def test_normalize_model_card_aliases_for_comparison(self):
        parsed = {
            "intent": "card_compare_query",
            "metric": None,
            "compare_metric": "win_rate",
            "rank": None,
            "top_n": None,
            "card_name": None,
            "card_names": ["\u706b\u7403", "\u6bd2\u836f"],
            "round": None,
            "date": None,
            "ask_players": False,
        }

        normalized = normalize_parsed_query(parsed, "\u706b\u7403\u548c\u6bd2\u836f\u8c01\u7684\u80dc\u7387\u66f4\u9ad8", self.card_data)

        self.assertEqual(normalized["card_names"], ["Fireball", "Poison"])

    def test_response_model_passes_configured_openai_compatible_base_url(self):
        with patch.object(runtime_multi, "OpenAIResponseModel") as model_class, patch.object(
            runtime_multi,
            "OPENAI_CLIENT_KWARGS",
            {"base_url": "https://example.invalid"},
        ), patch.object(
            runtime_multi,
            "OPENAI_WIRE_API",
            "responses",
        ):
            runtime_multi.build_chat_model("test-key")

        self.assertEqual(
            model_class.call_args.kwargs["client_kwargs"],
            {"base_url": "https://example.invalid"},
        )

    def test_normalize_parsed_query_casts_schedule_round_phrase(self):
        parsed = {
            "intent": "schedule_query",
            "metric": None,
            "rank": None,
            "top_n": None,
            "card_name": None,
            "round": "第6轮",
            "date": None,
            "ask_players": False,
        }
        normalized = normalize_parsed_query(parsed, "我们第6轮打谁", self.card_data)
        self.assertEqual(normalized["intent"], "schedule_query")
        self.assertEqual(normalized["round"], 6)

    def test_normalize_parsed_query_falls_back_to_question_round(self):
        parsed = {
            "intent": "schedule_query",
            "metric": None,
            "rank": None,
            "top_n": None,
            "card_name": None,
            "round": "6th",
            "date": None,
            "ask_players": False,
        }
        normalized = normalize_parsed_query(parsed, "我们第6轮打谁", self.card_data)
        self.assertEqual(normalized["intent"], "schedule_query")
        self.assertEqual(normalized["round"], 6)

    def test_build_schedule_answer_contains_reference(self):
        answer = build_schedule_answer({"round": 1, "ask_players": False}, self.schedule_data)
        self.assertIn("round=1", answer)
        self.assertIn("schedule.json", answer)

    def test_build_schedule_answer_supports_date(self):
        answer = build_schedule_answer({"date": "2026-05-22", "ask_players": False}, self.schedule_data)
        self.assertIn("2026-05-22", answer)
        self.assertIn("schedule.json", answer)

    def test_build_deck_answer_contains_rank_and_source(self):
        answer = build_deck_answer({"rank": 1, "top_n": None}, self.deck_data)
        self.assertIn("rank=1", answer)
        self.assertIn("top_decks.json", answer)

    def test_build_card_answer_contains_card_name(self):
        answer = build_card_answer(
            {"card_name": "Fireball", "rank": None, "top_n": None, "metric": "win_rate"},
            self.card_data,
        )
        self.assertIn("Fireball", answer)
        self.assertIn("unit-test fixture", answer)
        self.assertNotIn("not global meta", answer)

    def test_live_card_boundary_is_fully_chinese(self):
        answer = build_named_card_metrics_answer(
            {
                "card_name": "Bowler",
                "source": "Supercell API live sample",
                "sample_battles": 200000,
                "fetched_at": "2026-07-28T21:52:30+00:00",
                "usage_rate": 11.2,
                "win_rate": 57.0,
                "appearance_count": 22418,
            },
            ["usage_rate", "win_rate"],
        )

        self.assertIn("并非全球完整环境统计", answer)
        self.assertNotIn("not global meta", answer)
        self.assertNotIn("**", answer)

    def test_build_card_answer_returns_single_ranked_card(self):
        answer = build_card_answer(
            {"card_name": None, "rank": 3, "top_n": None, "metric": "usage_rate"},
            self.card_data,
        )
        self.assertIn("第 3 名卡牌", answer)
        self.assertNotIn("前 10 张卡牌", answer)

    def test_app_config_defaults_are_loaded(self):
        self.assertEqual(app_config.RUNTIME_PORT, 8091)
        self.assertEqual(app_config.RETRIEVAL_TOP_K_BM25, 32)
        self.assertEqual(app_config.RETRIEVAL_TOP_K_DENSE, 32)
        self.assertEqual(app_config.RETRIEVAL_FINAL_TOP_K, 24)
        self.assertEqual(app_config.RETRIEVAL_FUSION_MODE, "rrf")
        self.assertEqual(app_config.RETRIEVAL_RRF_K, 60)
        self.assertEqual(app_config.EMBED_MODEL, "bge-m3:latest")
        self.assertEqual(app_config.OLLAMA_EMBED_TIMEOUT_SECONDS, 10.0)
        self.assertEqual(app_config.EMBED_BATCH_SIZE, 32)
        self.assertEqual(app_config.PARSER_CALL_TIMEOUT_SECONDS, 45.0)
        self.assertEqual(app_config.MODEL_CALL_TIMEOUT_SECONDS, 120.0)
        self.assertEqual(app_config.MODEL_FIRST_TOKEN_TIMEOUT_SECONDS, 75.0)
        self.assertEqual(app_config.MODEL_PROGRESS_INTERVAL_SECONDS, 2.0)
        self.assertEqual(app_config.SUPERCELL_TARGET_BATTLES, 200000)
        self.assertEqual(app_config.SUPERCELL_MAX_TARGET_BATTLES, 200000)
        self.assertEqual(app_config.SUPERCELL_POL_SEED_PLAYERS, 1000)
        self.assertEqual(app_config.SUPERCELL_LEADERBOARD_PLAYERS, 12000)
        self.assertEqual(app_config.SUPERCELL_FETCH_CONCURRENCY, 2)
        self.assertEqual(app_config.OPENAI_MODEL, "gpt-5.5")
        self.assertEqual(app_config.OPENAI_REVIEW_MODEL, "gpt-5.5")
        self.assertEqual(app_config.OPENAI_BASE_URL, "https://crs.ruinique.com")
        self.assertEqual(app_config.OPENAI_WIRE_API, "responses")
        self.assertEqual(app_config.OPENAI_REASONING_EFFORT, "medium")
        self.assertEqual(app_config.PARSER_REASONING_EFFORT, "medium")
        self.assertEqual(app_config.SYNTHESIS_REASONING_EFFORT, "medium")

    def test_chat_model_uses_responses_configuration(self):
        with patch("runtime_multi.OpenAIResponseModel") as model_class:
            build_chat_model("test-key")

        model_class.assert_called_once_with(
            model_name=app_config.OPENAI_MODEL,
            api_key="test-key",
            stream=False,
            client_kwargs=app_config.OPENAI_CLIENT_KWARGS,
            reasoning_effort=app_config.OPENAI_REASONING_EFFORT,
        )

    def test_reviewer_model_uses_responses_configuration(self):
        with patch("query_answering.OpenAIResponseModel") as model_class:
            query_answering.build_reviewer_model("test-key")

        model_class.assert_called_once_with(
            model_name=app_config.OPENAI_REVIEW_MODEL,
            api_key="test-key",
            stream=False,
            client_kwargs=app_config.OPENAI_CLIENT_KWARGS,
            reasoning_effort=app_config.OPENAI_REASONING_EFFORT,
        )

    def test_query_needs_rag_only_for_open_deck_and_card_queries(self):
        self.assertFalse(query_needs_rag({"intent": "schedule_query", "round": 2}))
        self.assertFalse(query_needs_rag({"intent": "deck_query", "rank": 1, "top_n": None}))
        self.assertFalse(
            query_needs_rag(
                {"intent": "deck_query", "card_name": "Electro Giant", "rank": None, "top_n": None}
            )
        )
        self.assertFalse(query_needs_rag({"intent": "card_query", "card_name": "Fireball"}))
        self.assertTrue(
            query_needs_rag(
                {
                    "intent": "deck_query",
                    "rank": None,
                    "top_n": None,
                }
            )
        )
        self.assertTrue(
            query_needs_rag(
                {
                    "intent": "card_query",
                    "card_name": None,
                    "rank": None,
                    "top_n": None,
                }
            )
        )


class ModelFirstParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_parser_calls_model_before_its_local_fallback(self):
        parser_result = '{"intent":"deck_query","metric":"usage_rate","compare_metric":null,"rank":1,"top_n":null,"card_name":null,"card_names":null,"round":null,"date":null,"ask_players":false}'
        model_call = AsyncMock(return_value=parser_result)

        with patch.object(runtime_multi, "generate_model_text", model_call):
            parsed = await runtime_multi.parse_user_query(
                "当前排名第一的卡组是什么？",
                [],
                "test-key",
            )

        self.assertEqual(model_call.await_count, 1)
        self.assertEqual(parsed["intent"], "deck_query")
        self.assertEqual(parsed["parse_source"], "llm_parser")


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_starts_without_private_legacy_json_data(self):
        app = FastAPI()
        with patch.object(runtime_multi, "load_json_file", side_effect=AssertionError("legacy JSON read")), patch.object(
            runtime_multi, "RUNTIME_ROLE", "all"
        ), patch.object(runtime_multi, "SUPERCELL_LIVE_DATA_ENABLED", False):
            async with lifespan(app):
                self.assertEqual(app.state.schedule_data, [])
                self.assertEqual(app.state.top_decks_data, [])
                self.assertTrue(app.state.cards_meta_data)
                self.assertEqual(app.state.cards_meta_data, app.state.bootstrap_cards_meta_data)


if __name__ == "__main__":
    unittest.main()
