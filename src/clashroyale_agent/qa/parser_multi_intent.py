"""Deterministic multi-intent parser assembly and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from clashroyale_agent.qa.parser_schema import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    MAX_SUBQUERIES,
)


ParseFunction = Callable[[str, list[dict]], dict]
CardListResolver = Callable[[str, list[dict]], list[str]]
QuestionPredicate = Callable[[str], bool]
CardPredicate = Callable[[str, list[dict]], bool]


@dataclass(frozen=True)
class MultiIntentDependencies:
    """Adapters needed by packaged multi-intent parsing."""

    fallback_parse_query: ParseFunction
    resolve_card_names: CardListResolver
    extract_metrics: Callable[[str], list[str]]
    is_card_compare_query: CardPredicate
    is_card_rank_lookup_query: CardPredicate
    is_card_ranking_query: QuestionPredicate
    subquery_semantic_key: Callable[[dict], tuple]
    has_explicit_rank_signal: QuestionPredicate
    has_explicit_top_n_signal: QuestionPredicate
    make_multi_intent_result: Callable[[list[dict], str], dict]
    normalize_parsed_query: ParseFunction


def fallback_parse_multi_intent(
    question: str,
    cards_meta_data: list[dict],
    dependencies: MultiIntentDependencies,
) -> dict:
    """Conservatively discover independent local and RAG questions in one utterance."""
    candidates: list[dict] = []
    seen: set[tuple] = set()

    def add_candidate(candidate: dict) -> None:
        if candidate.get("intent") == "reject":
            return
        if candidate.get("intent") == "card_query" and candidate.get("card_name"):
            for existing in candidates:
                if (
                    existing.get("intent") != "card_query"
                    or existing.get("card_name") != candidate.get("card_name")
                ):
                    continue
                merged_metrics = list(existing.get("metrics") or [])
                for metric in candidate.get("metrics") or [candidate.get("metric")]:
                    if metric and metric not in merged_metrics:
                        merged_metrics.append(metric)
                existing["metrics"] = merged_metrics
                existing["metric"] = merged_metrics[0] if merged_metrics else existing.get("metric")
                return
        key = dependencies.subquery_semantic_key(candidate)
        if key in seen or len(candidates) >= MAX_SUBQUERIES:
            return
        seen.add(key)
        candidates.append(candidate)

    segments = [
        part.strip()
        for part in re.split(r"[\uff0c,\uff1b;\u3002\uff01\uff1f!?]|(?:\u8fd8\u6709|\u4ee5\u53ca|\u5e76\u4e14|\u540c\u65f6)", question)
        if part.strip()
    ]
    last_card_name: str | None = None
    for segment in segments:
        candidate = dependencies.fallback_parse_query(segment, cards_meta_data)
        segment_card_names = dependencies.resolve_card_names(segment, cards_meta_data)
        segment_metrics = dependencies.extract_metrics(segment)

        if (
            candidate.get("intent") == "card_query"
            and len(segment_card_names) > 1
            and segment_metrics
            and not dependencies.is_card_compare_query(segment, cards_meta_data)
            and not dependencies.is_card_rank_lookup_query(segment, cards_meta_data)
        ):
            for card_name in segment_card_names:
                card_query = dict(candidate)
                card_query.update(
                    {
                        "card_name": card_name,
                        "card_names": None,
                        "metric": segment_metrics[0],
                        "metrics": segment_metrics,
                    }
                )
                add_candidate(card_query)
            last_card_name = segment_card_names[-1]
            continue

        if segment_card_names:
            last_card_name = segment_card_names[-1]
        elif (
            segment_metrics
            and last_card_name
            and candidate.get("card_name") is None
            and candidate.get("top_n") is None
            and not dependencies.is_card_ranking_query(segment)
        ):
            candidate = dependencies.fallback_parse_query(
                f"{last_card_name} {segment}", cards_meta_data
            )
            candidate["intent"] = "card_query"
            candidate["card_name"] = last_card_name
            candidate["metric"] = segment_metrics[0]
            candidate["metrics"] = segment_metrics
        add_candidate(candidate)
    full_query = dependencies.fallback_parse_query(question, cards_meta_data)
    if not any(candidate.get("intent") == full_query.get("intent") for candidate in candidates):
        add_candidate(full_query)

    if any(candidate.get("intent") == "meta_analysis_query" for candidate in candidates) and not (
        dependencies.has_explicit_rank_signal(question)
        or dependencies.has_explicit_top_n_signal(question)
    ):
        candidates = [
            candidate
            for candidate in candidates
            if not (
                candidate.get("intent") == "deck_query"
                and candidate.get("card_name") is None
            )
        ]

    if len(candidates) <= 1:
        return candidates[0] if candidates else dependencies.fallback_parse_query(question, cards_meta_data)

    subqueries = []
    for index, candidate in enumerate(candidates, start=1):
        subquery = dict(candidate)
        subquery["id"] = f"q{index}"
        subqueries.append(subquery)
    return dependencies.make_multi_intent_result(subqueries, question)


def normalize_multi_intent_query(
    parsed: dict,
    question: str,
    cards_meta_data: list[dict],
    dependencies: MultiIntentDependencies,
) -> dict:
    """Validate an LLM multi-intent payload while retaining the single-intent contract."""
    if parsed.get("intent") != "multi_intent":
        return dependencies.normalize_parsed_query(parsed, question, cards_meta_data)

    normalized_subqueries: list[dict] = []
    seen: set[tuple] = set()
    raw_subqueries = parsed.get("subqueries") if isinstance(parsed.get("subqueries"), list) else []
    for raw_subquery in raw_subqueries[:MAX_SUBQUERIES]:
        if not isinstance(raw_subquery, dict):
            continue
        normalized = dependencies.normalize_parsed_query(raw_subquery, question, cards_meta_data)
        if normalized.get("intent") == "reject":
            continue
        key = dependencies.subquery_semantic_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        normalized["id"] = str(raw_subquery.get("id") or f"q{len(normalized_subqueries) + 1}")
        normalized_subqueries.append(normalized)

    if len(normalized_subqueries) <= 1:
        return (
            normalized_subqueries[0]
            if normalized_subqueries
            else fallback_parse_multi_intent(question, cards_meta_data, dependencies)
        )

    result = dependencies.make_multi_intent_result(normalized_subqueries, question)
    result["parse_source"] = parsed.get("parse_source") or "llm_parser"
    result["parse_confidence"] = parsed.get("parse_confidence") or LOCAL_PARSE_CONFIDENCE_HIGH
    result["parse_reason"] = parsed.get("parse_reason") or "validated llm multi-intent output"
    return result


__all__ = [
    "MultiIntentDependencies",
    "fallback_parse_multi_intent",
    "normalize_multi_intent_query",
]
