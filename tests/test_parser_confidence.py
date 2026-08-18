import unittest
from unittest.mock import AsyncMock, patch

from support import install_test_stubs, sample_cards

install_test_stubs()

import query_parser
from query_parser import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    LOCAL_PARSE_CONFIDENCE_LOW,
    LOCAL_PARSE_CONFIDENCE_MEDIUM,
    fallback_parse_query,
    normalize_parsed_query,
)
from runtime_multi import parse_user_query
from clashroyale_agent.qa.parser_orchestration import (
    ParserOrchestrationDependencies,
    parse_user_query_with_model,
)
from clashroyale_agent.qa.parser_metadata import (
    LocalParseMetadataDependencies,
    infer_local_parse_metadata as infer_packaged_local_parse_metadata,
)


class ParserConfidenceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.card_data = sample_cards()

    def test_local_schedule_parse_has_high_confidence_metadata(self):
        parsed = fallback_parse_query("我们第五轮打谁", self.card_data)

        self.assertEqual(parsed["parse_source"], "local_rule")
        self.assertEqual(parsed["parse_confidence"], LOCAL_PARSE_CONFIDENCE_HIGH)
        self.assertIn("intent=schedule_query", parsed["parse_reason"])

    def test_local_reject_parse_has_low_confidence_metadata(self):
        parsed = fallback_parse_query("今天天气怎么样", self.card_data)

        self.assertEqual(parsed["intent"], "reject")
        self.assertEqual(parsed["parse_source"], "local_reject")
        self.assertEqual(parsed["parse_confidence"], LOCAL_PARSE_CONFIDENCE_LOW)

    def test_packaged_local_metadata_inference_matches_root_contract(self):
        dependencies = LocalParseMetadataDependencies(
            is_meta_analysis_query=query_parser.is_meta_analysis_query,
            is_card_cooccurrence_query=query_parser.is_card_cooccurrence_query,
        )
        cases = [
            (
                {"intent": "schedule_query", "round": 5, "ask_players": True},
                "我们第五轮打谁",
            ),
            (
                {"intent": "card_query", "card_name": "Fireball", "metric": "win_rate"},
                "火球的胜率是多少",
            ),
            ({"intent": "reject"}, "今天天气怎么样"),
        ]
        for parsed, question in cases:
            with self.subTest(question=question):
                self.assertEqual(
                    infer_packaged_local_parse_metadata(
                        parsed, question, dependencies
                    ),
                    query_parser.infer_local_parse_metadata(parsed, question),
                )

    def test_normalize_parsed_query_preserves_parse_metadata(self):
        normalized = normalize_parsed_query(
            {
                "intent": "card_query",
                "metric": "win_rate",
                "rank": "3",
                "top_n": None,
                "card_name": None,
                "round": None,
                "date": None,
                "ask_players": False,
                "parse_source": "llm_parser",
                "parse_confidence": LOCAL_PARSE_CONFIDENCE_MEDIUM,
                "parse_reason": "llm output normalized",
            },
            "胜率第三的卡牌是什么",
            self.card_data,
        )

        self.assertEqual(normalized["rank"], 3)
        self.assertEqual(normalized["parse_source"], "llm_parser")
        self.assertEqual(normalized["parse_confidence"], LOCAL_PARSE_CONFIDENCE_MEDIUM)
        self.assertEqual(normalized["parse_reason"], "llm output normalized")

    def test_deck_query_preserves_named_card_and_infers_a_list_size(self):
        normalized = normalize_parsed_query(
            {
                "intent": "deck_query",
                "metric": None,
                "rank": None,
                "top_n": None,
                "card_name": "雷电巨人",
                "card_names": None,
                "round": None,
                "date": None,
                "ask_players": False,
                "parse_source": "llm_parser",
                "parse_confidence": LOCAL_PARSE_CONFIDENCE_HIGH,
                "parse_reason": "llm output",
            },
            "雷电巨人卡组有哪些",
            self.card_data,
        )

        self.assertEqual(normalized["card_name"], "Electro Giant")
        self.assertEqual(normalized["top_n"], 5)

    async def test_packaged_model_orchestrator_keeps_high_confidence_timeout_fallback(self):
        local_parsed = {
            "intent": "card_query",
            "parse_source": "local_rule",
            "parse_confidence": LOCAL_PARSE_CONFIDENCE_HIGH,
            "parse_reason": "local card match",
        }

        async def raise_timeout(**_kwargs):
            raise TimeoutError()

        dependencies = ParserOrchestrationDependencies(
            fallback_parse_multi_intent=lambda *_args: dict(local_parsed),
            extract_json_block=lambda _text: None,
            normalize_multi_intent_query=lambda parsed, *_args: parsed,
            merge_parse_metadata=lambda parsed, metadata: {**parsed, **metadata},
            build_parse_metadata=lambda **metadata: metadata,
            subquery_semantic_key=lambda _parsed: (),
            generate_model_text=raise_timeout,
            parser_system_prompt="test prompt",
            parser_reasoning_effort="low",
            parser_timeout_seconds=0.1,
            high_confidence=LOCAL_PARSE_CONFIDENCE_HIGH,
            medium_confidence=LOCAL_PARSE_CONFIDENCE_MEDIUM,
            low_confidence=LOCAL_PARSE_CONFIDENCE_LOW,
        )

        parsed = await parse_user_query_with_model("card usage", self.card_data, "test-key", dependencies)

        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["parse_source"], "validated_fallback")
        self.assertEqual(parsed["model_parser_status"], "timeout")
        self.assertTrue(parsed["model_parser_attempted"])

    async def test_high_confidence_local_parse_is_validated_fallback_when_model_call_fails(self):
        with patch("runtime_multi.generate_model_text") as build_parser_agent:
            parsed = await parse_user_query("我们第五轮打谁", self.card_data, api_key="test-key")

        self.assertEqual(parsed["parse_source"], "validated_fallback")
        self.assertEqual(parsed["parse_confidence"], LOCAL_PARSE_CONFIDENCE_HIGH)
        self.assertEqual(parsed["model_parser_status"], "error")
        build_parser_agent.assert_called_once()

    async def test_medium_confidence_local_parse_uses_local_when_no_api_key(self):
        with patch("runtime_multi.generate_model_text") as build_parser_agent:
            parsed = await parse_user_query("热门卡组有哪些", self.card_data, api_key=None)

        self.assertEqual(parsed["intent"], "deck_query")
        self.assertEqual(parsed["parse_source"], "local_rule")
        self.assertEqual(parsed["parse_confidence"], LOCAL_PARSE_CONFIDENCE_MEDIUM)
        build_parser_agent.assert_not_called()

    async def test_reject_local_parse_uses_llm_fallback_when_available(self):
        fake_agent = AsyncMock(return_value='{"intent":"deck_query","metric":"usage_rate","rank":null,"top_n":5,"card_name":null,"round":null,"date":null,"ask_players":false}')

        with patch("runtime_multi.generate_model_text", fake_agent):
            parsed = await parse_user_query("帮我总结一下环境", self.card_data, api_key="test-key")

        self.assertEqual(parsed["intent"], "deck_query")
        self.assertEqual(parsed["parse_source"], "llm_parser")
        self.assertEqual(parsed["parse_confidence"], LOCAL_PARSE_CONFIDENCE_HIGH)
        self.assertIn("gpt-5.5 structured parser output", parsed["parse_reason"])

    async def test_llm_reject_keeps_high_confidence_local_card_alias_parse(self):
        with patch("runtime_multi.generate_model_text", AsyncMock(return_value='{"intent":"reject"}')):
            parsed = await parse_user_query("\u8fdb\u5316\u8d85\u9a91\u7684\u80dc\u7387\u662f\u591a\u5c11\uff1f", self.card_data, api_key="test-key")

        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["card_name"], "Mega Knight Evolution")
        self.assertEqual(parsed["parse_source"], "llm_parser")
        self.assertEqual(parsed["model_parser_status"], "validated_reconciled")
        self.assertIn("reconciled", parsed["parse_reason"])

    async def test_llm_reject_keeps_current_mainstream_decks_on_rag_route(self):
        with patch("runtime_multi.generate_model_text", AsyncMock(return_value='{"intent":"reject"}')):
            parsed = await parse_user_query("当前主流卡组有哪些？", self.card_data, api_key="test-key")

        self.assertEqual(parsed["intent"], "meta_analysis_query")
        self.assertEqual(parsed["parse_source"], "llm_parser")
        self.assertEqual(parsed["model_parser_status"], "validated_reconciled")
        self.assertEqual(parsed["parse_confidence"], LOCAL_PARSE_CONFIDENCE_HIGH)
        self.assertIn("reconciled", parsed["parse_reason"])

    async def test_llm_non_json_response_keeps_local_parse_with_reason(self):
        fake_agent = AsyncMock(return_value="not json")

        with patch("runtime_multi.generate_model_text", fake_agent):
            parsed = await parse_user_query("今天天气怎么样", self.card_data, api_key="test-key")

        self.assertEqual(parsed["intent"], "reject")
        self.assertIn("non-json", parsed["parse_reason"])
        self.assertIn(parsed["parse_source"], {"local_reject", "local_rule"})

    async def test_llm_failure_keeps_local_parse_with_reason(self):
        fake_agent = AsyncMock(side_effect=RuntimeError("llm down"))

        with patch("runtime_multi.generate_model_text", fake_agent):
            parsed = await parse_user_query("今天天气怎么样", self.card_data, api_key="test-key")

        self.assertEqual(parsed["intent"], "reject")
        self.assertIn("llm parser failed", parsed["parse_reason"])

    async def test_llm_timeout_marks_high_confidence_local_parse_as_validated_fallback(self):
        fake_agent = AsyncMock(side_effect=TimeoutError())

        with patch("runtime_multi.generate_model_text", fake_agent):
            parsed = await parse_user_query("当前主流卡组有哪些？", self.card_data, api_key="test-key")

        self.assertEqual(parsed["intent"], "meta_analysis_query")
        self.assertEqual(parsed["parse_source"], "validated_fallback")
        self.assertEqual(parsed["model_parser_status"], "timeout")
        self.assertTrue(parsed["model_parser_attempted"])
        self.assertEqual(parsed["parse_confidence"], LOCAL_PARSE_CONFIDENCE_HIGH)
