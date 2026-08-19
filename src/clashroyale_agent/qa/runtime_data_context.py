from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class AnswerDataContext:
    cards_meta_data: list[dict]
    top_decks_data: list[dict]
    card_deck_stats_data: dict
    structured_repository: Any
    data_context: dict
    live_metadata: dict
    rolling_manifest: dict | None


def merge_live_card_snapshot(live_cards: list[dict], fallback_cards: list[dict]) -> list[dict]:
    """Prefer sampled live cards while retaining named-card coverage."""
    seen_names = {str(card.get("card_name", "")).strip().lower() for card in live_cards}
    return list(live_cards) + [
        {**card, "_fallback_only": True}
        for card in fallback_cards
        if str(card.get("card_name", "")).strip().lower() not in seen_names
    ]


async def select_answer_data_context(
    *,
    parsed: dict,
    app: Any,
    dataset_scope: str,
    cards_meta_data: list[dict],
    top_decks_data: list[dict],
    card_deck_stats_data: dict,
    external_api_required: bool,
    supercell_live_data_enabled: bool,
    supercell_api_token: str | None,
    data_dir: Any,
    query_needs_rag: Callable[[dict], bool],
    query_requires_official_snapshot: Callable[[dict], bool],
    get_structured_repository: Callable[[Any, str], Any],
    active_snapshot_group_manifest: Callable[[Any], dict | None],
    merge_live_card_snapshot: Callable[[list[dict], list[dict]], list[dict]],
    snapshot_refresh_due: Callable[[dict], bool],
    build_external_api_unavailable_result: Callable[[dict, str, dict], Any],
    event_sink: Any = None,
) -> AnswerDataContext | Any:
    needs_official_snapshot = query_requires_official_snapshot(parsed)
    data_context = {
        "schedule": "disabled_clan_war_feature",
        "cards": "not_used" if not needs_official_snapshot else "not_loaded",
        "decks": "not_used" if not needs_official_snapshot else "not_loaded",
        "rag_documents": "not_used" if not query_needs_rag(parsed) else "not_loaded",
        "snapshot_id": None,
    }
    live_metadata = {"status": "not_required" if not needs_official_snapshot else "disabled"}
    rolling_manifest = active_snapshot_group_manifest(data_dir)
    structured_repository = None
    if rolling_manifest is not None and needs_official_snapshot:
        rolling_repository = get_structured_repository(app, dataset_scope)
        structured_repository = rolling_repository
        rolling_payload = rolling_repository.answer_payload()
        rolling_provenance = rolling_payload["provenance"]
        cards_meta_data = rolling_payload["cards_meta"]
        top_decks_data = rolling_payload["top_decks"]
        card_deck_stats_data = rolling_payload["card_deck_stats"]
        data_context.update(
            {
                "cards": "rolling_path_of_legend_scope",
                "decks": "rolling_path_of_legend_scope",
                "rag_documents": "rolling_path_of_legend_scope" if query_needs_rag(parsed) else "not_used",
                "snapshot_group_id": rolling_provenance["snapshot_group_id"],
                "snapshot_id": rolling_provenance["snapshot_id"],
                "dataset_scope": dataset_scope,
                "window_started_at": rolling_provenance["window_started_at"],
                "window_ended_at": rolling_provenance["window_ended_at"],
                "unique_battles": rolling_provenance["unique_battles"],
            }
        )
        live_metadata = {
            "status": "rolling_snapshot_group",
            **rolling_provenance,
        }
    elif supercell_live_data_enabled and supercell_api_token and (needs_official_snapshot or not external_api_required):
        if event_sink is not None:
            await event_sink.execution(
                step_id="snapshot",
                phase="data",
                status="running",
                title="正在确认官方数据快照",
                detail="读取当前完整 Supercell 官方排行榜战斗日志快照。",
            )
        live_snapshot = getattr(app.state, "live_snapshot", None)
        if not isinstance(live_snapshot, dict):
            live_snapshot = None
        if live_snapshot is not None:
            if external_api_required:
                cards_meta_data = list(live_snapshot["cards_meta"])
            else:
                cards_meta_data = merge_live_card_snapshot(live_snapshot["cards_meta"], cards_meta_data)
            top_decks_data = live_snapshot["top_decks"]
            card_deck_stats_data = dict(live_snapshot.get("card_deck_stats", {}))
            data_context.update(
                {
                    "cards": "official_weekly_snapshot",
                    "decks": "official_weekly_snapshot",
                    "rag_documents": "official_weekly_snapshot" if query_needs_rag(parsed) else "not_used",
                    "snapshot_id": live_snapshot.get("snapshot_id"),
                }
            )
            live_metadata = {
                "status": "live_sample",
                "source": "supercell_api",
                "snapshot_id": live_snapshot.get("snapshot_id"),
                "fetched_at": live_snapshot.get("fetched_at"),
                "sample_battles": live_snapshot.get("sample_battles"),
                "target_battles": live_snapshot.get("target_battles"),
                "shortfall_battles": live_snapshot.get("shortfall_battles", 0),
                "sampled_players": live_snapshot.get("sampled_players"),
                "fetched_players": live_snapshot.get("fetched_players"),
                "failed_players": live_snapshot.get("failed_players", 0),
                "freshness": "stale" if snapshot_refresh_due(live_snapshot) else "fresh",
                "static_card_fallback_count": 0 if external_api_required else len(cards_meta_data) - len(live_snapshot["cards_meta"]),
                "matchup_count": len(live_snapshot.get("deck_matchups", [])),
                "collection_metrics": live_snapshot.get("collection_metrics", {}),
            }
            if event_sink is not None:
                await event_sink.execution(
                    step_id="snapshot",
                    phase="data",
                    status="completed",
                    title="官方数据快照可用",
                    detail=(
                        f"使用 {live_snapshot.get('sample_battles', 0)} 场官方样本，"
                        f"快照 {live_snapshot.get('snapshot_id', 'unknown')}。"
                    ),
                )
        else:
            live_metadata = {"status": "unavailable" if external_api_required else "fallback_snapshot", "error": getattr(app.state, "live_error", None)}
            if external_api_required and needs_official_snapshot:
                return build_external_api_unavailable_result(
                    parsed,
                    "Supercell official API is unavailable. Live-data mode will not use cards_meta.json as a substitute.",
                    live_metadata,
                )
    elif external_api_required and needs_official_snapshot:
        return build_external_api_unavailable_result(
            parsed,
            "Supercell official API is unavailable. Live-data mode will not use cards_meta.json as a substitute.",
            {"status": "unavailable", "error": "supercell_api_token is not configured"},
        )

    if external_api_required and not needs_official_snapshot:
        # Keep the local schedule usable while the first official game-data
        # snapshot is collecting; no card/deck repository data crosses here.
        cards_meta_data = []
        top_decks_data = []
        card_deck_stats_data = {}

    return AnswerDataContext(
        cards_meta_data=cards_meta_data,
        top_decks_data=top_decks_data,
        card_deck_stats_data=card_deck_stats_data,
        structured_repository=structured_repository,
        data_context=data_context,
        live_metadata=live_metadata,
        rolling_manifest=rolling_manifest,
    )


__all__ = ["AnswerDataContext", "merge_live_card_snapshot", "select_answer_data_context"]
