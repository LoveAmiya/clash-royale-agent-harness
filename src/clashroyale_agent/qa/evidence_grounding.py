"""Evidence ledger and grounding helpers for QA/RAG answers."""

from dataclasses import dataclass
from typing import Any

from rag_quality import GroundedStreamBuffer, GroundingValidationError, validate_answer_grounding
from retrieval_postprocess import build_context_and_refs, strip_generated_reference_section


@dataclass(slots=True)
class EvidenceLedger:
    context: str
    references: str
    allowed_doc_ids: set[str]
    grounding_evidence: str


@dataclass(slots=True)
class GroundedAnswer:
    answer: str
    dropped_sentence_count: int


def build_evidence_ledger(
    compressed_results: list[dict[str, Any]],
    *,
    start_index: int = 1,
    structured_evidence: str | None = None,
) -> EvidenceLedger:
    context, references = build_context_and_refs(compressed_results, start_index=start_index)
    allowed_doc_ids = {str(item["doc"].get("doc_id")) for item in compressed_results}
    grounding_evidence = f"{structured_evidence}\n{context}" if structured_evidence else context
    return EvidenceLedger(
        context=context,
        references=references,
        allowed_doc_ids=allowed_doc_ids,
        grounding_evidence=grounding_evidence,
    )


def build_reference_suffix(ledger: EvidenceLedger) -> str:
    return f"\n\n\u53c2\u8003\u6765\u6e90\uff1a\n{ledger.references}"


def append_references_if_missing(answer: str, ledger: EvidenceLedger) -> str:
    if ledger.allowed_doc_ids and not any(doc_id in answer for doc_id in ledger.allowed_doc_ids):
        return f"{answer}{build_reference_suffix(ledger)}"
    return answer


def create_grounded_stream_buffer(
    ledger: EvidenceLedger,
    *,
    stop_markers: tuple[str, ...] = (),
    drop_unsupported: bool = False,
) -> GroundedStreamBuffer:
    return GroundedStreamBuffer(
        ledger.grounding_evidence,
        ledger.allowed_doc_ids,
        stop_markers=stop_markers,
        drop_unsupported=drop_unsupported,
    )


def filter_completed_answer(
    answer: str,
    ledger: EvidenceLedger,
    *,
    drop_unsupported: bool = True,
    empty_answer: str = "",
) -> GroundedAnswer:
    stripped = strip_generated_reference_section(answer)
    completed_buffer = create_grounded_stream_buffer(
        ledger,
        drop_unsupported=drop_unsupported,
    )
    validated_parts = completed_buffer.push(stripped)
    validated_parts += completed_buffer.finish()
    filtered = "".join(validated_parts).strip()
    if not filtered and empty_answer:
        filtered = empty_answer
    return GroundedAnswer(
        answer=filtered,
        dropped_sentence_count=completed_buffer.dropped_count,
    )


def validate_ledger_grounding(
    answer: str,
    ledger: EvidenceLedger,
    *,
    raise_on_failure: bool = False,
    require_citations: bool = True,
) -> dict[str, Any]:
    return validate_answer_grounding(
        answer,
        ledger.grounding_evidence,
        ledger.allowed_doc_ids,
        raise_on_failure=raise_on_failure,
        require_citations=require_citations,
    )


__all__ = [
    "EvidenceLedger",
    "GroundedAnswer",
    "GroundingValidationError",
    "append_references_if_missing",
    "build_evidence_ledger",
    "build_reference_suffix",
    "create_grounded_stream_buffer",
    "filter_completed_answer",
    "validate_ledger_grounding",
]
