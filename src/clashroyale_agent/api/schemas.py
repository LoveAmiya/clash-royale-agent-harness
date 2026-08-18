"""Pydantic request models for the public API routes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rolling_corpus import DEFAULT_DATASET_SCOPE


class ProcessRequest(BaseModel):
    session_id: str | None = None
    user_id: str | None = None
    intent_hint: Literal["meta_analysis_query"] | None = None
    dataset_scope: str = DEFAULT_DATASET_SCOPE
    deck_mode: Literal["base8", "full_loadout"] = "base8"
    entity_mode: Literal["base8", "loadout_entity"] = "base8"
    input: list[dict]


class LiveSampleSettingsRequest(BaseModel):
    target_battles: int


class FeedbackRequest(BaseModel):
    request_id: str
    rating: str
    correction: str | None = None


class CardCompareRequest(BaseModel):
    card_ids: list[str]
    dataset_scope: str = DEFAULT_DATASET_SCOPE


class EntityCompareRequest(BaseModel):
    entity_ids: list[str]
    dataset_scope: str = DEFAULT_DATASET_SCOPE


class FullLoadoutCardRequest(BaseModel):
    card_id: str
    evolution_level: int = 0
    elite: bool


class FullLoadoutRequest(BaseModel):
    tower_id: str
    cards: list[FullLoadoutCardRequest]


class DeckProfileRequest(BaseModel):
    cards: list[str] | None = None
    deck_mode: Literal["base8", "full_loadout"] = "base8"
    loadout: FullLoadoutRequest | None = None
    dataset_scope: str = DEFAULT_DATASET_SCOPE


class DeckMatchupRequest(BaseModel):
    deck_a: list[str] | None = None
    deck_b: list[str] | None = None
    deck_mode: Literal["base8", "full_loadout"] = "base8"
    loadout_a: FullLoadoutRequest | None = None
    loadout_b: FullLoadoutRequest | None = None
    dataset_scope: str = DEFAULT_DATASET_SCOPE


__all__ = [
    "CardCompareRequest",
    "DeckMatchupRequest",
    "DeckProfileRequest",
    "EntityCompareRequest",
    "FeedbackRequest",
    "FullLoadoutCardRequest",
    "FullLoadoutRequest",
    "LiveSampleSettingsRequest",
    "ProcessRequest",
]
