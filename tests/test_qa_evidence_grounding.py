import unittest

from support import install_test_stubs

install_test_stubs()

import app_config  # noqa: F401 - loads the src package path for direct package imports.
from clashroyale_agent.qa.evidence_grounding import (
    append_references_if_missing,
    build_evidence_ledger,
    build_reference_suffix,
    validate_ledger_grounding,
)
from rag_quality import validate_answer_grounding
from retrieval_postprocess import build_context_and_refs


def _result(doc_id="snapshot-1:card:Fireball"):
    return {
        "doc": {
            "doc_id": doc_id,
            "source_type": "card",
            "metadata": {
                "source": "unit-test",
                "card_name": "Fireball",
                "usage_rate": 4.0,
                "win_rate": 55.0,
                "appearance_count": 10,
            },
            "text": "Card: Fireball; usage rate: 4.0%; win rate: 55.0%; 10 appearances",
        },
        "compressed_text": "Card: Fireball; usage rate: 4.0%; win rate: 55.0%; 10 appearances",
        "rerank_score": 0.95,
    }


class EvidenceGroundingTests(unittest.TestCase):
    def test_ledger_matches_legacy_context_and_reference_builder(self):
        results = [_result()]

        ledger = build_evidence_ledger(results, start_index=3)
        legacy_context, legacy_refs = build_context_and_refs(results, start_index=3)

        self.assertEqual(ledger.context, legacy_context)
        self.assertEqual(ledger.references, legacy_refs)
        self.assertEqual(ledger.allowed_doc_ids, {"snapshot-1:card:Fireball"})
        self.assertEqual(ledger.grounding_evidence, legacy_context)

    def test_ledger_combines_structured_and_retrieved_evidence_for_validation(self):
        ledger = build_evidence_ledger([_result()], structured_evidence="Structured evidence: 10 appearances")
        answer = (
            "Fireball usage rate is 4.0% with 10 appearances. "
            "snapshot-1:card:Fireball"
        )

        packaged = validate_ledger_grounding(answer, ledger)
        legacy = validate_answer_grounding(answer, ledger.grounding_evidence, ledger.allowed_doc_ids)

        self.assertEqual(packaged, legacy)
        self.assertTrue(packaged["passed"])

    def test_reference_suffix_and_append_match_existing_runtime_contract(self):
        ledger = build_evidence_ledger([_result()])

        self.assertEqual(
            build_reference_suffix(ledger),
            f"\n\n\u53c2\u8003\u6765\u6e90\uff1a\n{ledger.references}",
        )
        self.assertEqual(
            append_references_if_missing("answer", ledger),
            f"answer\n\n\u53c2\u8003\u6765\u6e90\uff1a\n{ledger.references}",
        )
        self.assertEqual(
            append_references_if_missing("answer snapshot-1:card:Fireball", ledger),
            "answer snapshot-1:card:Fireball",
        )


if __name__ == "__main__":
    unittest.main()
