import unittest

from evaluation.metrics import (
    answer_contains_accuracy,
    failure_rate,
    multi_subquery_accuracy,
    parser_field_accuracy,
    parser_intent_accuracy,
    skill_routing_accuracy,
    summarize_results,
    build_scorecard,
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

    def test_scorecard_unifies_quality_latency_token_and_cost_metrics(self):
        reports = [
            {
                "retrieval_relevant": 4,
                "retrieval_expected": 5,
                "assertions_supported": 8,
                "assertions_total": 10,
                "citations_correct": 3,
                "citations_total": 4,
                "refusal_correct": True,
                "boundary_violations": 0,
                "first_token_latency_ms": 300,
                "total_latency_ms": 1200,
                "timed_out": False,
                "fallback_used": False,
                "token_count": 500,
                "estimated_cost": 0.02,
            },
            {
                "retrieval_relevant": 1,
                "retrieval_expected": 5,
                "assertions_supported": 1,
                "assertions_total": 2,
                "citations_correct": 1,
                "citations_total": 1,
                "refusal_correct": False,
                "boundary_violations": 1,
                "first_token_latency_ms": 500,
                "total_latency_ms": 1800,
                "timed_out": True,
                "fallback_used": True,
                "token_count": 700,
                "estimated_cost": 0.03,
            },
        ]
        dimensions = {
            "snapshot_group_id": "group-1",
            "dataset_scope": "7d_all",
            "deck_mode": "base8",
            "entity_mode": "base8",
            "model": "gpt-5.5",
            "prompt_hash": "abc",
        }

        scorecard = build_scorecard(reports, dimensions=dimensions)

        self.assertEqual(scorecard["retrieval_recall"], 0.5)
        self.assertEqual(scorecard["assertion_support_rate"], 0.75)
        self.assertEqual(scorecard["citation_precision"], 0.8)
        self.assertEqual(scorecard["refusal_accuracy"], 0.5)
        self.assertEqual(scorecard["boundary_violation_rate"], 0.5)
        self.assertEqual(scorecard["first_token_latency_ms"], 400.0)
        self.assertEqual(scorecard["total_latency_ms"], 1500.0)
        self.assertEqual(scorecard["timeout_rate"], 0.5)
        self.assertEqual(scorecard["fallback_rate"], 0.5)
        self.assertEqual(scorecard["token_count"], 1200)
        self.assertEqual(scorecard["estimated_cost"], 0.05)
        self.assertEqual(scorecard["dimensions"], dimensions)
