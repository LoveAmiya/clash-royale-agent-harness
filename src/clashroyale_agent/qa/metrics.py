"""Metric parsing helpers for card and deck QA intents."""

from __future__ import annotations

from typing import Any

from clashroyale_agent.qa.intents import CARD_QUERY, VALID_METRIC_SET


def get_metric(question: str) -> str:
    """Return the primary metric implied by a free-text question."""
    q = question.lower()
    if "净胜率" in q or "cwr" in q or "clean win" in q:
        return "clean_win_rate"
    if "胜率" in q or "win rate" in q:
        return "win_rate"
    return "usage_rate"


def extract_metrics(question: str) -> list[str]:
    """Return all explicitly requested card metrics in a stable display order."""
    q = question.lower()
    metrics = []
    if "使用率" in q or "usage rate" in q:
        metrics.append("usage_rate")
    if "胜率" in q or "win rate" in q:
        metrics.append("win_rate")
    if "净胜率" in q or "cwr" in q or "clean win" in q:
        metrics.append("clean_win_rate")
    return metrics


def normalize_metrics(value: Any, question: str, intent: str) -> list[str] | None:
    """Normalize model-provided metric arrays to the card-query contract."""
    if intent != CARD_QUERY:
        return None

    raw_metrics = value if isinstance(value, list) else []
    metrics = [metric for metric in raw_metrics if metric in VALID_METRIC_SET]
    if not metrics:
        metrics = extract_metrics(question)
    if not metrics:
        metrics = [get_metric(question)]
    return list(dict.fromkeys(metrics))


__all__ = ["extract_metrics", "get_metric", "normalize_metrics"]
