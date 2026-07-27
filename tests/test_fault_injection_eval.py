import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class FaultInjectionEvaluationTests(unittest.TestCase):
    def test_default_corpus_has_at_least_twenty_scenarios_across_required_families(self):
        from evaluation.run_fault_injection import REQUIRED_CATEGORIES, load_scenarios

        scenarios = load_scenarios()

        self.assertGreaterEqual(len(scenarios), 20)
        self.assertEqual(REQUIRED_CATEGORIES, {item["category"] for item in scenarios})
        self.assertEqual(len(scenarios), len({item["id"] for item in scenarios}))

    def test_report_is_explicitly_synthetic_and_contains_quantitative_results(self):
        from evaluation.run_fault_injection import run_evaluation

        report = run_evaluation()

        self.assertEqual("synthetic_fault_injection", report["evaluation_type"])
        self.assertFalse(report["external_requests_enabled"])
        self.assertEqual(len(report["results"]), report["summary"]["total_scenarios"])
        self.assertGreaterEqual(report["summary"]["total_scenarios"], 20)
        self.assertIn("success_rate", report["summary"])
        self.assertIn("total_repeated_external_requests", report["summary"])
        self.assertIn("average_recovery_or_handling_latency_ms", report["summary"])
        for result in report["results"]:
            self.assertIsInstance(result["success"], bool)
            self.assertIsInstance(result["successful_degradation_or_recovery"], bool)
            self.assertIsInstance(result["repeated_external_requests"], int)
            self.assertGreaterEqual(result["repeated_external_requests"], 0)
            self.assertIsInstance(result["recovery_or_handling_latency_ms"], int)
            self.assertGreaterEqual(result["recovery_or_handling_latency_ms"], 0)
            self.assertIn("actual", result)
            self.assertIn("expected", result)

    def test_injected_failures_are_retained_in_report_and_written_to_disk(self):
        from evaluation.run_fault_injection import run_evaluation

        scenario = {
            "id": "intentional-mismatch",
            "category": "stream_unavailable_fallback",
            "variant": "stream_unavailable",
            "input": {"fallback_available": True, "fallback_latency_ms": 12},
            "expected": {
                "outcome": "streaming",
                "successful_degradation_or_recovery": True,
                "repeated_external_requests": 0,
                "recovery_or_handling_latency_ms": 12,
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "fault-report.json"
            report = run_evaluation(scenarios=[scenario], report_path=report_path)
            stored = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(1, report["summary"]["failed_scenarios"])
        self.assertEqual("intentional-mismatch", report["failures"][0]["id"])
        self.assertTrue(report["failures"][0]["errors"])
        self.assertEqual(report["failures"], stored["failures"])

    def test_runner_does_not_use_network_clients(self):
        from evaluation.run_fault_injection import run_evaluation

        with mock.patch("socket.create_connection", side_effect=AssertionError("network used")), mock.patch(
            "urllib.request.urlopen", side_effect=AssertionError("network used")
        ):
            report = run_evaluation()

        self.assertEqual(0, report["summary"]["failed_scenarios"])

    def test_representative_scenarios_measure_recovery_and_protection(self):
        from evaluation.run_fault_injection import run_evaluation

        report = run_evaluation()
        results = {item["id"]: item for item in report["results"]}

        self.assertEqual("fallback_chunked", results["stream-unavailable-fallback"]["actual"]["outcome"])
        self.assertEqual("cooldown", results["supercell-repeated-429-cooldown"]["actual"]["outcome"])
        self.assertEqual(0, results["supercell-cooldown-short-circuit"]["repeated_external_requests"])
        self.assertEqual("bm25_only", results["snapshot-rag-misaligned"]["actual"]["outcome"])
        self.assertEqual("rejected", results["grounding-number-mismatch"]["actual"]["outcome"])


if __name__ == "__main__":
    unittest.main()
