import json
import unittest
from pathlib import Path
from unittest.mock import patch

from support import install_test_stubs

install_test_stubs()

import app_config
import query_answering
from fastapi import FastAPI
from answer_builder import build_card_answer, build_deck_answer, build_schedule_answer
from runtime_multi import build_chat_model, lifespan, query_needs_rag
from query_parser import fallback_parse_query, normalize_parsed_query


DATA_DIR = Path("data")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class QueryLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = load_json("schedule.json")
        cls.deck_data = load_json("top_decks.json")
        cls.card_data = load_json("cards_meta.json")

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

    def test_meta_analysis_parses_baby_dragon_alias_and_qualitative_question(self):
        parsed = fallback_parse_query("绿龙在当前环境里的定位是什么？适合搭配哪些核心卡，主要怕什么？", self.card_data)

        self.assertEqual(parsed["intent"], "meta_analysis_query")
        self.assertEqual(parsed["card_name"], "Baby Dragon")

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
        self.assertIn("cards_meta.json", answer)

    def test_build_card_answer_returns_single_ranked_card(self):
        answer = build_card_answer(
            {"card_name": None, "rank": 3, "top_n": None, "metric": "usage_rate"},
            self.card_data,
        )
        self.assertIn("第 3 名卡牌", answer)
        self.assertNotIn("前 10 张卡牌", answer)

    def test_app_config_defaults_are_loaded(self):
        self.assertEqual(app_config.RUNTIME_PORT, 8091)
        self.assertEqual(app_config.RETRIEVAL_TOP_K_BM25, 10)
        self.assertEqual(app_config.RETRIEVAL_FINAL_TOP_K, 8)
        self.assertEqual(app_config.EMBED_MODEL, "bge-m3:latest")
        self.assertTrue(app_config.OPENAI_MODEL)

    def test_chat_model_uses_standard_openai_configuration(self):
        with patch("runtime_multi.OpenAIChatModel") as model_class:
            build_chat_model("test-key")

        model_class.assert_called_once_with(
            model_name=app_config.OPENAI_MODEL,
            api_key="test-key",
            stream=False,
        )

    def test_reviewer_model_uses_standard_openai_configuration(self):
        with patch("query_answering.OpenAIChatModel") as model_class:
            query_answering.build_reviewer_model("test-key")

        model_class.assert_called_once_with(
            model_name=app_config.OPENAI_MODEL,
            api_key="test-key",
            stream=False,
        )

    def test_query_needs_rag_only_for_open_deck_and_card_queries(self):
        self.assertFalse(query_needs_rag({"intent": "schedule_query", "round": 2}))
        self.assertFalse(query_needs_rag({"intent": "deck_query", "rank": 1, "top_n": None}))
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


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_initializes_json_data_and_keeps_retriever_lazy(self):
        app = FastAPI()
        async with lifespan(app):
            self.assertEqual(len(app.state.schedule_data), 11)
            self.assertEqual(len(app.state.top_decks_data), 30)
            self.assertEqual(len(app.state.cards_meta_data), 176)
            self.assertIsNone(app.state.retriever)


if __name__ == "__main__":
    unittest.main()
