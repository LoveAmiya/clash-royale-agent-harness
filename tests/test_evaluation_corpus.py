import unittest

from evaluation.run_eval import evaluate_case, load_cases
from skills.registry import build_default_registry
from support import sample_cards, sample_decks, sample_schedule


CASES = load_cases()
REQUIRED_CATEGORIES = {
    "card_metric_english",
    "card_metric_multiple",
    "card_alias_chinese",
    "card_comparison_english",
    "card_rank_lookup_english",
    "deck_ranking_english",
    "removed_clan_war_feature",
    "out_of_domain_rejection",
    "rag_route_optional",
    "multi_intent_decomposition",
}


class EvaluationCorpusContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule = sample_schedule()
        cls.decks = sample_decks()
        cls.cards = sample_cards()
        cls.registry = build_default_registry()

    def test_corpus_is_static_unique_and_covers_required_product_capabilities(self):
        case_ids = [case["id"] for case in CASES]
        categories = {case["category"] for case in CASES}

        self.assertGreaterEqual(len(CASES), 300)
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertTrue(REQUIRED_CATEGORIES.issubset(categories))


def _make_case_test(case: dict):
    def test_case(self):
        result = evaluate_case(
            case,
            self.registry,
            self.schedule,
            self.decks,
            self.cards,
        )
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(result["id"], case["id"])

    test_case.__name__ = "test_" + case["id"].replace("-", "_")
    test_case.__doc__ = f"Evaluation corpus case: {case['id']}"
    return test_case


for _case in CASES:
    setattr(EvaluationCorpusContractTests, "test_" + _case["id"].replace("-", "_"), _make_case_test(_case))


if __name__ == "__main__":
    unittest.main()
