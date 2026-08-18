"""Normalize official battle-log loadouts without changing base battle identity."""

from __future__ import annotations

import hashlib
import json
from typing import Any


LOADOUT_SCHEMA_VERSION = 1
ELITE_LEVEL_RULE = "official_evolution_level_v1"


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_id(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text or None


def _explicit_elite(card: dict) -> tuple[bool | None, str | None]:
    for field in ("elite", "isElite", "eliteLevel", "elite_level"):
        if field not in card:
            continue
        value = card.get(field)
        if isinstance(value, bool):
            return value, f"official_{field}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value > 0, f"official_{field}"
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "yes", "1", "elite"}:
                return True, f"official_{field}"
            if normalized in {"false", "no", "0", "none"}:
                return False, f"official_{field}"
    evolution_level = _as_int(card.get("evolutionLevel", card.get("evolution_level"))) or 0
    if evolution_level in {0, 1}:
        return False, ELITE_LEVEL_RULE
    if evolution_level == 2:
        return True, ELITE_LEVEL_RULE
    return None, ELITE_LEVEL_RULE


def normalize_loadout_card(card: object) -> dict | None:
    if not isinstance(card, dict):
        return None
    card_id = _as_id(card.get("id"))
    name = str(card.get("name") or "").strip() or None
    if card_id is None and name is None:
        return None
    elite, elite_detection = _explicit_elite(card)
    evolution_level = _as_int(card.get("evolutionLevel", card.get("evolution_level"))) or 0
    special_mode = {
        0: "ordinary",
        1: "evolution",
        2: "elite",
    }.get(evolution_level, "unknown")
    return {
        "id": card_id,
        "name": name,
        "level": _as_int(card.get("level")),
        "max_level": _as_int(card.get("maxLevel", card.get("max_level"))),
        "evolution_level": evolution_level,
        "max_evolution_level": _as_int(card.get("maxEvolutionLevel", card.get("max_evolution_level"))),
        "special_mode": special_mode,
        "elite": elite,
        "elite_detection": elite_detection,
    }


def _tower_member(member: dict) -> dict | None:
    support_cards = member.get("supportCards")
    if isinstance(support_cards, list):
        for item in support_cards:
            if isinstance(item, dict):
                return item
    for field in ("towerTroop", "tower_troop"):
        item = member.get(field)
        if isinstance(item, dict):
            return item
    return None


def normalize_side_loadout(member: object) -> dict:
    side = member if isinstance(member, dict) else {}
    cards_value = side.get("cards")
    if not isinstance(cards_value, list):
        cards_value = side.get("deck")
    cards = [normalized for item in (cards_value or []) if (normalized := normalize_loadout_card(item))]
    cards.sort(key=lambda item: (item.get("id") or "", item.get("name") or ""))
    tower_source = _tower_member(side)
    tower = normalize_loadout_card(tower_source) if tower_source is not None else None
    card_ids = [card.get("id") for card in cards]
    card_ids_complete = len(cards) == 8 and all(card_ids) and len(set(card_ids)) == 8
    elite_complete = len(cards) == 8 and all(card.get("elite") is not None for card in cards)
    tower_complete = bool(tower and tower.get("id"))
    evolution_slots = sum(card.get("special_mode") == "evolution" for card in cards)
    elite_slots = sum(card.get("special_mode") == "elite" for card in cards)
    slot_contract = evolution_slots <= 2 and elite_slots <= 2 and evolution_slots + elite_slots <= 3
    complete = bool(card_ids_complete and elite_complete and tower_complete and slot_contract)
    return {
        "schema_version": LOADOUT_SCHEMA_VERSION,
        "tower": tower,
        "cards": cards,
        "complete": complete,
        "coverage": {
            "tower": tower_complete,
            "eight_cards": len(cards) == 8,
            "card_ids": card_ids_complete,
            "evolution": len(cards) == 8,
            "elite": elite_complete,
            "slot_contract": slot_contract,
        },
        "slot_counts": {"evolution": evolution_slots, "elite": elite_slots},
    }


def canonical_loadout(loadout: object) -> dict | None:
    if not isinstance(loadout, dict):
        return None
    tower = loadout.get("tower")
    cards = loadout.get("cards")
    if tower is not None and not isinstance(tower, dict):
        return None
    if not isinstance(cards, list):
        return None
    normalized_cards = [normalize_loadout_card(card) for card in cards]
    if any(card is None for card in normalized_cards):
        return None
    normalized_cards = [card for card in normalized_cards if card is not None]
    normalized_cards.sort(key=lambda item: (item.get("id") or "", item.get("name") or ""))
    normalized_tower = normalize_loadout_card(tower) if tower is not None else None
    card_ids = [card.get("id") for card in normalized_cards]
    card_ids_complete = len(normalized_cards) == 8 and all(card_ids) and len(set(card_ids)) == 8
    elite_complete = len(normalized_cards) == 8 and all(card.get("elite") is not None for card in normalized_cards)
    tower_complete = bool(normalized_tower and normalized_tower.get("id"))
    evolution_slots = sum(card.get("special_mode") == "evolution" for card in normalized_cards)
    elite_slots = sum(card.get("special_mode") == "elite" for card in normalized_cards)
    slot_contract = evolution_slots <= 2 and elite_slots <= 2 and evolution_slots + elite_slots <= 3
    return {
        "schema_version": LOADOUT_SCHEMA_VERSION,
        "tower": normalized_tower,
        "cards": normalized_cards,
        "complete": bool(card_ids_complete and elite_complete and tower_complete and slot_contract),
        "coverage": {
            "tower": tower_complete,
            "eight_cards": len(normalized_cards) == 8,
            "card_ids": card_ids_complete,
            "evolution": len(normalized_cards) == 8,
            "elite": elite_complete,
            "slot_contract": slot_contract,
        },
        "slot_counts": {"evolution": evolution_slots, "elite": elite_slots},
    }


def loadout_payload(loadout: dict) -> str:
    return json.dumps(loadout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def full_loadout_signature(loadout: object) -> str | None:
    normalized = canonical_loadout(loadout)
    if not normalized or not normalized["complete"]:
        return None
    signature_source: dict[str, Any] = {
        "schema_version": LOADOUT_SCHEMA_VERSION,
        "tower_id": normalized["tower"]["id"],
        "cards": [
            [card["id"], card["evolution_level"], card["elite"]]
            for card in normalized["cards"]
        ],
    }
    payload = json.dumps(signature_source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "loadout-v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def loadout_fact_signature(loadout: object) -> str | None:
    """Hash only immutable battle-time loadout state, excluding catalog metadata."""
    normalized = canonical_loadout(loadout)
    if not normalized or not normalized["complete"]:
        return None

    def card_state(card: dict) -> list[object]:
        return [card["id"], card["level"], card["evolution_level"], card["elite"]]

    signature_source: dict[str, Any] = {
        "schema_version": LOADOUT_SCHEMA_VERSION,
        "tower": card_state(normalized["tower"]),
        "cards": [card_state(card) for card in normalized["cards"]],
    }
    payload = json.dumps(signature_source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "loadout-fact-v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def loadout_quality(loadout: object) -> tuple[int, int, int, int]:
    normalized = canonical_loadout(loadout)
    if not normalized:
        return (0, 0, 0, 0)
    coverage = normalized["coverage"]
    return (
        int(normalized["complete"]),
        int(coverage["tower"]),
        sum(int(bool(card.get("id"))) for card in normalized["cards"]),
        sum(int(card.get("elite") is not None) for card in normalized["cards"]),
    )


__all__ = [
    "ELITE_LEVEL_RULE",
    "LOADOUT_SCHEMA_VERSION",
    "canonical_loadout",
    "full_loadout_signature",
    "loadout_fact_signature",
    "loadout_payload",
    "loadout_quality",
    "normalize_loadout_card",
    "normalize_side_loadout",
]
