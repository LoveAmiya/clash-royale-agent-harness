"""Intent and metric schema constants for QA parsing."""

from __future__ import annotations

SCHEDULE_QUERY = "schedule_query"
SCHEDULE_SUMMARY_QUERY = "schedule_summary_query"
DECK_QUERY = "deck_query"
CARD_QUERY = "card_query"
CARD_COMPARE_QUERY = "card_compare_query"
CARD_COOCCURRENCE_QUERY = "card_cooccurrence_query"
CARD_RANK_LOOKUP_QUERY = "card_rank_lookup_query"
META_ANALYSIS_QUERY = "meta_analysis_query"
MATCH_PREPARATION_QUERY = "match_preparation_query"
REJECT = "reject"
MULTI_INTENT = "multi_intent"

SUPPORTED_SINGLE_INTENTS = (
    SCHEDULE_QUERY,
    SCHEDULE_SUMMARY_QUERY,
    DECK_QUERY,
    CARD_QUERY,
    CARD_COMPARE_QUERY,
    CARD_COOCCURRENCE_QUERY,
    CARD_RANK_LOOKUP_QUERY,
    META_ANALYSIS_QUERY,
    MATCH_PREPARATION_QUERY,
    REJECT,
)
SUPPORTED_SINGLE_INTENT_SET = frozenset(SUPPORTED_SINGLE_INTENTS)
SUPPORTED_INTENTS = (*SUPPORTED_SINGLE_INTENTS, MULTI_INTENT)
SUPPORTED_INTENT_SET = frozenset(SUPPORTED_INTENTS)

VALID_METRICS = ("usage_rate", "win_rate", "clean_win_rate")
VALID_METRIC_SET = frozenset(VALID_METRICS)


def is_supported_single_intent(intent: object) -> bool:
    """Return whether an intent is valid inside the single-query parser contract."""
    return intent in SUPPORTED_SINGLE_INTENT_SET


def is_valid_metric(metric: object) -> bool:
    """Return whether a metric field is valid or intentionally absent."""
    return metric is None or metric in VALID_METRIC_SET


__all__ = [
    "CARD_COMPARE_QUERY",
    "CARD_COOCCURRENCE_QUERY",
    "CARD_QUERY",
    "CARD_RANK_LOOKUP_QUERY",
    "DECK_QUERY",
    "MATCH_PREPARATION_QUERY",
    "META_ANALYSIS_QUERY",
    "MULTI_INTENT",
    "REJECT",
    "SCHEDULE_QUERY",
    "SCHEDULE_SUMMARY_QUERY",
    "SUPPORTED_INTENTS",
    "SUPPORTED_INTENT_SET",
    "SUPPORTED_SINGLE_INTENTS",
    "SUPPORTED_SINGLE_INTENT_SET",
    "VALID_METRICS",
    "VALID_METRIC_SET",
    "is_supported_single_intent",
    "is_valid_metric",
]
