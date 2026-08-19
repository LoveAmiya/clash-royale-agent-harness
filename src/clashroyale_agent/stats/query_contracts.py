"""Pure structured-query validation and presentation helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Callable


def warning(sample_count: int, *, threshold: int) -> dict | None:
    if sample_count >= threshold:
        return None
    return {
        "code": "LOW_SAMPLE_WARNING",
        "message": f"Only {sample_count} matched observations are available.",
        "threshold": threshold,
        "matched_sample_count": sample_count,
    }


def validate_card(card_id: str, catalog_names: set[str], error_type: type[ValueError]) -> str:
    value = str(card_id or "").strip()
    if value not in catalog_names:
        raise error_type(
            "INVALID_CARD_ID",
            "card_id must exactly match a card from the structured catalog.",
            details={"card_id": value},
        )
    return value


def validate_deck(
    cards: list[str],
    catalog_names: set[str],
    error_type: type[ValueError],
) -> tuple[tuple[str, ...], str]:
    if not isinstance(cards, list) or len(cards) != 8:
        raise error_type(
            "INVALID_DECK",
            "A structured deck must contain exactly 8 card IDs.",
            details={"card_count": len(cards) if isinstance(cards, list) else None},
        )
    normalized = [validate_card(card, catalog_names, error_type) for card in cards]
    if len(set(normalized)) != 8:
        raise error_type(
            "INVALID_DECK",
            "A structured deck cannot contain duplicate card IDs.",
            details={"duplicate_card_ids": sorted({card for card in normalized if normalized.count(card) > 1})},
        )
    deck = tuple(sorted(normalized))
    return deck, json.dumps(deck, ensure_ascii=False, separators=(",", ":"))


def validate_loadout(
    loadout: dict,
    *,
    canonical_loadout: Callable[[dict], dict | None],
    full_loadout_signature: Callable[[dict | None], str],
    error_type: type[ValueError],
) -> tuple[dict, str]:
    normalized = canonical_loadout(loadout)
    official_ids_valid = bool(
        normalized
        and re.fullmatch(r"\d+", str(normalized["tower"]["id"]))
        and all(re.fullmatch(r"\d+", str(card.get("id") or "")) for card in normalized["cards"])
    )
    signature = full_loadout_signature(normalized)
    special_modes_valid = bool(
        normalized
        and all(
            (int(card.get("evolution_level") or 0), card.get("elite")) in {(0, False), (1, False), (2, True)}
            for card in normalized.get("cards", [])
        )
    )
    if not normalized or not signature or not special_modes_valid or not official_ids_valid:
        raise error_type(
            "INVALID_FULL_LOADOUT",
            "A full loadout requires one official tower ID, 8 official card IDs, and official special modes 0=ordinary, 1=evolution, or 2=elite.",
            details={"deck_mode": "full_loadout"},
        )
    return normalized, signature


def display_loadout(
    loadout: dict | None,
    *,
    tower_display_names: dict[str, str],
    card_aliases: dict[str, list[str]],
) -> dict | None:
    if not isinstance(loadout, dict):
        return None
    displayed = json.loads(json.dumps(loadout, ensure_ascii=False))
    tower = displayed.get("tower")
    if isinstance(tower, dict):
        tower_name = str(tower.get("name") or tower.get("id") or "")
        tower["display_name_zh"] = tower_display_names.get(tower_name, tower_name)
    for card in displayed.get("cards") or []:
        if not isinstance(card, dict):
            continue
        card_name = str(card.get("name") or card.get("id") or "")
        card["display_name_zh"] = card_aliases.get(card_name, [card_name])[0]
    return displayed


def card_row(row: Any) -> dict:
    keys = (
        "card_name", "appearances", "wins", "losses", "draws", "usage_rate",
        "clean_win_rate", "net_win_rate", "wilson_lower_bound", "usage_percentile",
        "sample_confidence", "rating",
    )
    return {key: row[key] for key in keys}


def entity_display_name(row: Any, *, tower_display_names: dict[str, str], card_aliases: dict[str, list[str]]) -> str:
    if row["entity_type"] == "tower":
        payload = json.loads(row["entity_json"])
        name = str(payload.get("name") or row["tower_id"])
        return tower_display_names.get(name, name)
    card_name = str(row["card_name"] or row["card_id"])
    base_name = card_aliases.get(card_name, [card_name])[0]
    if row["special_state"] == "evolution":
        return f"瑙夐啋{base_name}"
    if row["special_state"] == "elite":
        return f"绮捐嫳{base_name}"
    return base_name


def entity_row(
    row: Any,
    *,
    low_sample_threshold: int,
    tower_display_names: dict[str, str],
    card_aliases: dict[str, list[str]],
) -> dict:
    keys = (
        "entity_id", "entity_type", "card_id", "card_name", "tower_id", "special_state",
        "appearances", "wins", "losses", "draws", "usage_rate", "clean_win_rate",
        "net_win_rate", "wilson_lower_bound", "usage_percentile", "sample_confidence", "rating",
    )
    return {
        **{key: row[key] for key in keys},
        "display_name_zh": entity_display_name(row, tower_display_names=tower_display_names, card_aliases=card_aliases),
        "is_low_sample": int(row["appearances"]) < low_sample_threshold,
    }


__all__ = [
    "card_row", "display_loadout", "entity_display_name", "entity_row",
    "validate_card", "validate_deck", "validate_loadout", "warning",
]
