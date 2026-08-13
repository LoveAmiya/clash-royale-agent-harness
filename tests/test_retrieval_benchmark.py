import unittest

from evaluation.retrieval_benchmark import (
    attach_corpus_identity,
    bootstrap_mean_ci,
    build_cases,
    compare_paired_rankings,
    default_benchmark_index_path,
    evaluate_cases,
    paired_cohens_d,
    score_ranking,
)


class RetrievalBenchmarkTests(unittest.TestCase):
    def test_quality_report_is_bound_to_snapshot_and_document_fingerprint(self):
        retriever = type(
            "RetrieverIdentity",
            (),
            {"snapshot_id": "official-1", "docs_fingerprint": "fingerprint-1"},
        )()

        report = attach_corpus_identity({"case_count": 1}, retriever)

        self.assertEqual(report["snapshot_id"], "official-1")
        self.assertEqual(report["docs_fingerprint"], "fingerprint-1")

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
        self.assertEqual(
            metrics["case_scores"],
            [
                {"case_id": "one", "recall_at_k": 1, "reciprocal_rank": 0.5},
                {"case_id": "two", "recall_at_k": 0, "reciprocal_rank": 0.0},
            ],
        )

    def test_bootstrap_confidence_interval_is_deterministic(self):
        first = bootstrap_mean_ci([0.0, 0.5, 1.0, 1.0], iterations=500, seed=73)
        second = bootstrap_mean_ci([0.0, 0.5, 1.0, 1.0], iterations=500, seed=73)

        self.assertEqual(first, second)
        self.assertEqual(first["seed"], 73)
        self.assertEqual(first["iterations"], 500)
        self.assertLessEqual(first["lower"], first["mean"])
        self.assertGreaterEqual(first["upper"], first["mean"])

    def test_paired_comparison_uses_aligned_case_scores(self):
        baseline = [
            {"case_id": "a", "recall_at_k": 0, "reciprocal_rank": 0.0},
            {"case_id": "b", "recall_at_k": 1, "reciprocal_rank": 0.5},
            {"case_id": "c", "recall_at_k": 0, "reciprocal_rank": 0.0},
        ]
        treatment = [
            {"case_id": "c", "recall_at_k": 1, "reciprocal_rank": 1.0},
            {"case_id": "a", "recall_at_k": 1, "reciprocal_rank": 0.5},
            {"case_id": "b", "recall_at_k": 1, "reciprocal_rank": 1.0},
        ]

        report = compare_paired_rankings(baseline, treatment, iterations=500, seed=19)

        self.assertEqual(report["case_count"], 3)
        self.assertAlmostEqual(report["recall_at_k"]["mean_delta"], 2 / 3)
        self.assertAlmostEqual(report["reciprocal_rank"]["mean_delta"], 2 / 3)
        self.assertIsNotNone(report["recall_at_k"]["paired_cohens_d"])
        self.assertEqual(report["recall_at_k"]["ci95"]["seed"], 19)
        self.assertEqual(
            report["case_scores"][0],
            {
                "case_id": "a",
                "baseline_recall_at_k": 0.0,
                "treatment_recall_at_k": 1.0,
                "recall_at_k_delta": 1.0,
                "baseline_reciprocal_rank": 0.0,
                "treatment_reciprocal_rank": 0.5,
                "reciprocal_rank_delta": 0.5,
            },
        )

    def test_paired_cohens_d_reports_zero_for_identical_pairs(self):
        self.assertEqual(paired_cohens_d([1.0, 0.0], [1.0, 0.0]), 0.0)

    def test_evaluate_cases_reports_all_ablation_variants_and_paired_comparisons(self):
        relevant = {
            "doc_id": "card-1",
            "source_type": "card_profile",
            "text": "Electro Giant usage profile",
            "metadata": {"card_name": "Electro Giant"},
        }
        distractor = {
            "doc_id": "card-2",
            "source_type": "card_profile",
            "text": "another card",
            "metadata": {"card_name": "Other"},
        }

        class FakeRetriever:
            dense_available = True

            def __init__(self):
                self.bm25_calls = []
                self.hybrid_calls = []
                self.embed_batches = []

            def embed_texts(self, texts):
                self.embed_batches.append(list(texts))
                return [[0.0] * 1024 for _ in texts]

            def bm25_search(self, *_args, **_kwargs):
                self.bm25_calls.append(_kwargs)
                return [{"doc": distractor, "score": 1.0}, {"doc": relevant, "score": 0.5}]

            def hybrid_search(self, *_args, **_kwargs):
                self.hybrid_calls.append(_kwargs)
                return [
                    {"doc": distractor, "final_score": 0.7},
                    {"doc": relevant, "final_score": 0.6},
                ]

        retriever = FakeRetriever()
        report = evaluate_cases(
            retriever,
            [
                {
                    "case_id": "one",
                    "query": "Electro Giant usage profile",
                    "parsed": {"card_name": "Electro Giant"},
                    "relevant_doc_id": "card-1",
                    "source_type": "card_profile",
                    "dataset_scope": "35d_all",
                }
            ],
            k=1,
            bootstrap_iterations=100,
            bootstrap_seed=7,
        )

        self.assertEqual(set(report["methods"]), {"bm25", "hybrid", "hybrid_rerank"})
        self.assertTrue(report["ablation"]["hybrid_rerank_includes_metadata_bonus"])
        self.assertEqual(
            set(report["paired_comparisons"]),
            {"hybrid_vs_bm25", "hybrid_rerank_vs_hybrid", "hybrid_rerank_vs_bm25"},
        )
        self.assertEqual(report["methods"]["hybrid_rerank"]["metrics"]["recall_at_k"], 1.0)
        self.assertEqual(retriever.bm25_calls[0]["dataset_scope"], "35d_all")
        self.assertEqual(retriever.hybrid_calls[0]["dataset_scope"], "35d_all")
        self.assertEqual(retriever.hybrid_calls[0]["fusion_mode"], "rrf")
        self.assertEqual(len(retriever.embed_batches), 1)
        self.assertEqual(len(retriever.hybrid_calls[0]["query_vector"]), 1024)
        self.assertGreater(retriever.hybrid_calls[0]["top_k_bm25"], 10)
        self.assertIn("latency_ms", report["methods"]["hybrid"])
        self.assertIn("p95", report["methods"]["hybrid"]["latency_ms"])
        self.assertIn("query_embedding_ms", report["runtime"])

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
                "metadata": {"snapshot_id": "snapshot-1", "sample_battles": 20000, "dataset_scope": "35d_all"},
            },
            {
                "doc_id": "card-1",
                "source_type": "card_profile",
                "metadata": {"card_name": "Electro Giant", "dataset_scope": "35d_all"},
            },
            {
                "doc_id": "deck-1",
                "source_type": "deck_profile",
                "metadata": {"deck_name": "Electro Giant / Tornado", "dataset_scope": "35d_all"},
            },
            {
                "doc_id": "pair-1",
                "source_type": "card_pair",
                "metadata": {"cards": ["Electro Giant", "Tornado"], "dataset_scope": "35d_all"},
            },
            {
                "doc_id": "counter-1",
                "source_type": "counter",
                "metadata": {"card_name": "Electro Giant", "opponent_card_name": "P.E.K.K.A", "dataset_scope": "35d_all"},
            },
            {
                "doc_id": "matchup-1",
                "source_type": "matchup",
                "metadata": {"deck_name": "Deck A", "opponent_deck_name": "Deck B", "dataset_scope": "35d_all"},
            },
            {
                "doc_id": "archetype-1",
                "source_type": "archetype",
                "metadata": {"archetype": "Hog cycle", "dataset_scope": "35d_all"},
            },
        ]

        cases = build_cases(docs)

        self.assertEqual({case["relevant_doc_id"] for case in cases}, {doc["doc_id"] for doc in docs})
        self.assertEqual({case["source_type"] for case in cases}, {doc["source_type"] for doc in docs})
        self.assertTrue(all(case["query"] for case in cases))
        self.assertTrue(all(case["dataset_scope"] == "35d_all" for case in cases))


if __name__ == "__main__":
    unittest.main()
