"""Validation boundary for normalized single-intent parser results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from clashroyale_agent.qa.intents import is_supported_single_intent, is_valid_metric
from clashroyale_agent.qa.metrics import get_metric, normalize_metrics
from clashroyale_agent.qa.parser_primitives import (
    coerce_round_value,
    extract_date,
    extract_round_number,
)
from clashroyale_agent.qa.parser_schema import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    LOCAL_PARSE_CONFIDENCE_LOW,
    LOCAL_PARSE_CONFIDENCE_MEDIUM,
)
from clashroyale_agent.qa.ranking import coerce_rank_value, coerce_top_n_value


FallbackParser = Callable[[str, list[dict]], dict]
CardResolver = Callable[[str, list[dict]], str | None]
CardListResolver = Callable[[str, list[dict]], list[str]]
QuestionPredicate = Callable[[str], bool]
EntityDetector = Callable[[str, list[dict]], dict]


@dataclass(frozen=True)
class ParserNormalizationDependencies:
    """Local-rule adapters used while validating model parser output."""

    fallback_parse_query: FallbackParser
    resolve_card_name: CardResolver
    resolve_card_names: CardListResolver
    is_asking_players: QuestionPredicate
    is_meta_delta_query: QuestionPredicate
    is_card_ranking_query: QuestionPredicate
    has_explicit_top_n_signal: QuestionPredicate
    detect_entity_reference: EntityDetector


def normalize_parsed_query(
    parsed: dict,
    question: str,
    cards_meta_data: list[dict],
    dependencies: ParserNormalizationDependencies,
) -> dict:
    """Validate and repair model-provided routing fields."""
    result = {
        "intent": parsed.get("intent"),
        "metric": parsed.get("metric"),
        "metrics": parsed.get("metrics"),
        "compare_metric": parsed.get("compare_metric"),
        "rank": parsed.get("rank"),
        "top_n": parsed.get("top_n"),
        "card_name": parsed.get("card_name"),
        "card_names": parsed.get("card_names"),
        "deck_cards": parsed.get("deck_cards"),
        "round": parsed.get("round"),
        "date": parsed.get("date"),
        "ask_players": parsed.get("ask_players", False),
        "parse_source": parsed.get("parse_source"),
        "parse_confidence": parsed.get("parse_confidence"),
        "parse_reason": parsed.get("parse_reason"),
        "entity_mode": parsed.get("entity_mode"),
        "entity_type": parsed.get("entity_type"),
        "entity_name": parsed.get("entity_name"),
        "special_state": parsed.get("special_state"),
        "analysis_type": parsed.get("analysis_type"),
    }

    if not is_supported_single_intent(result["intent"]):
        return dependencies.fallback_parse_query(question, cards_meta_data)

    if isinstance(result["card_name"], str):
        result["card_name"] = (
            dependencies.resolve_card_name(result["card_name"], cards_meta_data)
            or dependencies.resolve_card_name(question, cards_meta_data)
        )
    if isinstance(result["card_names"], list):
        canonical_names: list[str] = []
        for raw_name in result["card_names"]:
            canonical_name = dependencies.resolve_card_name(
                str(raw_name), cards_meta_data
            )
            if canonical_name and canonical_name not in canonical_names:
                canonical_names.append(canonical_name)
        for canonical_name in dependencies.resolve_card_names(
            question, cards_meta_data
        ):
            if canonical_name not in canonical_names:
                canonical_names.append(canonical_name)
        result["card_names"] = canonical_names
    if isinstance(result["deck_cards"], list):
        canonical_deck_cards: list[str] = []
        for raw_name in result["deck_cards"]:
            canonical_name = dependencies.resolve_card_name(
                str(raw_name), cards_meta_data
            )
            if canonical_name and canonical_name not in canonical_deck_cards:
                canonical_deck_cards.append(canonical_name)
        result["deck_cards"] = (
            canonical_deck_cards if len(canonical_deck_cards) == 8 else None
        )

    if not is_valid_metric(result["metric"]):
        result["metric"] = get_metric(question)
    if not is_valid_metric(result["compare_metric"]):
        result["compare_metric"] = get_metric(question)

    coerced_rank = coerce_rank_value(result["rank"], max_n=30)
    if coerced_rank is not None:
        result["rank"] = coerced_rank

    coerced_top_n = coerce_top_n_value(result["top_n"], max_n=30)
    if coerced_top_n is not None:
        result["top_n"] = coerced_top_n

    coerced_round = coerce_round_value(result["round"])
    if coerced_round is not None:
        result["round"] = coerced_round

    if not isinstance(result["ask_players"], bool):
        result["ask_players"] = dependencies.is_asking_players(question)

    if result["intent"] == "schedule_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        if not isinstance(result["round"], int):
            result["round"] = extract_round_number(question)
        if not result["date"]:
            result["date"] = extract_date(question)

    if result["intent"] == "schedule_summary_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None

    if result["intent"] == "match_preparation_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None

    if result["intent"] == "meta_analysis_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not result["card_name"]:
            result["card_name"] = dependencies.resolve_card_name(
                question, cards_meta_data
            )
        result["analysis_type"] = (
            "meta_delta" if dependencies.is_meta_delta_query(question) else None
        )
    else:
        result["analysis_type"] = None

    if result["intent"] == "deck_query":
        resolved_deck_cards = dependencies.resolve_card_names(
            question, cards_meta_data
        )
        if len(resolved_deck_cards) == 8:
            result["deck_cards"] = resolved_deck_cards
            result["card_name"] = None
            result["rank"] = None
            result["top_n"] = None
        result["card_names"] = None
        if not result["deck_cards"] and not result["card_name"]:
            result["card_name"] = dependencies.resolve_card_name(
                question, cards_meta_data
            )
        if result["metric"] is None:
            result["metric"] = "usage_rate"
        if (
            not result["deck_cards"]
            and result["card_name"]
            and result["rank"] is None
            and result["top_n"] is None
        ):
            result["top_n"] = 5
        elif (
            not result["deck_cards"]
            and result["rank"] is None
            and result["top_n"] is None
            and any(
                keyword in question
                for keyword in ["热门卡组", "主流卡组", "卡组有哪些", "哪些卡组"]
            )
        ):
            result["top_n"] = 5

    if result["intent"] == "card_query":
        result["card_names"] = None
        if not result["card_name"]:
            result["card_name"] = dependencies.resolve_card_name(
                question, cards_meta_data
            )
        if result["card_name"] and not dependencies.is_card_ranking_query(question):
            result["rank"] = None
            result["top_n"] = None
        if result["metric"] is None:
            result["metric"] = get_metric(question)
        result["metrics"] = normalize_metrics(
            result["metrics"], question, result["intent"]
        )
        if result["metrics"]:
            result["metric"] = result["metrics"][0]

    if result["intent"] == "card_rank_lookup_query":
        result["compare_metric"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not result["card_name"]:
            result["card_name"] = dependencies.resolve_card_name(
                question, cards_meta_data
            )
        if result["metric"] is None:
            result["metric"] = get_metric(question)

    if result["intent"] == "card_compare_query":
        result["metric"] = None
        result["card_name"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not isinstance(result["card_names"], list) or len(result["card_names"]) < 2:
            result["card_names"] = dependencies.resolve_card_names(
                question, cards_meta_data
            )
        if result["compare_metric"] is None:
            result["compare_metric"] = get_metric(question)

    if result["intent"] == "card_cooccurrence_query":
        resolved_names = dependencies.resolve_card_names(question, cards_meta_data)
        result["metric"] = None
        result["compare_metric"] = None
        result["rank"] = None
        result["round"] = None
        result["date"] = None
        result["deck_cards"] = None
        if len(resolved_names) >= 2:
            result["card_name"] = None
            result["card_names"] = resolved_names[:2]
            result["top_n"] = None
        else:
            result["card_name"] = (
                resolved_names[0] if resolved_names else result["card_name"]
            )
            result["card_names"] = None
            result["top_n"] = (
                coerce_top_n_value(result["top_n"], max_n=30) or 10
                if dependencies.has_explicit_top_n_signal(question)
                else 10
            )

    if not result["parse_source"]:
        result["parse_source"] = "llm_parser"
    if result["parse_confidence"] not in {
        LOCAL_PARSE_CONFIDENCE_HIGH,
        LOCAL_PARSE_CONFIDENCE_MEDIUM,
        LOCAL_PARSE_CONFIDENCE_LOW,
    }:
        result["parse_confidence"] = LOCAL_PARSE_CONFIDENCE_MEDIUM
    if not result["parse_reason"]:
        result["parse_reason"] = "normalized parser output"

    entity_reference = dependencies.detect_entity_reference(
        question, cards_meta_data
    )
    if entity_reference["entity_mode"] == "loadout_entity":
        result.update(entity_reference)
    else:
        result["entity_mode"] = "base8"
        result["entity_type"] = None
        result["entity_name"] = None
        result["special_state"] = None

    if result["intent"] != "deck_query":
        result["deck_cards"] = None

    return result


__all__ = ["ParserNormalizationDependencies", "normalize_parsed_query"]
