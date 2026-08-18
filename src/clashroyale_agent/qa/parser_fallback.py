"""Deterministic local fallback parser assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


QuestionPredicate = Callable[[str], bool]
CardPredicate = Callable[[str, list[dict]], bool]
CardResolver = Callable[[str, list[dict]], str | None]
CardListResolver = Callable[[str, list[dict]], list[str]]


@dataclass(frozen=True)
class FallbackParseDependencies:
    """Adapters needed by the packaged fallback parser."""

    is_schedule_summary_query: QuestionPredicate
    is_match_preparation_query: QuestionPredicate
    is_meta_analysis_query: QuestionPredicate
    is_card_cooccurrence_query: CardPredicate
    is_card_compare_query: CardPredicate
    is_card_rank_lookup_query: CardPredicate
    is_schedule_query: QuestionPredicate
    is_deck_query: QuestionPredicate
    is_card_query: CardPredicate
    resolve_card_name: CardResolver
    resolve_card_names: CardListResolver
    get_metric: Callable[[str], str | None]
    extract_rank_target: Callable[..., int | None]
    extract_top_n: Callable[..., int | None]
    extract_round_number: Callable[[str], int | None]
    extract_date: Callable[[str], str | None]
    is_card_ranking_query: QuestionPredicate
    has_explicit_top_n_signal: QuestionPredicate
    normalize_metrics: Callable[[Any, str, str | None], list[str]]
    is_asking_players: QuestionPredicate
    is_meta_delta_query: QuestionPredicate
    detect_entity_reference: Callable[[str, list[dict]], dict]
    merge_parse_metadata: Callable[[dict, dict], dict]
    infer_local_parse_metadata: Callable[[dict, str], dict]


def fallback_parse_query(
    question: str,
    cards_meta_data: list[dict],
    dependencies: FallbackParseDependencies,
) -> dict:
    """Return a conservative deterministic parse when model parsing is unavailable."""
    intent = "reject"
    if dependencies.is_schedule_summary_query(question):
        intent = "schedule_summary_query"
    elif dependencies.is_match_preparation_query(question):
        intent = "match_preparation_query"
    elif dependencies.is_meta_analysis_query(question):
        intent = "meta_analysis_query"
    elif dependencies.is_card_cooccurrence_query(question, cards_meta_data):
        intent = "card_cooccurrence_query"
    elif dependencies.is_card_compare_query(question, cards_meta_data):
        intent = "card_compare_query"
    elif dependencies.is_card_rank_lookup_query(question, cards_meta_data):
        intent = "card_rank_lookup_query"
    elif dependencies.is_schedule_query(question):
        intent = "schedule_query"
    elif dependencies.is_deck_query(question):
        intent = "deck_query"
    elif dependencies.is_card_query(question, cards_meta_data):
        intent = "card_query"

    card_name = dependencies.resolve_card_name(question, cards_meta_data)
    card_names = dependencies.resolve_card_names(question, cards_meta_data)
    metric = (
        dependencies.get_metric(question)
        if intent in {"deck_query", "card_query", "card_rank_lookup_query"}
        else None
    )
    compare_metric = dependencies.get_metric(question) if intent == "card_compare_query" else None
    rank_target = dependencies.extract_rank_target(question, max_n=30)
    top_n = dependencies.extract_top_n(question, default=None, max_n=30)
    round_no = dependencies.extract_round_number(question)
    target_date = dependencies.extract_date(question)

    if card_name and not dependencies.is_card_ranking_query(question):
        rank_target = None
        top_n = None

    if rank_target is not None:
        top_n = None

    if intent == "schedule_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None

    if intent == "schedule_summary_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "match_preparation_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "meta_analysis_query":
        metric = None
        compare_metric = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    deck_cards = card_names if intent == "deck_query" and len(card_names) == 8 else None

    if intent == "deck_query":
        card_names = None
        if deck_cards:
            card_name = None
            rank_target = None
            top_n = None

    if intent == "card_query":
        card_names = None

    if intent == "card_rank_lookup_query":
        compare_metric = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "card_compare_query":
        metric = None
        card_name = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None
        if not card_names:
            card_names = None

    if intent == "card_cooccurrence_query":
        metric = None
        compare_metric = None
        rank_target = None
        round_no = None
        target_date = None
        if len(card_names) >= 2:
            card_name = None
            top_n = None
        else:
            card_name = card_names[0] if card_names else card_name
            card_names = None
            top_n = (
                dependencies.extract_top_n(question, default=10, max_n=30)
                if dependencies.has_explicit_top_n_signal(question)
                else 10
            )

    if intent == "deck_query" and rank_target is None and top_n is None:
        if any(
            keyword in question
            for keyword in [
                "\u70ed\u95e8\u5361\u7ec4",
                "\u9ad8\u4f7f\u7528\u7387\u5361\u7ec4",
                "\u6700\u70ed\u95e8\u5361\u7ec4",
                "\u5361\u7ec4",
            ]
        ):
            if any(
                keyword in question
                for keyword in [
                    "\u6709\u54ea\u4e9b",
                    "\u54ea\u4e9b",
                    "\u5206\u522b\u662f\u8c01",
                    "\u90fd\u6709\u4ec0\u4e48",
                ]
            ):
                top_n = 5

    if intent == "card_query" and card_name is None and rank_target is None and top_n is None:
        if any(
            keyword in question
            for keyword in [
                "\u70ed\u95e8\u5361\u724c",
                "\u9ad8\u4f7f\u7528\u7387\u5361\u724c",
                "\u4f7f\u7528\u7387\u6700\u9ad8",
                "\u80dc\u7387\u6700\u9ad8",
            ]
        ):
            if any(
                keyword in question
                for keyword in [
                    "\u6709\u54ea\u4e9b",
                    "\u54ea\u4e9b",
                    "\u5206\u522b\u662f\u8c01",
                    "\u90fd\u6709\u4ec0\u4e48",
                ]
            ):
                top_n = 5

    metrics = dependencies.normalize_metrics(None, question, intent)
    if intent == "card_query" and metrics:
        metric = metrics[0]

    parsed = {
        "intent": intent,
        "metric": metric,
        "metrics": metrics,
        "compare_metric": compare_metric,
        "rank": rank_target,
        "top_n": top_n,
        "card_name": card_name,
        "card_names": card_names,
        "deck_cards": deck_cards,
        "round": round_no,
        "date": target_date,
        "ask_players": dependencies.is_asking_players(question),
        "analysis_type": (
            "meta_delta"
            if intent == "meta_analysis_query" and dependencies.is_meta_delta_query(question)
            else None
        ),
        **dependencies.detect_entity_reference(question, cards_meta_data),
    }
    return dependencies.merge_parse_metadata(
        parsed, dependencies.infer_local_parse_metadata(parsed, question)
    )


__all__ = ["FallbackParseDependencies", "fallback_parse_query"]
