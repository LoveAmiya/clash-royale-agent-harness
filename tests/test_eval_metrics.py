import unittest

from evaluation.metrics import (
    answer_contains_accuracy,
    failure_rate,
    multi_subquery_accuracy,
    parser_field_accuracy,
    parser_intent_accuracy,
    skill_routing_accuracy,
    summarize_results,
)


class EvalMetricsTests(unittest.TestCase):
    def setUp(self):
        self.results = [
            {
                "expected_intent": "schedule_query",
                "parsed_intent": "schedule_query",
                "expected_fields": {"round": 5},
                "parsed": {"round": 5},
                "expected_skill": "ScheduleQuerySkill",
                "selected_skill": "ScheduleQuerySkill",
                "answer_contains": ["schedule.json", "第 5 轮"],
                "answer": "第 5 轮 ... schedule.json",
                "success": True,
                "skipped": False,
            },
            {
                "expected_intent": "card_query",
                "parsed_intent": "reject",
                "expected_fields": {"card_name": "Fireball"},
                "parsed": {"card_name": "Poison"},
                "expected_skill": "CardMetaSkill",
                "selected_skill": "RAGEvidenceSkill",
                "answer_contains": ["Fireball"],
                "answer": "Poison",
                "success": False,
                "skipped": False,
            },
            {
                "expected_intent": "deck_query",
                "parsed_intent": "deck_query",
                "expected_fields": {},
                "parsed": {},
                "expected_skill": "RAGEvidenceSkill",
                "selected_skill": "RAGEvidenceSkill",
                "answer_contains": [],
                "answer": "",
                "success": True,
                "skipped": True,
            },
        ]

    def test_parser_intent_accuracy(self):
        self.assertEqual(parser_intent_accuracy(self.results), 0.5)

    def test_parser_field_accuracy(self):
        self.assertEqual(parser_field_accuracy(self.results), 0.5)

    def test_skill_routing_accuracy(self):
        self.assertEqual(skill_routing_accuracy(self.results), 0.5)

    def test_answer_contains_accuracy(self):
        self.assertEqual(answer_contains_accuracy(self.results), 2 / 3)

    def test_failure_rate(self):
        self.assertEqual(failure_rate(self.results), 0.5)

    def test_summarize_results(self):
        summary = summarize_results(self.results)

        self.assertEqual(summary["total_cases"], 3)
        self.assertEqual(summary["skipped_cases"], 1)
        self.assertEqual(summary["parser_intent_accuracy"], 0.5)

    def test_multi_subquery_accuracy(self):
        results = [
            {
                "expected_subqueries": [{"intent": "card_query"}],
                "parsed_subqueries": [{"intent": "card_query"}],
                "skipped": False,
            },
            {
                "expected_subqueries": [{"intent": "meta_analysis_query"}],
                "parsed_subqueries": [{"intent": "deck_query"}],
                "skipped": False,
            },
        ]

        self.assertEqual(multi_subquery_accuracy(results), 0.5)
