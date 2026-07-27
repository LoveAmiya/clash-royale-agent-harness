"""Deterministic quality gates for snapshot RAG indexes and generated answers."""

from __future__ import annotations

import json
import hashlib
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_NUMERIC_FACT = re.compile(
    r"(?<![\w.,])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(%|场|次|battles?|games?|appearances?)",
    re.IGNORECASE,
)
_DOC_ID = re.compile(r"\b(?:supercell|snapshot|official|other)[\w-]*:[^\s|`]+", re.IGNORECASE)
_INVALID_PERCENT = re.compile(r"\b(?:none|null|nan)\s*%", re.IGNORECASE)
_CARD_ENTITY_PATTERNS = (
    re.compile(r"(?:卡牌|card)\s*[:：]\s*([A-Za-z][A-Za-z .'-]{1,50})", re.IGNORECASE),
    re.compile(
        r"\|\s*([A-Z][A-Za-z .'-]{1,50})\s*\|(?=[^\n]*(?:使用率|胜率|usage\s*rate|win\s*rate))",
        re.IGNORECASE,
    ),
)
_METRIC_ALIASES = {
    "clean_win_rate": ("净胜率", "clean win rate", "clean_win_rate"),
    "usage_rate": ("使用率", "usage rate", "usage_rate"),
    "win_rate": ("胜率", "win rate", "win_rate"),
    "sample_size": ("样本", "出场", "sample", "appearances", "battles", "games"),
}


class RAGQualityGateError(RuntimeError):
    pass


class GroundingValidationError(RuntimeError):
    pass


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _invalid_evidence_doc_ids(docs: list[dict[str, Any]]) -> list[str]:
    invalid: list[str] = []
    for doc in docs:
        if not isinstance(doc, dict):
            invalid.append("")
            continue
        doc_id = str(doc.get("doc_id", ""))
        source_type = doc.get("source_type")
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        text = str(doc.get("text", ""))
        malformed = not text.strip() or bool(_INVALID_PERCENT.search(text))
        if source_type == "card" and any(
            key in metadata for key in ("usage_rate", "win_rate", "appearance_count")
        ):
            malformed = malformed or not all(
                _is_number(metadata.get(key))
                for key in ("usage_rate", "win_rate", "appearance_count")
            )
        elif source_type == "deck" and any(
            key in metadata for key in ("cards", "battles", "sample_win_rate")
        ):
            malformed = malformed or not (
                isinstance(metadata.get("cards"), list)
                and bool(metadata.get("cards"))
                and _is_number(metadata.get("battles"))
                and _is_number(metadata.get("sample_win_rate"))
            )
        if malformed:
            invalid.append(doc_id)
    return sorted(invalid)


def _normalize_fact(value: str, unit: str) -> tuple[str, str]:
    value = value.replace(",", "")
    normalized_value = value.rstrip("0").rstrip(".") if "." in value else value
    normalized_unit = unit.lower()
    if normalized_unit.startswith("battle") or normalized_unit.startswith("game"):
        normalized_unit = "场"
    elif normalized_unit.startswith("appearance"):
        normalized_unit = "次"
    return normalized_value, normalized_unit


def extract_numeric_facts(text: str) -> set[tuple[str, str]]:
    return {_normalize_fact(value, unit) for value, unit in _NUMERIC_FACT.findall(text or "")}


def _known_entities(evidence: str) -> set[str]:
    entities: set[str] = set()
    for pattern in _CARD_ENTITY_PATTERNS:
        for match in pattern.finditer(evidence or ""):
            entity = match.group(1).strip(" .;；")
            if entity:
                entities.add(entity)
    return entities


def _metric_near_fact(segment: str, start: int, end: int, unit: str) -> str | None:
    if unit.lower().startswith(("battle", "game", "appearance")) or unit in {"场", "次"}:
        return "sample_size"
    window = segment[max(0, start - 48) : start].lower()
    matches: list[tuple[int, str]] = []
    for metric, aliases in _METRIC_ALIASES.items():
        for alias in aliases:
            position = window.rfind(alias.lower())
            if position >= 0:
                matches.append((position, metric))
    if matches:
        return max(matches)[1]
    return None


def _numeric_claims(text: str, known_entities: set[str]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for segment in re.split(r"[\n。！？!?]+", text or ""):
        lowered = segment.lower()
        entities = {entity for entity in known_entities if entity.lower() in lowered}
        for match in _NUMERIC_FACT.finditer(segment):
            value, unit = _normalize_fact(match.group(1), match.group(2))
            claims.append(
                {
                    "value": value,
                    "unit": unit,
                    "metric": _metric_near_fact(segment, match.start(), match.end(), match.group(2)),
                    "entities": entities,
                }
            )
    return claims


def _unsupported_numeric_claims(answer: str, evidence: str) -> list[str]:
    entities = _known_entities(evidence)
    evidence_claims = _numeric_claims(evidence, entities)
    unsupported: set[str] = set()
    for claim in _numeric_claims(answer, entities):
        candidates = [
            item
            for item in evidence_claims
            if (item["value"], item["unit"]) == (claim["value"], claim["unit"])
        ]
        if claim["metric"] is not None:
            candidates = [item for item in candidates if item["metric"] == claim["metric"]]
        if claim["entities"]:
            candidates = [item for item in candidates if claim["entities"] & item["entities"]]
        if candidates:
            continue
        entity = ",".join(sorted(claim["entities"])) or "*"
        metric = claim["metric"] or "*"
        unsupported.add(f"{entity}|{metric}|{claim['value']}{claim['unit']}")
    return sorted(unsupported)


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
    unsupported_claims = _unsupported_numeric_claims(answer, evidence)

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
    passed = not unsupported_facts and not unsupported_claims and not unknown_citations and not missing_citations
    report = {
        "passed": passed,
        "numeric_fact_count": len(answer_facts),
        "unsupported_numeric_facts": [f"{value}{unit}" for value, unit in unsupported_facts],
        "unsupported_numeric_claims": unsupported_claims,
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

    def __init__(
        self,
        evidence: str,
        allowed_doc_ids: set[str] | list[str] | tuple[str, ...],
        *,
        stop_markers: tuple[str, ...] = (),
    ):
        self.evidence = evidence
        self.allowed_doc_ids = set(allowed_doc_ids)
        self.stop_markers = stop_markers
        self.pending = ""
        self.stopped = False

    def push(self, delta: str) -> list[str]:
        if self.stopped:
            return []
        self.pending += delta
        marker_positions = [self.pending.find(marker) for marker in self.stop_markers]
        marker_positions = [position for position in marker_positions if position >= 0]
        if marker_positions:
            position = min(marker_positions)
            pending = self.pending[:position]
            self.pending = ""
            self.stopped = True
            if not pending:
                return []
            validate_answer_grounding(
                pending,
                self.evidence,
                self.allowed_doc_ids,
                raise_on_failure=True,
                require_citations=False,
            )
            return [pending]
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
        if self.stopped or not self.pending:
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
    source_type = str(doc.get("source_type") or "")
    card_name = str(metadata.get("card_name") or "").strip()
    deck_name = str(metadata.get("deck_name") or "").strip()
    opponent_deck = str(metadata.get("opponent_deck_name") or "").strip()
    archetype = str(metadata.get("archetype") or "").strip()
    cards = metadata.get("cards") if isinstance(metadata.get("cards"), list) else []
    if source_type == "snapshot":
        return "current Clash Royale environment official sample overview"
    if source_type == "card":
        return f"{card_name} usage rate win rate sample appearances"
    if source_type == "deck":
        return f"{deck_name} deck cards sample games win rate"
    if source_type == "matchup":
        return f"{deck_name} versus {opponent_deck} matchup win rate"
    if source_type == "card_profile":
        return f"{card_name} common teammates opponents card profile"
    if source_type == "deck_profile":
        return f"{deck_name} common opposing decks performance"
    if source_type == "archetype":
        return f"{archetype} archetype usage win rate matchups"
    if source_type == "card_pair":
        return f"{' and '.join(str(card) for card in cards)} synergy win rate"
    if source_type == "counter":
        return f"{card_name} against {metadata.get('opponent_card_name', '')} counter matchup"
    labels = [card_name, deck_name, opponent_deck, archetype, *[str(card) for card in cards]]
    return " ".join(value for value in labels if value) or source_type


def evaluate_rag_quality(
    snapshot_id: str,
    docs: list[dict[str, Any]],
    retriever: Any,
    *,
    min_documents: int,
    min_source_types: int,
    min_probe_recall: float,
    probes_per_source: int = 3,
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
    invalid_evidence_doc_ids = _invalid_evidence_doc_ids(docs)
    if invalid_evidence_doc_ids:
        failures.append("invalid_evidence_fields")

    probe_docs: list[dict[str, Any]] = []
    documents_by_source: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        source_type = str(doc.get("source_type", ""))
        if source_type:
            documents_by_source.setdefault(source_type, []).append(doc)
    for source_type in sorted(documents_by_source):
        candidates = sorted(documents_by_source[source_type], key=lambda item: str(item.get("doc_id", "")))
        limit = min(max(1, int(probes_per_source)), len(candidates))
        if limit == len(candidates):
            selected = candidates
        elif limit == 1:
            selected = [candidates[0]]
        else:
            indices = [round(index * (len(candidates) - 1) / (limit - 1)) for index in range(limit)]
            selected = [candidates[index] for index in indices]
        probe_docs.extend(selected)
    hits = 0
    probes_by_source: dict[str, int] = {}
    hits_by_source: dict[str, int] = {}
    for doc in probe_docs:
        source_type = str(doc.get("source_type", ""))
        probes_by_source[source_type] = probes_by_source.get(source_type, 0) + 1
        results = retriever.hybrid_search(
            _probe_query(doc),
            top_k_bm25=10,
            top_k_dense=10,
            final_top_k=5,
            alpha=0.5,
            source_type=doc.get("source_type"),
        )
        retrieved_ids = {str(item.get("doc", {}).get("doc_id", "")) for item in results}
        hit = int(doc.get("doc_id") in retrieved_ids)
        hits += hit
        hits_by_source[source_type] = hits_by_source.get(source_type, 0) + hit
    probe_recall = hits / len(probe_docs) if probe_docs else 0.0
    probe_recall_by_source = {
        source_type: hits_by_source.get(source_type, 0) / count
        for source_type, count in sorted(probes_by_source.items())
    }
    if probe_recall < min_probe_recall:
        failures.append("probe_recall_below_threshold")
    failures.extend(
        f"source_probe_recall_below_threshold:{source_type}"
        for source_type, recall in probe_recall_by_source.items()
        if recall < min_probe_recall
    )

    return {
        "schema_version": 2,
        "snapshot_id": snapshot_id,
        "docs_fingerprint": hashlib.sha256(
            json.dumps(docs, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failures,
        "failures": failures,
        "document_count": len(docs),
        "source_types": sorted(source_types),
        "invalid_evidence_doc_ids": invalid_evidence_doc_ids,
        "probe_count": len(probe_docs),
        "probe_hits_at_5": hits,
        "probe_recall_at_5": round(probe_recall, 6),
        "probe_recall_by_source": probe_recall_by_source,
        "thresholds": {
            "min_documents": min_documents,
            "min_source_types": min_source_types,
            "min_probe_recall": min_probe_recall,
            "probes_per_source": max(1, int(probes_per_source)),
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
