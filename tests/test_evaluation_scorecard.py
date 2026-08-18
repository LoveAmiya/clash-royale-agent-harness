import unittest

from evaluation.scorecard import build_unified_scorecard, normalize_report


class EvaluationScorecardTests(unittest.TestCase):
    def test_normalizes_retrieval_and_citation_reports(self):
        retrieval = {
            "benchmark": "Official snapshot RAG retrieval benchmark",
            "snapshot_id": "snapshot-1",
            "methods": {
                "hybrid_rerank": {
                    "metrics": {"case_count": 10, "hits_at_k": 8}
                }
            },
        }
        citation = {
            "benchmark": "Snapshot RAG answer grounding and citation benchmark",
            "snapshot_id": "snapshot-1",
            "case_count": 2,
            "grounded_count": 1,
            "rows": [
                {
                    "cited_doc_ids": ["a", "b"],
                    "unknown_citations": [],
                    "missing_citations": False,
                },
                {
                    "cited_doc_ids": ["c"],
                    "unknown_citations": ["x"],
                    "missing_citations": False,
                },
            ],
        }

        rows = normalize_report(retrieval, source="retrieval.json")
        rows.extend(normalize_report(citation, source="citation.json"))
        scorecard = build_unified_scorecard(
            [retrieval, citation],
            dimensions={"dataset_scope": "7d_all"},
            sources=["retrieval.json", "citation.json"],
        )

        self.assertEqual(rows[0]["retrieval_relevant"], 8)
        self.assertEqual(scorecard["retrieval_recall"], 0.8)
        self.assertEqual(scorecard["assertion_support_rate"], 0.5)
        self.assertEqual(scorecard["citation_precision"], 0.75)
        self.assertEqual(scorecard["dimensions"]["snapshot_id"], "snapshot-1")
        self.assertEqual(scorecard["sources"], ["retrieval.json", "citation.json"])

    def test_normalizes_fault_and_live_parser_boundaries_without_question_text(self):
        fault = {
            "evaluation_type": "synthetic_fault_injection",
            "results": [
                {"success": True, "recovery_or_handling_latency_ms": 4},
                {"success": False, "recovery_or_handling_latency_ms": 6},
            ],
        }
        live = {
            "benchmark": "Live structured-query parser",
            "results": [
                {
                    "question": "private text",
                    "expected_intent": "reject",
                    "success": True,
                    "elapsed_seconds": 0.25,
                },
                {
                    "question": "more private text",
                    "expected_intent": "card_query",
                    "success": True,
                    "elapsed_seconds": 0.75,
                },
            ],
        }

        scorecard = build_unified_scorecard([fault, live])

        self.assertEqual(scorecard["boundary_violation_rate"], 0.5)
        self.assertEqual(scorecard["refusal_accuracy"], 1.0)
        self.assertEqual(scorecard["total_latency_ms"], 252.5)
        self.assertNotIn("question", str(scorecard).lower())
        self.assertEqual(scorecard["metric_coverage"]["boundary_violation_rate"], 2)

    def test_normalizes_qa_performance_without_retaining_question_text(self):
        report = {
            "benchmark": "QA response performance benchmark",
            "results": [
                {
                    "question": "private question",
                    "first_token_latency_ms": 120,
                    "total_latency_ms": 260,
                    "timed_out": False,
                    "fallback_used": True,
                }
            ],
        }

        scorecard = build_unified_scorecard([report])

        self.assertEqual(scorecard["first_token_latency_ms"], 120.0)
        self.assertEqual(scorecard["total_latency_ms"], 260.0)
        self.assertEqual(scorecard["timeout_rate"], 0.0)
        self.assertEqual(scorecard["fallback_rate"], 1.0)
        self.assertNotIn("question", str(scorecard).lower())


if __name__ == "__main__":
    unittest.main()
