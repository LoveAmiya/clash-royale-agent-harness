import asyncio
import json
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import install_test_stubs

install_test_stubs()

from answer_builder import build_card_answer
from query_parser import fallback_parse_multi_intent
from query_answering import AnswerResult, answer_query
from runtime_events import RuntimeEventEmitter
import runtime_multi


DATA_DIR = Path("data")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class MultiIntentParserTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.card_data = load_json("cards_meta.json")

    def test_compound_question_creates_direct_card_and_rag_meta_subqueries(self):
        question = "\u96f7\u7535\u5de8\u4eba\u7684\u4f7f\u7528\u7387\u3001\u80dc\u7387\uff0c\u8fd8\u6709\u5f53\u524d\u73af\u5883\u4e3b\u6d41\u5361\u7ec4"

        parsed = fallback_parse_multi_intent(question, self.card_data)

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual(len(parsed["subqueries"]), 2)
        self.assertEqual(parsed["subqueries"][0]["id"], "q1")
        self.assertEqual(parsed["subqueries"][0]["intent"], "card_query")
        self.assertEqual(parsed["subqueries"][0]["card_name"], "Electro Giant")
        self.assertEqual(parsed["subqueries"][0]["metrics"], ["usage_rate", "win_rate"])
        self.assertEqual(parsed["subqueries"][1]["intent"], "meta_analysis_query")

    def test_execution_description_names_card_comparison_instead_of_marking_it_unsupported(self):
        description = runtime_multi.describe_parsed_request(
            {
                "intent": "card_compare_query",
                "card_names": ["Fireball", "Poison"],
                "compare_metric": "win_rate",
            }
        )

        self.assertIn("Fireball", description)
        self.assertIn("Poison", description)
        self.assertIn("胜率比较", description)
        self.assertNotIn("未支持", description)

    def test_english_compound_question_deduplicates_card_metrics_and_routes_meta_to_synthesis(self):
        parsed = fallback_parse_multi_intent(
            "Electro Giant usage rate, win rate, and current meta decks",
            [{"card_name": "Electro Giant"}],
        )

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual(len(parsed["subqueries"]), 2)
        self.assertEqual(parsed["subqueries"][0]["card_name"], "Electro Giant")
        self.assertEqual(parsed["subqueries"][0]["metrics"], ["usage_rate", "win_rate"])
        self.assertEqual(parsed["subqueries"][1]["intent"], "meta_analysis_query")

    def test_meta_question_with_two_card_metrics_does_not_add_an_implicit_deck_ranking(self):
        parsed = fallback_parse_multi_intent(
            "\u73b0\u5728\u7684\u73af\u5883\u600e\u4e48\u6837\uff0c\u4e3b\u6d41\u5361\u7ec4\u6709\u54ea\u4e9b\uff0c\u706b\u7403\u7684\u4f7f\u7528\u7387\u5982\u4f55\uff0c\u6bd2\u836f\u7684\u4f7f\u7528\u7387\u5462\uff1f",
            self.card_data,
        )

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual([item["intent"] for item in parsed["subqueries"]].count("meta_analysis_query"), 1)
        self.assertEqual([item["intent"] for item in parsed["subqueries"]].count("card_query"), 2)
        self.assertEqual({item.get("card_name") for item in parsed["subqueries"]}, {None, "Fireball", "Poison"})

    def test_environment_deck_and_metrics_keep_three_bound_subqueries_in_user_order(self):
        parsed = fallback_parse_multi_intent(
            "\u73b0\u5728\u7684\u73af\u5883\u662f\u600e\u6837\u7684\uff1f"
            "\u96f7\u7535\u5de8\u4eba\u5361\u7ec4\u914d\u7f6e\u662f\u600e\u6837\u7684\uff1f"
            "\u4f7f\u7528\u7387\u548c\u80dc\u7387\u5462\uff1f",
            self.card_data,
        )

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual(
            [item["intent"] for item in parsed["subqueries"]],
            ["meta_analysis_query", "deck_query", "card_query"],
        )
        self.assertEqual(parsed["subqueries"][1]["card_name"], "Electro Giant")
        self.assertEqual(parsed["subqueries"][2]["card_name"], "Electro Giant")
        self.assertEqual(parsed["subqueries"][2]["metrics"], ["usage_rate", "win_rate"])
        self.assertFalse(
            any(
                item["intent"] == "card_query" and item.get("card_name") is None
                for item in parsed["subqueries"]
            )
        )

    def test_two_named_card_metrics_create_one_direct_subquery_per_card(self):
        parsed = fallback_parse_multi_intent("火球和毒药的使用率分别是多少？", self.card_data)

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual([item["card_name"] for item in parsed["subqueries"]], ["Fireball", "Poison"])
        self.assertTrue(all(item["metrics"] == ["usage_rate"] for item in parsed["subqueries"]))

    def test_card_answer_limits_a_named_card_to_requested_metrics(self):
        card = next(item for item in self.card_data if item["card_name"] == "Electro Giant")
        answer = build_card_answer(
            {
                "intent": "card_query",
                "card_name": "Electro Giant",
                "metrics": ["usage_rate", "win_rate"],
                "rank": None,
                "top_n": None,
            },
            self.card_data,
        )

        self.assertIn(str(card["usage_rate"]), answer)
        self.assertIn(str(card["win_rate"]), answer)
        self.assertNotIn("\u51c0\u80dc\u7387", answer)
        self.assertIn(card["source"], answer)

    async def test_llm_multi_intent_payload_is_normalized_to_subqueries(self):
        payload = (
            '{"intent":"multi_intent","subqueries":['
            '{"id":"q1","intent":"card_query","card_name":"雷电巨人","metrics":["usage_rate","win_rate"]},'
            '{"id":"q2","intent":"meta_analysis_query"}]}'
        )

        with patch.object(runtime_multi, "generate_model_text", AsyncMock(return_value=payload)):
            parsed = await runtime_multi.parse_user_query("雷电巨人的使用率、胜率，还有当前环境主流卡组", self.card_data, "test-key")

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual(parsed["subqueries"][0]["card_name"], "Electro Giant")
        self.assertEqual(parsed["subqueries"][0]["metrics"], ["usage_rate", "win_rate"])

    async def test_llm_reject_does_not_discard_high_confidence_local_multi_intent(self):
        with patch.object(runtime_multi, "generate_model_text", AsyncMock(return_value='{"intent":"reject"}')):
            parsed = await runtime_multi.parse_user_query("雷电巨人的使用率、胜率，还有当前环境主流卡组", self.card_data, "test-key")

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual(parsed["parse_source"], "llm_parser")
        self.assertIn("reconciled", parsed["parse_reason"])

    async def test_llm_cannot_change_the_local_meta_subquery_to_open_deck_query(self):
        payload = (
            '{"intent":"multi_intent","subqueries":['
            '{"id":"q1","intent":"card_query","card_name":"雷电巨人","metrics":["usage_rate","win_rate"]},'
            '{"id":"q2","intent":"deck_query"}]}'
        )

        with patch.object(runtime_multi, "generate_model_text", AsyncMock(return_value=payload)):
            parsed = await runtime_multi.parse_user_query("雷电巨人的使用率、胜率，还有当前环境主流卡组", self.card_data, "test-key")

        self.assertEqual(parsed["subqueries"][1]["intent"], "meta_analysis_query")

    async def test_llm_single_card_result_cannot_drop_a_second_explicit_card(self):
        with patch.object(
            runtime_multi,
            "generate_model_text",
            AsyncMock(return_value='{"intent":"card_query","card_name":"毒药","metrics":["usage_rate"]}'),
        ):
            parsed = await runtime_multi.parse_user_query("火球和毒药的使用率分别是多少？", self.card_data, "test-key")

        self.assertEqual(parsed["intent"], "multi_intent")
        self.assertEqual([item["card_name"] for item in parsed["subqueries"]], ["Fireball", "Poison"])


class MultiIntentOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_subqueries_execute_concurrently_and_keep_question_order(self):
        parsed = {
            "intent": "multi_intent",
            "subqueries": [
                {"id": "q1", "intent": "card_query"},
                {"id": "q2", "intent": "meta_analysis_query"},
                {"id": "q3", "intent": "deck_query"},
            ],
        }
        running = 0
        peak_running = 0

        async def execute(**kwargs):
            nonlocal running, peak_running
            running += 1
            peak_running = max(peak_running, running)
            await asyncio.sleep(0.05)
            running -= 1
            subquery = kwargs["parsed"]
            return {
                "id": subquery["id"], "title": subquery["id"], "parsed": subquery,
                "plan": None, "selected_skill": "test", "mode": "direct", "status": "success",
                "answer": subquery["id"], "metadata": {}, "error": None, "latency_ms": 50,
            }

        started = time.perf_counter()
        with patch("query_answering.execute_subquery", side_effect=execute):
            result = await answer_query(
                user_text="compound", parsed=parsed, schedule_data=[], top_decks_data=[],
                cards_meta_data=[], retriever=None, api_key="test-key", include_metadata=True,
            )
        elapsed = time.perf_counter() - started

        self.assertEqual(peak_running, 3)
        self.assertLess(elapsed, 0.12)
        self.assertEqual([item["id"] for item in result.sub_results], ["q1", "q2", "q3"])

    async def test_top_level_model_stream_reflects_rag_subquery_fallback(self):
        parsed = {
            "intent": "multi_intent",
            "subqueries": [
                {"id": "q1", "intent": "card_query"},
                {"id": "q2", "intent": "meta_analysis_query"},
            ],
        }
        results = [
            {
                "id": "q1",
                "title": "card",
                "parsed": parsed["subqueries"][0],
                "plan": None,
                "selected_skill": "CardMetaSkill",
                "mode": "direct",
                "status": "success",
                "answer": "card answer",
                "metadata": {"model_stream": "unavailable"},
                "error": None,
                "latency_ms": 1,
            },
            {
                "id": "q2",
                "title": "meta",
                "parsed": parsed["subqueries"][1],
                "plan": None,
                "selected_skill": "EvidenceSynthesisSkill",
                "mode": "rag_synthesis",
                "status": "success",
                "answer": "meta answer",
                "metadata": {"model_stream": "fallback_chunked"},
                "error": None,
                "latency_ms": 2,
            },
        ]

        with patch("query_answering.execute_subquery", AsyncMock(side_effect=results)):
            result = await answer_query(
                user_text="compound",
                parsed=parsed,
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=None,
                api_key="test-key",
                include_metadata=True,
            )

        self.assertEqual(result.metadata["model_stream"], "fallback_chunked")

    async def test_keeps_direct_result_when_rag_subquery_cannot_run(self):
        card_data = load_json("cards_meta.json")
        parsed = {
            "intent": "multi_intent",
            "subqueries": [
                {
                    "id": "q1",
                    "intent": "card_query",
                    "card_name": "Electro Giant",
                    "metrics": ["usage_rate", "win_rate"],
                    "rank": None,
                    "top_n": None,
                },
                {"id": "q2", "intent": "meta_analysis_query"},
            ],
        }

        emitter = RuntimeEventEmitter()
        result = await answer_query(
            user_text="compound query",
            parsed=parsed,
            schedule_data=[],
            top_decks_data=[],
            cards_meta_data=card_data,
            retriever=None,
            api_key="",
            include_metadata=True,
            event_sink=emitter,
            stream_content=False,
        )

        self.assertIsInstance(result, AnswerResult)
        self.assertEqual(result.selected_skill, "MultiIntentOrchestrator")
        self.assertEqual([item["id"] for item in result.sub_results], ["q1", "q2"])
        self.assertEqual(result.sub_results[0]["status"], "success")
        self.assertEqual(result.sub_results[1]["status"], "unavailable")
        self.assertIn("Electro Giant", result.sub_results[0]["title"])
        self.assertIn("total_latency_ms", result.metadata)
        self.assertIn("latency_ms", result.sub_results[0])
        self.assertIn("Electro Giant", result.answer)
        self.assertIn("OPENAI_API_KEY", result.answer)
        self.assertLess(result.answer.index("Electro Giant"), result.answer.index("OPENAI_API_KEY"))

        events = []
        while not emitter.empty():
            events.append(await emitter.next_event())
        route_steps = {event["step_id"] for event in events if event["object"] == "execution"}
        self.assertIn("q1.route", route_steps)
        self.assertIn("q2.route", route_steps)


if __name__ == "__main__":
    unittest.main()
