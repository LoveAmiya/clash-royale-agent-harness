import unittest

from evaluation.retrieval_benchmark import build_cases, default_benchmark_index_path, score_ranking


class RetrievalBenchmarkTests(unittest.TestCase):
    def test_default_index_is_isolated_from_the_runtime_qdrant_directory(self):
        docs = [{"metadata": {"snapshot_id": "official-1"}}]

        path = default_benchmark_index_path(docs)

        self.assertEqual(path.parts[:2], ("data", "retrieval_benchmark_qdrant"))
        self.assertNotIn("daily_snapshot_qdrant", path.parts)

    def test_scores_recall_and_reciprocal_rank_at_k(self):
        metrics = score_ranking(
            [
                {"case_id": "one", "relevant_doc_id": "doc-a", "retrieved_doc_ids": ["doc-x", "doc-a"]},
                {"case_id": "two", "relevant_doc_id": "doc-b", "retrieved_doc_ids": ["doc-x", "doc-y"]},
            ],
            k=5,
        )

        self.assertEqual(metrics["case_count"], 2)
        self.assertEqual(metrics["hits_at_k"], 1)
        self.assertEqual(metrics["recall_at_k"], 0.5)
        self.assertEqual(metrics["mrr_at_k"], 0.25)

    def test_ignores_results_beyond_cutoff(self):
        metrics = score_ranking(
            [{"case_id": "one", "relevant_doc_id": "doc-a", "retrieved_doc_ids": ["x", "y", "z", "w", "v", "doc-a"]}],
            k=5,
        )

        self.assertEqual(metrics["recall_at_k"], 0.0)
        self.assertEqual(metrics["mrr_at_k"], 0.0)

    def test_build_cases_uses_current_official_snapshot_evidence_types(self):
        docs = [
            {
                "doc_id": "snapshot-1",
                "source_type": "snapshot",
                "metadata": {"snapshot_id": "snapshot-1", "sample_battles": 20000},
            },
            {
                "doc_id": "card-1",
                "source_type": "card_profile",
                "metadata": {"card_name": "Electro Giant"},
            },
            {
                "doc_id": "deck-1",
                "source_type": "deck_profile",
                "metadata": {"deck_name": "Electro Giant / Tornado"},
            },
            {
                "doc_id": "pair-1",
                "source_type": "card_pair",
                "metadata": {"cards": ["Electro Giant", "Tornado"]},
            },
            {
                "doc_id": "counter-1",
                "source_type": "counter",
                "metadata": {"card_name": "Electro Giant", "opponent_card_name": "P.E.K.K.A"},
            },
            {
                "doc_id": "matchup-1",
                "source_type": "matchup",
                "metadata": {"deck_name": "Deck A", "opponent_deck_name": "Deck B"},
            },
            {
                "doc_id": "archetype-1",
                "source_type": "archetype",
                "metadata": {"archetype": "Hog cycle"},
            },
        ]

        cases = build_cases(docs)

        self.assertEqual({case["relevant_doc_id"] for case in cases}, {doc["doc_id"] for doc in docs})
        self.assertEqual({case["source_type"] for case in cases}, {doc["source_type"] for doc in docs})
        self.assertTrue(all(case["query"] for case in cases))


if __name__ == "__main__":
    unittest.main()
