import tempfile
import unittest
from pathlib import Path

from rag_quality import (
    GroundingValidationError,
    GroundedStreamBuffer,
    evaluate_rag_quality,
    persist_quality_report,
    validate_answer_grounding,
    _probe_query,
)


class _Retriever:
    dense_available = True

    def __init__(self, docs, misses=None):
        self.docs = docs
        self.misses = set(misses or [])

    def hybrid_search(self, query, **_kwargs):
        lowered = query.lower()
        expected = next(
            (
                doc
                for doc in self.docs
                if any(
                    str(value).lower() in lowered
                    for key, value in doc.get("metadata", {}).items()
                    if key in {"card_name", "deck_name", "opponent_deck_name", "archetype"}
                    and isinstance(value, str)
                    and value
                )
            ),
            self.docs[0],
        )
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

    def test_quality_probe_uses_user_slots_without_leaking_doc_id_or_document_text(self):
        doc = self.docs[1]
        query = _probe_query(doc)

        self.assertIn("Electro Giant", query)
        self.assertIn("usage rate", query)
        self.assertNotIn(doc["doc_id"], query)
        self.assertNotIn(doc["text"], query)

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

    def test_numeric_validation_binds_metric_value_to_named_entity(self):
        evidence = (
            "卡牌：Electro Giant；使用率：4.3%；胜率：62.3%\n"
            "卡牌：Poison；使用率：5.5%；胜率：51.2%"
        )
        report = validate_answer_grounding(
            "Electro Giant 的使用率是 5.5%。参考 snapshot-1:card:Electro Giant",
            evidence,
            {"snapshot-1:card:Electro Giant"},
        )
        self.assertFalse(report["passed"])
        self.assertIn("Electro Giant|usage_rate|5.5%", report["unsupported_numeric_claims"])

    def test_numeric_validation_normalizes_thousands_separators(self):
        report = validate_answer_grounding(
            "该结论来自 20,000 场官方样本。参考 snapshot-1:overview",
            "snapshot_id=snapshot-1 | sample_battles=20000 场",
            {"snapshot-1:overview"},
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["unsupported_numeric_facts"], [])

    def test_numeric_validation_normalizes_english_appearances_to_chinese_count_unit(self):
        report = validate_answer_grounding(
            "Skeletons 样本出场 6610 次。来源 snapshot-1:card:Skeletons",
            "Card evidence. Skeletons had 6610 appearances.",
            {"snapshot-1:card:Skeletons"},
        )

        self.assertTrue(report["passed"], report)
        self.assertEqual(report["unsupported_numeric_facts"], [])

    def test_quality_gate_samples_multiple_documents_per_source_type(self):
        docs = [
            {
                "doc_id": f"snapshot-1:card:{index}",
                "source_type": "card",
                "text": f"card {index}",
                "metadata": {"snapshot_id": "snapshot-1", "card_name": f"Card {index}"},
            }
            for index in range(3)
        ] + [
            {
                "doc_id": f"snapshot-1:deck:{index}",
                "source_type": "deck",
                "text": f"deck {index}",
                "metadata": {"snapshot_id": "snapshot-1", "deck_name": f"Deck {index}"},
            }
            for index in range(3)
        ]
        report = evaluate_rag_quality(
            "snapshot-1",
            docs,
            _Retriever(docs, misses={"snapshot-1:card:0", "snapshot-1:card:1"}),
            min_documents=6,
            min_source_types=2,
            min_probe_recall=0.6,
            probes_per_source=3,
        )
        self.assertEqual(report["probe_count"], 6)
        self.assertEqual(report["probe_recall_by_source"]["card"], 1 / 3)
        self.assertGreaterEqual(report["probe_recall_at_5"], 0.6)
        self.assertIn("source_probe_recall_below_threshold:card", report["failures"])
        self.assertFalse(report["passed"])

    def test_quality_gate_rejects_missing_snapshot_metrics_before_activation(self):
        docs = [
            {
                "doc_id": "snapshot-1:card:1:Skeletons",
                "source_type": "card",
                "text": "Card evidence. Skeletons usage rate None%; win rate None%.",
                "metadata": {
                    "snapshot_id": "snapshot-1",
                    "card_name": "Skeletons",
                    "rank": 1,
                },
            },
            {
                "doc_id": "snapshot-1:deck:1:Broken",
                "source_type": "deck",
                "text": "Deck evidence. Broken sampled games None; win rate None%.",
                "metadata": {
                    "snapshot_id": "snapshot-1",
                    "deck_name": "Broken",
                    "rank": 1,
                },
            },
        ]

        report = evaluate_rag_quality(
            "snapshot-1",
            docs,
            _Retriever(docs),
            min_documents=2,
            min_source_types=2,
            min_probe_recall=0.0,
        )

        self.assertFalse(report["passed"])
        self.assertIn("invalid_evidence_fields", report["failures"])
        self.assertEqual(
            report["invalid_evidence_doc_ids"],
            ["snapshot-1:card:1:Skeletons", "snapshot-1:deck:1:Broken"],
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

    def test_stream_buffer_can_drop_only_unsupported_numeric_sentences(self):
        buffer = GroundedStreamBuffer(
            "usage rate 4.3%",
            set(),
            drop_unsupported=True,
        )

        chunks = buffer.push("可保留结论。使用率 99.9%。后续结论。")
        chunks += buffer.finish()

        self.assertEqual("".join(chunks), "可保留结论。后续结论。")
        self.assertEqual(buffer.dropped_count, 1)

    def test_stream_buffer_stops_before_model_generated_reference_section(self):
        buffer = GroundedStreamBuffer(
            "usage rate 4.3%",
            set(),
            stop_markers=("参考来源：",),
        )

        chunks = buffer.push("结论基于 usage rate 4.3%。\n参考来")
        chunks += buffer.push("源：\n[1] unverified")
        chunks += buffer.finish()

        self.assertEqual("".join(chunks), "结论基于 usage rate 4.3%。\n")
        self.assertTrue(buffer.stopped)

    def test_citation_followed_by_chinese_punctuation_remains_valid(self):
        report = validate_answer_grounding(
            "证据来自 supercell-s1:deck:1。",
            "evidence",
            {"supercell-s1:deck:1"},
        )
        self.assertTrue(report["passed"])


if __name__ == "__main__":
    unittest.main()
