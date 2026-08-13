"""Shared policy and diagnostics for bounded rolling RAG evidence documents."""

from __future__ import annotations

from collections import Counter
from typing import Iterable


# These limits bound index growth while structured SQLite keeps every fact.
# Exhaustive entity sources (cards, variants, towers, archetypes) are omitted.
RAG_SOURCE_LIMITS: dict[str, int] = {
    "deck": 150,
    "deck_profile": 150,
    "meta_delta": 301,
    "full_loadout": 150,
    "full_loadout_matchup": 500,
    "matchup": 500,
    "card_pair": 365,
    "counter": 300,
    "card_profile": 180,
}

RAG_DOCUMENT_COUNT_SEMANTICS = "scope_sum_including_duplicates"
RAG_SCOPE_COUNT_SEMANTICS = "bounded_evidence_documents"


def summarize_scope_documents(
    documents: Iterable[dict],
    scopes: Iterable[str],
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    scope_list = list(scopes)
    counts = {scope: 0 for scope in scope_list}
    source_counters = {scope: Counter() for scope in scope_list}
    for document in documents:
        if not isinstance(document, dict):
            continue
        metadata = document.get("metadata")
        if not isinstance(metadata, dict):
            continue
        scope = str(metadata.get("dataset_scope") or "")
        if scope not in counts:
            continue
        counts[scope] += 1
        source_type = str(document.get("source_type") or "").strip()
        if source_type:
            source_counters[scope][source_type] += 1
    return counts, {
        scope: dict(sorted(source_counters[scope].items()))
        for scope in scope_list
    }


def saturated_source_types(source_counts: dict[str, int] | None) -> list[str]:
    counts = source_counts or {}
    return sorted(
        source_type
        for source_type, limit in RAG_SOURCE_LIMITS.items()
        if int(counts.get(source_type) or 0) >= limit
    )
