"""Request payload adapters for structured API routes."""

from __future__ import annotations

from clashroyale_agent.api.schemas import FullLoadoutRequest
from structured_query import StructuredQueryError


def build_full_loadout_payload(value: FullLoadoutRequest | None) -> dict:
    """Convert an API full-loadout request into the repository payload shape."""
    if value is None:
        raise StructuredQueryError(
            "INVALID_FULL_LOADOUT",
            "full_loadout mode requires a tower and exactly 8 configured cards.",
        )
    return {
        "schema_version": 1,
        "tower": {"id": value.tower_id},
        "cards": [
            {
                "id": card.card_id,
                "evolution_level": card.evolution_level,
                "elite": card.elite,
            }
            for card in value.cards
        ],
    }


__all__ = ["build_full_loadout_payload"]
