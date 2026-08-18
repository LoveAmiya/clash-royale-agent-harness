import unittest

from support import install_test_stubs

install_test_stubs()

import query_answering
import runtime_multi
from clashroyale_agent.qa.answer_routing import (
    query_needs_rag as packaged_query_needs_rag,
    subquery_needs_rag as packaged_subquery_needs_rag,
    subquery_title as packaged_subquery_title,
    subquery_user_text as packaged_subquery_user_text,
)


class QAAnswerRoutingTests(unittest.TestCase):
    def test_card_scoped_deck_query_never_requires_rag(self):
        parsed = {
            "intent": "deck_query",
            "card_name": "Royal Giant",
            "deck_cards": None,
            "rank": None,
            "top_n": None,
        }

        self.assertFalse(packaged_query_needs_rag(parsed))
        self.assertFalse(packaged_subquery_needs_rag(parsed))

    def test_unspecified_deck_analysis_still_requires_rag(self):
        parsed = {
            "intent": "deck_query",
            "card_name": None,
            "deck_cards": None,
            "rank": None,
            "top_n": None,
        }

        self.assertTrue(packaged_query_needs_rag(parsed))
        self.assertTrue(packaged_subquery_needs_rag(parsed))

    def test_packaged_query_needs_rag_matches_runtime_compatibility_entry(self):
        cases = [
            {"intent": "meta_analysis_query"},
            {"intent": "match_preparation_query"},
            {"intent": "deck_query", "rank": 1, "top_n": None},
            {"intent": "deck_query", "card_name": "Electro Giant", "rank": None, "top_n": None},
            {"intent": "deck_query", "card_name": None, "rank": None, "top_n": None},
            {"intent": "card_query", "card_name": "Fireball"},
            {"intent": "card_query", "card_name": None, "rank": None, "top_n": None},
            {
                "intent": "multi_intent",
                "subqueries": [
                    {"intent": "card_query", "card_name": "Fireball"},
                    {"intent": "meta_analysis_query"},
                ],
            },
        ]

        for parsed in cases:
            with self.subTest(parsed=parsed):
                self.assertEqual(
                    packaged_query_needs_rag(parsed),
                    runtime_multi.query_needs_rag(parsed),
                )

    def test_packaged_subquery_routing_matches_query_answering_entry(self):
        cases = [
            {"intent": "meta_analysis_query"},
            {"intent": "deck_query", "deck_cards": None, "rank": None, "top_n": None},
            {"intent": "card_query", "entity_mode": "loadout_entity", "card_name": None},
            {"intent": "card_query", "entity_mode": "base8", "card_name": None, "rank": None, "top_n": None},
        ]

        for parsed in cases:
            with self.subTest(parsed=parsed):
                self.assertEqual(
                    packaged_subquery_needs_rag(parsed),
                    query_answering.subquery_needs_rag(parsed),
                )

    def test_packaged_title_and_user_text_match_query_answering_entries(self):
        question = "原始问题"
        cases = [
            {"intent": "meta_analysis_query"},
            {"intent": "card_query", "card_name": "Fireball", "metrics": ["usage_rate"]},
            {"intent": "card_compare_query", "card_names": ["Fireball", "Poison"], "compare_metric": "usage_rate"},
            {"intent": "card_cooccurrence_query", "card_name": "Giant", "card_names": None},
            {"intent": "deck_query", "card_name": "Electro Giant", "rank": None, "top_n": None},
            {"intent": "match_preparation_query"},
        ]

        for parsed in cases:
            with self.subTest(parsed=parsed):
                self.assertEqual(packaged_subquery_title(parsed), query_answering.subquery_title(parsed))
                self.assertEqual(
                    packaged_subquery_user_text(parsed, question),
                    query_answering.subquery_user_text(parsed, question),
                )


if __name__ == "__main__":
    unittest.main()
