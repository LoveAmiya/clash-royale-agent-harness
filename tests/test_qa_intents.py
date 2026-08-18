import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from support import sample_cards

import query_parser
from clashroyale_agent.qa.intents import (
    MULTI_INTENT,
    REJECT,
    SUPPORTED_SINGLE_INTENTS,
    VALID_METRICS,
    is_supported_single_intent,
    is_valid_metric,
)
from clashroyale_agent.qa.parser_schema import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    LOCAL_PARSE_CONFIDENCE_LOW,
    LOCAL_PARSE_CONFIDENCE_MEDIUM,
    MAX_SUBQUERIES,
    PARSER_SYSTEM_PROMPT,
    TOWER_ENTITY_NAMES,
)
from clashroyale_agent.qa.parser_primitives import (
    extract_date,
    extract_json_block,
    extract_round_number,
    normalize_text,
)
from clashroyale_agent.qa.parser_metadata import (
    build_parse_metadata,
    merge_parse_metadata,
    subquery_semantic_key,
)
from clashroyale_agent.qa.parser_fallback import (
    FallbackParseDependencies,
    fallback_parse_query as fallback_packaged_parse_query,
)
from clashroyale_agent.qa.parser_multi_intent import (
    MultiIntentDependencies,
    fallback_parse_multi_intent as fallback_packaged_multi_intent,
    normalize_multi_intent_query as normalize_packaged_multi_intent_query,
)
from clashroyale_agent.qa.parser_entities import (
    apply_selected_entity_mode,
    detect_entity_reference as detect_packaged_entity_reference,
)
from clashroyale_agent.qa.parser_normalization import (
    ParserNormalizationDependencies,
    normalize_parsed_query as normalize_packaged_parsed_query,
)
from clashroyale_agent.qa.parser_rules import (
    is_asking_players,
    is_card_compare_query as is_packaged_card_compare_query,
    is_card_cooccurrence_query as is_packaged_card_cooccurrence_query,
    is_card_query as is_packaged_card_query,
    is_card_rank_lookup_query as is_packaged_card_rank_lookup_query,
    is_meta_analysis_query as is_packaged_meta_analysis_query,
    is_meta_delta_query,
    is_schedule_query,
)


class QAIntentSchemaTests(unittest.TestCase):
    def _fallback_dependencies(self) -> FallbackParseDependencies:
        return FallbackParseDependencies(
            is_schedule_summary_query=query_parser.is_schedule_summary_query,
            is_match_preparation_query=query_parser.is_match_preparation_query,
            is_meta_analysis_query=query_parser.is_meta_analysis_query,
            is_card_cooccurrence_query=query_parser.is_card_cooccurrence_query,
            is_card_compare_query=query_parser.is_card_compare_query,
            is_card_rank_lookup_query=query_parser.is_card_rank_lookup_query,
            is_schedule_query=query_parser.is_schedule_query,
            is_deck_query=query_parser.is_deck_query,
            is_card_query=query_parser.is_card_query,
            resolve_card_name=query_parser.resolve_card_name,
            resolve_card_names=query_parser.resolve_card_names,
            get_metric=query_parser.get_metric,
            extract_rank_target=query_parser.extract_rank_target,
            extract_top_n=query_parser.extract_top_n,
            extract_round_number=query_parser.extract_round_number,
            extract_date=query_parser.extract_date,
            is_card_ranking_query=query_parser.is_card_ranking_query,
            has_explicit_top_n_signal=query_parser.has_explicit_top_n_signal,
            normalize_metrics=query_parser.normalize_metrics,
            is_asking_players=query_parser.is_asking_players,
            is_meta_delta_query=query_parser.is_meta_delta_query,
            detect_entity_reference=query_parser.detect_entity_reference,
            merge_parse_metadata=query_parser.merge_parse_metadata,
            infer_local_parse_metadata=query_parser.infer_local_parse_metadata,
        )

    def _multi_intent_dependencies(self) -> MultiIntentDependencies:
        return MultiIntentDependencies(
            fallback_parse_query=query_parser.fallback_parse_query,
            resolve_card_names=query_parser.resolve_card_names,
            extract_metrics=query_parser.extract_metrics,
            is_card_compare_query=query_parser.is_card_compare_query,
            is_card_rank_lookup_query=query_parser.is_card_rank_lookup_query,
            is_card_ranking_query=query_parser.is_card_ranking_query,
            subquery_semantic_key=query_parser.subquery_semantic_key,
            has_explicit_rank_signal=query_parser.has_explicit_rank_signal,
            has_explicit_top_n_signal=query_parser.has_explicit_top_n_signal,
            make_multi_intent_result=query_parser.make_multi_intent_result,
            normalize_parsed_query=query_parser.normalize_parsed_query,
        )

    def test_supported_single_intents_preserve_parser_contract(self):
        self.assertEqual(
            SUPPORTED_SINGLE_INTENTS,
            (
                "schedule_query",
                "schedule_summary_query",
                "deck_query",
                "card_query",
                "card_compare_query",
                "card_cooccurrence_query",
                "card_rank_lookup_query",
                "meta_analysis_query",
                "match_preparation_query",
                "reject",
            ),
        )
        self.assertTrue(is_supported_single_intent(REJECT))
        self.assertFalse(is_supported_single_intent(MULTI_INTENT))
        self.assertFalse(is_supported_single_intent("unknown"))

    def test_valid_metrics_preserve_parser_contract(self):
        self.assertEqual(VALID_METRICS, ("usage_rate", "win_rate", "clean_win_rate"))
        self.assertTrue(is_valid_metric("usage_rate"))
        self.assertTrue(is_valid_metric(None))
        self.assertFalse(is_valid_metric("damage"))
        self.assertEqual(query_parser.VALID_METRICS, VALID_METRICS)

    def test_parser_specific_schema_constants_keep_root_compatibility(self):
        self.assertIs(query_parser.PARSER_SYSTEM_PROMPT, PARSER_SYSTEM_PROMPT)
        self.assertEqual(
            (
                LOCAL_PARSE_CONFIDENCE_HIGH,
                LOCAL_PARSE_CONFIDENCE_MEDIUM,
                LOCAL_PARSE_CONFIDENCE_LOW,
            ),
            ("high", "medium", "low"),
        )
        self.assertEqual(MAX_SUBQUERIES, 4)
        self.assertEqual(
            TOWER_ENTITY_NAMES,
            {"Tower Princess", "Dagger Duchess", "Royal Chef", "Cannoneer"},
        )

    def test_parser_primitives_keep_root_compatibility(self):
        self.assertIs(query_parser.normalize_text, normalize_text)
        self.assertIs(query_parser.extract_json_block, extract_json_block)
        self.assertIs(query_parser.extract_round_number, extract_round_number)
        self.assertIs(query_parser.extract_date, extract_date)
        self.assertEqual(extract_round_number("第十二轮"), 12)
        self.assertEqual(extract_date("2026-8-7"), "2026-08-07")
        self.assertEqual(extract_json_block("prefix {\"intent\": \"reject\"}"), {"intent": "reject"})

    def test_parser_metadata_helpers_keep_root_compatibility(self):
        self.assertIs(query_parser.build_parse_metadata, build_parse_metadata)
        self.assertIs(query_parser.merge_parse_metadata, merge_parse_metadata)
        self.assertIs(query_parser.subquery_semantic_key, subquery_semantic_key)
        self.assertEqual(
            subquery_semantic_key(
                {
                    "intent": "card_query",
                    "card_name": "Fireball",
                    "metrics": ["usage_rate", "win_rate"],
                }
            ),
            (
                "card_query",
                "Fireball",
                ("usage_rate", "win_rate"),
                (),
                (),
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        )

    def test_unknown_model_intent_still_falls_back_to_local_parse(self):
        parsed = query_parser.normalize_parsed_query(
            {"intent": "unknown", "metric": "damage"},
            "火球的胜率是多少",
            sample_cards(),
        )

        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["card_name"], "Fireball")
        self.assertEqual(parsed["metric"], "win_rate")

    def test_packaged_parser_normalization_matches_root_compatibility_entry(self):
        dependencies = ParserNormalizationDependencies(
            fallback_parse_query=query_parser.fallback_parse_query,
            resolve_card_name=query_parser.resolve_card_name,
            resolve_card_names=query_parser.resolve_card_names,
            is_asking_players=query_parser.is_asking_players,
            is_meta_delta_query=query_parser.is_meta_delta_query,
            is_card_ranking_query=query_parser.is_card_ranking_query,
            has_explicit_top_n_signal=query_parser.has_explicit_top_n_signal,
            detect_entity_reference=query_parser.detect_entity_reference,
        )
        raw = {
            "intent": "card_query",
            "metric": "damage",
            "metrics": ["win_rate", "damage"],
            "card_name": "火球",
            "rank": 9,
            "parse_source": "llm_parser",
        }
        question = "火球的胜率是多少"
        cards = sample_cards()

        self.assertEqual(
            normalize_packaged_parsed_query(raw, question, cards, dependencies),
            query_parser.normalize_parsed_query(raw, question, cards),
        )

    def test_packaged_fallback_parser_matches_root_compatibility_entry(self):
        cards = sample_cards()
        dependencies = self._fallback_dependencies()
        questions = [
            "\u706b\u7403\u7684\u80dc\u7387\u662f\u591a\u5c11",
            "\u70ed\u95e8\u5361\u7ec4\u6709\u54ea\u4e9b",
            "\u6211\u4eec\u7b2c\u4e94\u8f6e\u6253\u8c01",
            "\u5f53\u524d\u73af\u5883\u4e3b\u6d41\u5361\u7ec4",
        ]

        for question in questions:
            with self.subTest(question=question):
                self.assertEqual(
                    fallback_packaged_parse_query(question, cards, dependencies),
                    query_parser.fallback_parse_query(question, cards),
                )

    def test_packaged_multi_intent_parser_matches_root_compatibility_entry(self):
        cards = sample_cards()
        dependencies = self._multi_intent_dependencies()
        question = (
            "\u96f7\u7535\u5de8\u4eba\u7684\u4f7f\u7528\u7387\u3001"
            "\u80dc\u7387\uff0c\u8fd8\u6709\u5f53\u524d\u73af\u5883"
            "\u4e3b\u6d41\u5361\u7ec4"
        )

        self.assertEqual(
            fallback_packaged_multi_intent(question, cards, dependencies),
            query_parser.fallback_parse_multi_intent(question, cards),
        )

    def test_packaged_multi_intent_normalization_matches_root_entry(self):
        cards = sample_cards()
        dependencies = self._multi_intent_dependencies()
        raw = {
            "intent": "multi_intent",
            "subqueries": [
                {
                    "id": "q1",
                    "intent": "card_query",
                    "card_name": "\u706b\u7403",
                    "metrics": ["usage_rate"],
                },
                {"id": "q2", "intent": "meta_analysis_query"},
            ],
        }
        question = "\u706b\u7403\u4f7f\u7528\u7387\uff0c\u8fd8\u6709\u73af\u5883\u53d8\u5316"

        self.assertEqual(
            normalize_packaged_multi_intent_query(raw, question, cards, dependencies),
            query_parser.normalize_multi_intent_query(raw, question, cards),
        )

    def test_packaged_parser_rules_keep_local_intent_contracts(self):
        cards = sample_cards()
        self.assertIs(query_parser.is_asking_players, is_asking_players)
        self.assertIs(query_parser.is_schedule_query, is_schedule_query)
        self.assertIs(query_parser.is_meta_delta_query, is_meta_delta_query)
        self.assertTrue(is_asking_players("who plays next round"))
        self.assertTrue(is_schedule_query("下一轮打谁"))
        self.assertTrue(is_meta_delta_query("相比上周环境有什么变化"))
        self.assertEqual(
            is_packaged_meta_analysis_query(
                "当前环境主流卡组", query_parser.resolve_card_name
            ),
            query_parser.is_meta_analysis_query("当前环境主流卡组"),
        )
        self.assertEqual(
            is_packaged_card_query("火球胜率", cards, query_parser.resolve_card_name),
            query_parser.is_card_query("火球胜率", cards),
        )
        self.assertEqual(
            is_packaged_card_compare_query(
                "火球和毒药哪个胜率更高", cards, query_parser.resolve_card_names
            ),
            query_parser.is_card_compare_query("火球和毒药哪个胜率更高", cards),
        )
        self.assertEqual(
            is_packaged_card_cooccurrence_query(
                "火球和毒药一起出现多少次", cards, query_parser.resolve_card_names
            ),
            query_parser.is_card_cooccurrence_query("火球和毒药一起出现多少次", cards),
        )
        self.assertEqual(
            is_packaged_card_rank_lookup_query(
                "火球排第几", cards, query_parser.resolve_card_name
            ),
            query_parser.is_card_rank_lookup_query("火球排第几", cards),
        )

    def test_packaged_parser_entities_preserve_form_and_ui_contracts(self):
        cards = sample_cards()
        self.assertIs(query_parser.apply_selected_entity_mode, apply_selected_entity_mode)
        for question in ("觉醒骑士的使用率", "飞刀塔表现如何", "火球胜率"):
            with self.subTest(question=question):
                self.assertEqual(
                    detect_packaged_entity_reference(
                        question, cards, query_parser.resolve_card_name
                    ),
                    query_parser.detect_entity_reference(question, cards),
                )
        self.assertEqual(
            apply_selected_entity_mode(
                {"intent": "card_query", "card_name": "Fireball", "entity_mode": "base8"},
                "loadout_entity",
            ),
            query_parser.apply_selected_entity_mode(
                {"intent": "card_query", "card_name": "Fireball", "entity_mode": "base8"},
                "loadout_entity",
            ),
        )


if __name__ == "__main__":
    unittest.main()
