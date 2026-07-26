import tempfile
import unittest
from pathlib import Path

from rag_quality import (
    GroundingValidationError,
    GroundedStreamBuffer,
    evaluate_rag_quality,
    persist_quality_report,
    validate_answer_grounding,
)


class _Retriever:
    dense_available = True

    def __init__(self, docs, misses=None):
        self.docs = docs
        self.misses = set(misses or [])

    def hybrid_search(self, query, **_kwargs):
        expected = next((doc for doc in self.docs if doc["doc_id"] in query), self.docs[0])
        if expected["doc_id"] in self.misses:
            expected = self.docs[-1]
        return [{"doc": expected, "retrieval_mode": "hybrid", "final_score": 1.0}]


class RAGQualityTests(unittest.TestCase):
    def setUp(self):
        self.docs = [
            {
                "doc_id": "snapshot-1:overview",
                "source_type": "snapshot",
                "text": "snapshot-1:overview contains 20000 battles",
                "metadata": {"snapshot_id": "snapshot-1", "sample_battles": 20000},
            },
            {
                "doc_id": "snapshot-1:card:Electro Giant",
                "source_type": "card",
                "text": "snapshot-1:card:Electro Giant usage rate 4.3% win rate 62.3% over 859 次 appearances",
                "metadata": {"snapshot_id": "snapshot-1", "card_name": "Electro Giant"},
            },
        ]

    def test_quality_gate_requires_one_snapshot_and_probe_recall(self):
        report = evaluate_rag_quality(
            "snapshot-1",
            self.docs,
            _Retriever(self.docs),
            min_documents=2,
            min_source_types=2,
            min_probe_recall=1.0,
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["probe_recall_at_5"], 1.0)

        mixed = [*self.docs, {**self.docs[0], "doc_id": "other:overview", "metadata": {"snapshot_id": "other"}}]
        failed = evaluate_rag_quality(
            "snapshot-1",
            mixed,
            _Retriever(mixed),
            min_documents=2,
            min_source_types=2,
            min_probe_recall=0.5,
        )
        self.assertFalse(failed["passed"])
        self.assertIn("snapshot_mismatch", failed["failures"])

    def test_numeric_and_citation_validation_rejects_unsupported_claims(self):
        evidence = "Electro Giant usage rate 4.3% and 859 次 appearances."
        valid = validate_answer_grounding(
            "使用率 4.3%，样本出场 859 次。参考 snapshot-1:card:Electro Giant",
            evidence,
            {"snapshot-1:card:Electro Giant"},
        )
        self.assertTrue(valid["passed"])

        with self.assertRaises(GroundingValidationError):
            validate_answer_grounding(
                "使用率 99.9%，参考 other-snapshot:card:Fake",
                evidence,
                {"snapshot-1:card:Electro Giant"},
                raise_on_failure=True,
            )

    def test_quality_report_is_persisted_atomically_by_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = persist_quality_report({"snapshot_id": "snapshot/1", "passed": True}, Path(tmp))
            self.assertTrue(path.exists())
            self.assertNotIn("/", path.name)
            self.assertIn('"passed": true', path.read_text(encoding="utf-8"))

    def test_stream_buffer_withholds_unsupported_numeric_sentence(self):
        buffer = GroundedStreamBuffer("usage rate 4.3%", set())
        self.assertEqual(buffer.push("使用率 "), [])
        with self.assertRaises(GroundingValidationError):
            buffer.push("99.9%。")

    def test_citation_followed_by_chinese_punctuation_remains_valid(self):
        report = validate_answer_grounding(
            "证据来自 supercell-s1:deck:1。",
            "evidence",
            {"supercell-s1:deck:1"},
        )
        self.assertTrue(report["passed"])


if __name__ == "__main__":
    unittest.main()
