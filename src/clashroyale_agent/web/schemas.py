"""Request models shared by the browser UI routes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from clashroyale_agent.collection.rolling_corpus import DEFAULT_DATASET_SCOPE


class ChatRequest(BaseModel):
    """Validated browser chat submission."""

    message: str
    session_id: str | None = None
    user_id: str | None = None
    intent_hint: Literal["meta_analysis_query"] | None = None
    dataset_scope: str = DEFAULT_DATASET_SCOPE
    deck_mode: Literal["base8", "full_loadout"] = "base8"
    entity_mode: Literal["base8", "loadout_entity"] = "base8"


class LiveSampleSettingsRequest(BaseModel):
    target_battles: int


class FeedbackProxyRequest(BaseModel):
    request_id: str
    rating: str
    correction: str | None = None


class CardCompareProxyRequest(BaseModel):
    card_ids: list[str]
    dataset_scope: str = DEFAULT_DATASET_SCOPE


class EntityCompareProxyRequest(BaseModel):
    entity_ids: list[str]
    dataset_scope: str = DEFAULT_DATASET_SCOPE


class DeckProfileProxyRequest(BaseModel):
    cards: list[str] | None = None
    deck_mode: Literal["base8", "full_loadout"] = "base8"
    loadout: dict | None = None
    dataset_scope: str = DEFAULT_DATASET_SCOPE


class DeckMatchupProxyRequest(BaseModel):
    deck_a: list[str] | None = None
    deck_b: list[str] | None = None
    deck_mode: Literal["base8", "full_loadout"] = "base8"
    loadout_a: dict | None = None
    loadout_b: dict | None = None
    dataset_scope: str = DEFAULT_DATASET_SCOPE


__all__ = [
    "ChatRequest",
    "LiveSampleSettingsRequest",
    "FeedbackProxyRequest",
    "CardCompareProxyRequest",
    "EntityCompareProxyRequest",
    "DeckProfileProxyRequest",
    "DeckMatchupProxyRequest",
]
