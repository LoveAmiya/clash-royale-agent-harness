"""Entity-form detection and UI entity-mode application for parsed queries."""

from __future__ import annotations

import re
from typing import Callable

from clashroyale_agent.qa.card_aliases import normalize_card_alias
from clashroyale_agent.qa.parser_schema import TOWER_ENTITY_NAMES


CardResolver = Callable[[str, list[dict]], str | None]


def _has_form_keyword(normalized_question: str, card_name: str, keyword: str) -> bool:
    """Ignore a state word when it is part of the card's official name."""
    remainder = normalized_question
    canonical_name = normalize_card_alias(card_name)
    if canonical_name and canonical_name in remainder:
        remainder = remainder.replace(canonical_name, " ", 1)
    return bool(re.search(rf"\b{re.escape(keyword)}\b", remainder))


def detect_entity_reference(
    question: str, cards_meta_data: list[dict], resolve_card_name: CardResolver
) -> dict:
    """Identify an explicitly requested card form or tower without inventing an ID."""
    card_name = resolve_card_name(question, cards_meta_data)
    if card_name in TOWER_ENTITY_NAMES:
        return {
            "entity_mode": "loadout_entity",
            "entity_type": "tower",
            "entity_name": card_name,
            "special_state": "tower",
        }
    normalized = normalize_card_alias(question)
    if card_name and (
        "觉醒" in normalized
        or _has_form_keyword(normalized, card_name, "evolved")
        or _has_form_keyword(normalized, card_name, "evolution")
    ):
        base_card_name = card_name.removesuffix(" Evolution")
        return {
            "entity_mode": "loadout_entity",
            "entity_type": "card",
            "entity_name": base_card_name,
            "special_state": "evolution",
        }
    if card_name and (
        "精英" in normalized or _has_form_keyword(normalized, card_name, "elite")
    ):
        return {
            "entity_mode": "loadout_entity",
            "entity_type": "card",
            "entity_name": card_name,
            "special_state": "elite",
        }
    return {
        "entity_mode": "base8",
        "entity_type": None,
        "entity_name": None,
        "special_state": None,
    }


def apply_selected_entity_mode(parsed: dict, selected_entity_mode: str) -> dict:
    """Apply the UI data contract after parsing without overriding explicit forms."""
    result = dict(parsed)
    if result.get("intent") == "multi_intent":
        result["subqueries"] = [
            apply_selected_entity_mode(item, selected_entity_mode)
            if isinstance(item, dict)
            else item
            for item in result.get("subqueries", [])
        ]
        return result
    if (
        selected_entity_mode == "loadout_entity"
        and result.get("intent") == "card_query"
        and result.get("card_name")
        and result.get("entity_mode") != "loadout_entity"
    ):
        result.update(
            {
                "entity_mode": "loadout_entity",
                "entity_type": "card",
                "entity_name": result["card_name"],
                "special_state": "ordinary",
            }
        )
    return result


__all__ = ["apply_selected_entity_mode", "detect_entity_reference"]
