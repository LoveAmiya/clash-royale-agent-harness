import json
import tempfile
import unittest
from pathlib import Path

from evaluation.run_eval import evaluate_case, load_json, run_evaluation
from skills.registry import build_default_registry


ROOT = Path(__file__).resolve().parents[1]


class EvaluationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.schedule = load_json(ROOT / "data" / "schedule.json")
        self.decks = load_json(ROOT / "data" / "top_decks.json")
        self.cards = load_json(ROOT / "data" / "cards_meta.json")
        self.registry = build_default_registry()

    def test_expectation_mismatches_are_reported_as_case_failures(self):
        case = {
            "id": "intentional-mismatch",
            "category": "regression_guard",
            "question": "Fireball win rate",
            "expected_intent": "deck_query",
            "expected_skill": "DeckRankingSkill",
            "expected_fields": {"card_name": "Poison"},
            "answer_contains": ["this fragment must never appear"],
        }

        result = evaluate_case(case, self.registry, self.schedule, self.decks, self.cards)

        self.assertFalse(result["success"])
        self.assertIn("parsed_intent", result["errors"])
        self.assertIn("selected_skill", result["errors"])
        self.assertIn("card_name", result["errors"])
        self.assertIn("answer_contains", result["errors"])

    def test_failed_cases_are_preserved_in_a_json_report(self):
        case = {
            "id": "persisted-failure",
            "category": "regression_guard",
            "question": "Fireball win rate",
            "expected_intent": "reject",
            "expected_skill": None,
            "expected_fields": {},
            "answer_contains": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "evaluation-report.json"
            report = run_evaluation(
                cases=[case],
                report_path=report_path,
                registry=self.registry,
                schedule_data=self.schedule,
                deck_data=self.decks,
                card_data=self.cards,
            )
            persisted = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["summary"]["failed_cases"], 1)
        self.assertEqual(persisted["failures"][0]["id"], "persisted-failure")
        self.assertFalse(persisted["failures"][0]["success"])

    def test_optional_rag_synthesis_is_routed_but_not_executed(self):
        case = {
            "id": "optional-rag-synthesis",
            "category": "rag_routing",
            "question": "current meta analysis",
            "expected_intent": "meta_analysis_query",
            "expected_skill": "EvidenceSynthesisSkill",
            "expected_fields": {},
            "answer_contains": [],
            "optional": True,
        }

        result = evaluate_case(case, self.registry, self.schedule, self.decks, self.cards)

        self.assertTrue(result["skipped"])
        self.assertTrue(result["success"], result["error"])


if __name__ == "__main__":
    unittest.main()
