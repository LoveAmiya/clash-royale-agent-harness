import json
import tempfile
import unittest
from pathlib import Path

from evaluation.citation_benchmark import (
    build_snapshot_citation_cases,
    evaluate_citation_cases,
    run_citation_benchmark,
)


class CitationBenchmarkTests(unittest.TestCase):
    def test_snapshot_documents_generate_balanced_reproducible_citation_cases(self):
        documents = [
            {
                "doc_id": f"snapshot-1:card:{index}",
                "source_type": "card",
                "text": f"Card {index} usage rate {index}.0%.",
                "metadata": {"snapshot_id": "snapshot-1"},
            }
            for index in range(5)
        ] + [
            {
                "doc_id": "snapshot-1:overview",
                "source_type": "snapshot",
                "text": "Official sample has 20000 battles.",
                "metadata": {"snapshot_id": "snapshot-1"},
            }
        ]

        cases = build_snapshot_citation_cases(documents, probes_per_source=3)
        report = evaluate_citation_cases(cases, snapshot_id="snapshot-1")

        self.assertEqual(len(cases), 4)
        self.assertEqual([case["case_id"] for case in cases], [
            "card_001", "card_002", "card_003", "snapshot_001"
        ])
        self.assertTrue(report["passed"])
        self.assertEqual(report["grounding_rate"], 1.0)
        self.assertEqual(report["invalid_citation_rate"], 0.0)

    def test_reports_grounding_and_invalid_citation_rates_with_failure_rows(self):
        cases = [
            {
                "case_id": "grounded",
                "snapshot_id": "snapshot-1",
                "answer": "Usage rate is 4.3%. Source snapshot-1:card:Electro-Giant",
                "evidence": "Usage rate is 4.3%.",
                "allowed_doc_ids": ["snapshot-1:card:Electro-Giant"],
            },
            {
                "case_id": "hallucinated",
                "snapshot_id": "snapshot-1",
                "answer": "Usage rate is 99.9%. Source other:card:Fake",
                "evidence": "Usage rate is 4.3%.",
                "allowed_doc_ids": ["snapshot-1:card:Electro-Giant"],
            },
        ]

        report = evaluate_citation_cases(cases)

        self.assertEqual(report["snapshot_id"], "snapshot-1")
        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["grounding_rate"], 0.5)
        self.assertEqual(report["invalid_citation_rate"], 0.5)
        self.assertTrue(report["rows"][0]["passed"])
        self.assertFalse(report["rows"][1]["passed"])
        self.assertTrue(report["rows"][1]["invalid_citation"])
        self.assertIn("99.9%", report["rows"][1]["unsupported_numeric_facts"])

    def test_runner_persists_a_report_even_when_cases_fail_validation(self):
        cases = [
            {
                "case_id": "missing-citation",
                "snapshot_id": "snapshot-2",
                "answer": "Observed usage is 4.3%.",
                "evidence": "Observed usage is 4.3%.",
                "allowed_doc_ids": ["snapshot-2:card:One"],
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            case_path = Path(temporary) / "cases.json"
            report_path = Path(temporary) / "report.json"
            case_path.write_text(json.dumps(cases), encoding="utf-8")

            report = run_citation_benchmark(case_path, report_path)

            self.assertFalse(report["passed"])
            self.assertTrue(report_path.exists())
            persisted = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["rows"][0]["case_id"], "missing-citation")
            self.assertTrue(persisted["rows"][0]["missing_citations"])
            self.assertTrue(persisted["rows"][0]["grounded"])
            self.assertEqual(persisted["grounding_rate"], 1.0)
            self.assertEqual(persisted["invalid_citation_rate"], 1.0)

    def test_explicit_snapshot_id_marks_mismatched_cases_and_report(self):
        report = evaluate_citation_cases(
            [
                {
                    "case_id": "wrong-snapshot",
                    "snapshot_id": "snapshot-old",
                    "answer": "Source snapshot-old:card:One",
                    "evidence": "evidence",
                    "allowed_doc_ids": ["snapshot-old:card:One"],
                }
            ],
            snapshot_id="snapshot-new",
        )

        self.assertEqual(report["snapshot_id"], "snapshot-new")
        self.assertFalse(report["snapshot_consistent"])
        self.assertFalse(report["passed"])
        self.assertIn("does not match", report["rows"][0]["error"])

    def test_runner_persists_fatal_input_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            case_path = Path(temporary) / "broken.json"
            report_path = Path(temporary) / "report.json"
            case_path.write_text("{not-json", encoding="utf-8")

            report = run_citation_benchmark(case_path, report_path, snapshot_id="snapshot-3")

            self.assertFalse(report["passed"])
            self.assertEqual(report["snapshot_id"], "snapshot-3")
            self.assertIn("JSONDecodeError", report["fatal_error"])
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8"))["fatal_error"],
                report["fatal_error"],
            )


if __name__ == "__main__":
    unittest.main()
