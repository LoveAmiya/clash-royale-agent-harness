"""Compatibility wrapper for collection loadout normalization helpers."""

import app_config  # noqa: F401 - initializes the src package path for root runs.

from clashroyale_agent.collection.loadout_normalization import (
    ELITE_LEVEL_RULE,
    LOADOUT_SCHEMA_VERSION,
    canonical_loadout,
    full_loadout_signature,
    loadout_fact_signature,
    loadout_payload,
    loadout_quality,
    normalize_loadout_card,
    normalize_side_loadout,
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
