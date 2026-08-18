"""Parser provenance metadata and multi-intent identity helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from clashroyale_agent.qa.parser_rules import (
    has_explicit_rank_signal,
    has_explicit_top_n_signal,
    has_implicit_list_signal,
    is_match_preparation_query,
    is_schedule_summary_query,
)
from clashroyale_agent.qa.parser_schema import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    LOCAL_PARSE_CONFIDENCE_LOW,
    LOCAL_PARSE_CONFIDENCE_MEDIUM,
)


QuestionPredicate = Callable[[str], bool]
CardRelationshipPredicate = Callable[[str, list[dict]], bool]


@dataclass(frozen=True)
class LocalParseMetadataDependencies:
    """Root-rule adapters required for local parse confidence inference."""

    is_meta_analysis_query: QuestionPredicate
    is_card_cooccurrence_query: CardRelationshipPredicate


def build_parse_metadata(
    *,
    parse_source: str,
    parse_confidence: str,
    parse_reason: str,
) -> dict:
    return {
        "parse_source": parse_source,
        "parse_confidence": parse_confidence,
        "parse_reason": parse_reason,
    }


def merge_parse_metadata(parsed: dict, metadata: dict) -> dict:
    result = dict(parsed)
    result.update(metadata)
    return result


def infer_local_parse_metadata(
    parsed: dict,
    question: str,
    dependencies: LocalParseMetadataDependencies,
) -> dict:
    q = question.lower()
    intent = parsed.get("intent")
    rank = parsed.get("rank")
    top_n = parsed.get("top_n")
    card_name = parsed.get("card_name")
    round_no = parsed.get("round")
    target_date = parsed.get("date")
    ask_players = parsed.get("ask_players", False)
    metric = parsed.get("metric")
    compare_metric = parsed.get("compare_metric")
    card_names = parsed.get("card_names") or []

    if intent == "reject":
        return build_parse_metadata(
            parse_source="local_reject",
            parse_confidence=LOCAL_PARSE_CONFIDENCE_LOW,
            parse_reason="local rules could not classify the query",
        )

    strong_signals = 0
    weak_signals = 0
    reasons = [f"intent={intent}"]

    if intent == "schedule_query":
        if round_no is not None:
            strong_signals += 1
            reasons.append("round matched")
        if target_date is not None:
            strong_signals += 1
            reasons.append("date matched")
        if ask_players:
            weak_signals += 1
            reasons.append("player intent matched")
        if any(
            keyword in q
            for keyword in ["下一轮", "赛程", "对战", "打谁", "上场", "round", "match", "轮"]
        ):
            strong_signals += 1
            reasons.append("schedule keyword matched")

    elif intent == "schedule_summary_query":
        if is_schedule_summary_query(question):
            strong_signals += 2
            reasons.append("strict schedule summary pattern matched")

    elif intent == "match_preparation_query":
        if is_match_preparation_query(question):
            strong_signals += 2
            reasons.append("strict match preparation pattern matched")

    elif intent == "meta_analysis_query":
        if dependencies.is_meta_analysis_query(question):
            strong_signals += 2
            reasons.append("strict meta analysis pattern matched")

    elif intent == "deck_query":
        if rank is not None and has_explicit_rank_signal(question):
            strong_signals += 1
            reasons.append("explicit rank matched")
        elif rank is not None:
            weak_signals += 1
            reasons.append("implicit rank inferred")
        if top_n is not None and has_explicit_top_n_signal(question):
            strong_signals += 1
            reasons.append("explicit top_n matched")
        elif top_n is not None and has_implicit_list_signal(question):
            weak_signals += 1
            reasons.append("implicit list size inferred")
        if "热门卡组" in question or "deck" in q or "卡组" in question:
            strong_signals += 1
            reasons.append("deck keyword matched")
        if metric is not None:
            weak_signals += 1
            reasons.append("metric inferred")

    elif intent == "card_query":
        if card_name is not None:
            strong_signals += 1
            reasons.append("card_name matched")
        if rank is not None and has_explicit_rank_signal(question):
            strong_signals += 1
            reasons.append("explicit rank matched")
        elif rank is not None:
            weak_signals += 1
            reasons.append("implicit rank inferred")
        if top_n is not None and has_explicit_top_n_signal(question):
            strong_signals += 1
            reasons.append("explicit top_n matched")
        elif top_n is not None and has_implicit_list_signal(question):
            weak_signals += 1
            reasons.append("implicit list size inferred")
        if (
            ("胜率" in question)
            or ("净胜率" in question)
            or ("使用率" in question)
            or ("cwr" in q)
        ):
            strong_signals += 1
            reasons.append("metric keyword matched")
        elif metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            weak_signals += 1
            reasons.append("metric inferred")
        if "卡牌" in question or "热门卡牌" in question or "card" in q:
            strong_signals += 1
            reasons.append("card keyword matched")

    elif intent == "card_compare_query":
        if len(card_names) >= 2:
            strong_signals += 1
            reasons.append("multiple card names matched")
        if any(keyword in q for keyword in ["哪个", "谁更", "更高", "更强", "比较", "vs", "对比"]):
            strong_signals += 1
            reasons.append("compare keyword matched")
        if compare_metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            strong_signals += 1
            reasons.append("compare metric matched")

    elif intent == "card_cooccurrence_query":
        if len(card_names) >= 2 or card_name is not None:
            strong_signals += 1
            reasons.append("card relationship entities matched")
        if dependencies.is_card_cooccurrence_query(
            question,
            []
            if not card_names and not card_name
            else [{"card_name": name} for name in ([card_name] if card_name else card_names)],
        ):
            strong_signals += 1
            reasons.append("cooccurrence keyword matched")

    elif intent == "card_rank_lookup_query":
        if card_name is not None:
            strong_signals += 1
            reasons.append("card_name matched")
        if metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            strong_signals += 1
            reasons.append("metric matched")
        if any(
            keyword in q
            for keyword in [
                "排第几",
                "排名多少",
                "排名第几",
                "榜排第几",
                "榜排名多少",
                "榜单排名多少",
                "ranking position",
                "rank position",
                "what rank",
                "what position",
            ]
        ):
            strong_signals += 1
            reasons.append("rank lookup keyword matched")

    if strong_signals >= 2:
        confidence = LOCAL_PARSE_CONFIDENCE_HIGH
    elif strong_signals >= 1 or weak_signals >= 2:
        confidence = LOCAL_PARSE_CONFIDENCE_MEDIUM
    else:
        confidence = LOCAL_PARSE_CONFIDENCE_LOW

    return build_parse_metadata(
        parse_source="local_rule",
        parse_confidence=confidence,
        parse_reason=", ".join(reasons),
    )


def subquery_semantic_key(parsed: dict) -> tuple:
    intent = parsed.get("intent")
    if intent == "meta_analysis_query":
        return (intent, parsed.get("analysis_type"))
    return (
        intent,
        parsed.get("card_name"),
        tuple(parsed.get("metrics") or []),
        tuple(parsed.get("card_names") or []),
        tuple(parsed.get("deck_cards") or []),
        parsed.get("rank"),
        parsed.get("top_n"),
        parsed.get("round"),
        parsed.get("date"),
        parsed.get("entity_mode"),
        parsed.get("special_state"),
    )


def make_multi_intent_result(subqueries: list[dict], question: str) -> dict:
    return {
        "intent": "multi_intent",
        "subqueries": subqueries,
        "parse_source": "local_rule",
        "parse_confidence": LOCAL_PARSE_CONFIDENCE_HIGH,
        "parse_reason": (
            f"split {len(subqueries)} independent intents from compound query: {question[:80]}"
        ),
    }


__all__ = [
    "build_parse_metadata",
    "infer_local_parse_metadata",
    "LocalParseMetadataDependencies",
    "make_multi_intent_result",
    "merge_parse_metadata",
    "subquery_semantic_key",
]
