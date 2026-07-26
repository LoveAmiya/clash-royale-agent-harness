"""Deterministic quality gates for snapshot RAG indexes and generated answers."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_NUMERIC_FACT = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|场|次|battles?|games?|appearances?)",
    re.IGNORECASE,
)
_DOC_ID = re.compile(r"\b(?:supercell|snapshot|official|other)[\w-]*:[^\s|`]+", re.IGNORECASE)


class RAGQualityGateError(RuntimeError):
    pass


class GroundingValidationError(RuntimeError):
    pass


def _normalize_fact(value: str, unit: str) -> tuple[str, str]:
    normalized_value = value.rstrip("0").rstrip(".") if "." in value else value
    normalized_unit = unit.lower()
    if normalized_unit.startswith("battle") or normalized_unit.startswith("game"):
        normalized_unit = "场"
    elif normalized_unit.startswith("appearance"):
        normalized_unit = "次"
    return normalized_value, normalized_unit


def extract_numeric_facts(text: str) -> set[tuple[str, str]]:
    return {_normalize_fact(value, unit) for value, unit in _NUMERIC_FACT.findall(text or "")}


def validate_answer_grounding(
    answer: str,
    evidence: str,
    allowed_doc_ids: set[str] | list[str] | tuple[str, ...],
    *,
    raise_on_failure: bool = False,
    require_citations: bool = True,
) -> dict[str, Any]:
    """Verify unit-bearing numbers and snapshot document references."""
    allowed = {str(doc_id) for doc_id in allowed_doc_ids if str(doc_id).strip()}
    answer_facts = extract_numeric_facts(answer)
    evidence_facts = extract_numeric_facts(evidence)
    unsupported_facts = sorted(answer_facts - evidence_facts)

    mentioned_tokens = {
        token.rstrip(".,;:，。；：)]}）】")
        for token in _DOC_ID.findall(answer or "")
    }
    unknown_citations = sorted(
        token
        for token in mentioned_tokens
        if not any(
            doc_id == token
            or (doc_id.startswith(token) and len(doc_id) > len(token) and doc_id[len(token)] == " ")
            for doc_id in allowed
        )
    )
    cited_doc_ids = sorted(doc_id for doc_id in allowed if doc_id in (answer or ""))
    missing_citations = bool(require_citations and allowed and not cited_doc_ids)
    passed = not unsupported_facts and not unknown_citations and not missing_citations
    report = {
        "passed": passed,
        "numeric_fact_count": len(answer_facts),
        "unsupported_numeric_facts": [f"{value}{unit}" for value, unit in unsupported_facts],
        "cited_doc_ids": cited_doc_ids,
        "unknown_citations": unknown_citations,
        "missing_citations": missing_citations,
    }
    if raise_on_failure and not passed:
        raise GroundingValidationError(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


class GroundedStreamBuffer:
    """Hold incomplete sentences so unsupported numeric claims are never emitted."""

    _BOUNDARY = re.compile(r"(?<=[\n。！？!?])")

    def __init__(self, evidence: str, allowed_doc_ids: set[str] | list[str] | tuple[str, ...]):
        self.evidence = evidence
        self.allowed_doc_ids = set(allowed_doc_ids)
        self.pending = ""

    def push(self, delta: str) -> list[str]:
        self.pending += delta
        parts = self._BOUNDARY.split(self.pending)
        if len(parts) == 1:
            return []
        self.pending = parts.pop()
        for part in parts:
            if part:
                validate_answer_grounding(
                    part,
                    self.evidence,
                    self.allowed_doc_ids,
                    raise_on_failure=True,
                    require_citations=False,
                )
        return [part for part in parts if part]

    def finish(self) -> list[str]:
        if not self.pending:
            return []
        pending = self.pending
        self.pending = ""
        validate_answer_grounding(
            pending,
            self.evidence,
            self.allowed_doc_ids,
            raise_on_failure=True,
            require_citations=False,
        )
        return [pending]


def _probe_query(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata", {})
    labels = [
        metadata.get("card_name"),
        metadata.get("deck_name"),
        metadata.get("opponent_deck_name"),
        metadata.get("archetype"),
        metadata.get("title"),
    ]
    label_text = " ".join(str(value) for value in labels if value)
    return f"{doc.get('doc_id', '')} {label_text} {str(doc.get('text', ''))[:180]}".strip()


def evaluate_rag_quality(
    snapshot_id: str,
    docs: list[dict[str, Any]],
    retriever: Any,
    *,
    min_documents: int,
    min_source_types: int,
    min_probe_recall: float,
) -> dict[str, Any]:
    failures: list[str] = []
    doc_ids = [str(doc.get("doc_id", "")) for doc in docs]
    doc_snapshot_ids = {
        str(doc.get("metadata", {}).get("snapshot_id", ""))
        for doc in docs
        if isinstance(doc, dict)
    }
    source_types = {str(doc.get("source_type", "")) for doc in docs if doc.get("source_type")}
    if len(docs) < min_documents:
        failures.append("insufficient_documents")
    if len(source_types) < min_source_types:
        failures.append("insufficient_source_types")
    if doc_snapshot_ids != {snapshot_id}:
        failures.append("snapshot_mismatch")
    if not all(doc_ids) or len(set(doc_ids)) != len(doc_ids):
        failures.append("invalid_or_duplicate_doc_ids")

    probe_docs: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for doc in docs:
        source_type = str(doc.get("source_type", ""))
        if source_type and source_type not in seen_sources:
            probe_docs.append(doc)
            seen_sources.add(source_type)
    hits = 0
    for doc in probe_docs:
        results = retriever.hybrid_search(
            _probe_query(doc),
            top_k_bm25=10,
            top_k_dense=10,
            final_top_k=5,
            alpha=0.5,
            source_type=doc.get("source_type"),
        )
        retrieved_ids = {str(item.get("doc", {}).get("doc_id", "")) for item in results}
        hits += int(doc.get("doc_id") in retrieved_ids)
    probe_recall = hits / len(probe_docs) if probe_docs else 0.0
    if probe_recall < min_probe_recall:
        failures.append("probe_recall_below_threshold")

    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failures,
        "failures": failures,
        "document_count": len(docs),
        "source_types": sorted(source_types),
        "probe_count": len(probe_docs),
        "probe_hits_at_5": hits,
        "probe_recall_at_5": round(probe_recall, 6),
        "thresholds": {
            "min_documents": min_documents,
            "min_source_types": min_source_types,
            "min_probe_recall": min_probe_recall,
        },
        "retrieval_mode": "hybrid" if getattr(retriever, "dense_available", False) else "bm25_only",
    }


def persist_quality_report(report: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_snapshot_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(report.get("snapshot_id") or "unknown"))
    destination = directory / f"{safe_snapshot_id}.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=directory)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(json.dumps(report, ensure_ascii=False, indent=2))
        Path(temporary_name).replace(destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return destination
