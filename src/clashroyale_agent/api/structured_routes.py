"""Structured API route registration helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from clashroyale_agent.api.payloads import build_full_loadout_payload
from clashroyale_agent.api.schemas import (
    CardCompareRequest,
    DeckMatchupRequest,
    DeckProfileRequest,
    EntityCompareRequest,
)
from structured_query import StructuredQueryError


def register_structured_api_routes(
    app: FastAPI,
    *,
    default_dataset_scope: str,
    get_dataset_catalog: Callable[[], dict],
    get_repository: Callable[[str], Any],
    card_ranking_metrics: set[str],
) -> None:
    """Register structured dataset/card/entity/deck API routes on an app."""

    @app.get("/api/datasets")
    async def structured_datasets():
        return get_dataset_catalog()

    @app.get("/api/cards/catalog")
    async def structured_card_catalog(dataset_scope: str = default_dataset_scope):
        return get_repository(dataset_scope).card_catalog()

    @app.get("/api/cards/rankings")
    async def structured_card_rankings(
        dataset_scope: str = default_dataset_scope,
        sort_by: str = "usage_rate",
    ):
        if sort_by not in card_ranking_metrics:
            raise StructuredQueryError(
                "INVALID_CARD_RANKING_METRIC",
                "sort_by must be usage_rate, clean_win_rate, or rating.",
                details={"sort_by": sort_by, "allowed": list(card_ranking_metrics)},
            )
        return get_repository(dataset_scope).card_rankings(sort_by)

    @app.get("/api/cards/{card_id}/stats")
    async def structured_card_stats(card_id: str, dataset_scope: str = default_dataset_scope):
        return get_repository(dataset_scope).card_stats(card_id)

    @app.get("/api/entities/catalog")
    async def structured_entity_catalog(dataset_scope: str = default_dataset_scope):
        return get_repository(dataset_scope).entity_catalog()

    @app.get("/api/entities/rankings")
    async def structured_entity_rankings(
        dataset_scope: str = default_dataset_scope,
        sort_by: str = "usage_rate",
    ):
        return get_repository(dataset_scope).entity_rankings(sort_by)

    @app.get("/api/entities/{entity_id}/stats")
    async def structured_entity_stats(entity_id: str, dataset_scope: str = default_dataset_scope):
        return get_repository(dataset_scope).entity_stats(entity_id)

    @app.get("/api/loadouts/catalog")
    async def structured_loadout_catalog(dataset_scope: str = default_dataset_scope):
        return get_repository(dataset_scope).loadout_catalog()

    @app.post("/api/cards/compare")
    async def structured_card_compare(payload: CardCompareRequest):
        return get_repository(payload.dataset_scope).compare_cards(payload.card_ids)

    @app.post("/api/entities/compare")
    async def structured_entity_compare(payload: EntityCompareRequest):
        return get_repository(payload.dataset_scope).compare_entities(payload.entity_ids)

    @app.post("/api/decks/profile")
    async def structured_deck_profile(payload: DeckProfileRequest):
        repository = get_repository(payload.dataset_scope)
        if payload.deck_mode == "full_loadout":
            return repository.full_loadout_profile(build_full_loadout_payload(payload.loadout))
        return repository.deck_profile(payload.cards or [])

    @app.post("/api/decks/matchup")
    async def structured_deck_matchup(payload: DeckMatchupRequest):
        repository = get_repository(payload.dataset_scope)
        if payload.deck_mode == "full_loadout":
            return repository.full_loadout_matchup(
                build_full_loadout_payload(payload.loadout_a),
                build_full_loadout_payload(payload.loadout_b),
            )
        return repository.deck_matchup(payload.deck_a or [], payload.deck_b or [])

    @app.get("/api/meta/archetypes")
    async def structured_archetypes(dataset_scope: str = default_dataset_scope):
        return get_repository(dataset_scope).archetypes()


__all__ = ["register_structured_api_routes"]
