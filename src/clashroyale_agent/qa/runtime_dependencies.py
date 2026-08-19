"""Dependency-wiring builders for the runtime compatibility facade."""

from __future__ import annotations

from typing import Any


def build_snapshot_lifecycle_dependencies(dependencies_cls: Any, runtime: dict[str, Any]) -> Any:
    """Bind snapshot lifecycle providers from the runtime compatibility namespace."""
    return dependencies_cls(
        data_dir=runtime["DATA_DIR"], runtime_role=runtime["RUNTIME_ROLE"],
        live_data_enabled=runtime["SUPERCELL_LIVE_DATA_ENABLED"], external_api_required=runtime["EXTERNAL_API_REQUIRED"],
        api_token=runtime["SUPERCELL_API_TOKEN"], daily_target_battles=runtime["DAILY_TARGET_BATTLES"],
        daily_refresh_interval_seconds=runtime["DAILY_REFRESH_INTERVAL"].total_seconds(),
        follower_poll_seconds=runtime["SNAPSHOT_FOLLOWER_POLL_SECONDS"], client_factory=runtime["SupercellAPIClient"],
        client_timeout_seconds=runtime["SUPERCELL_API_TIMEOUT_SECONDS"], client_max_retries=runtime["SUPERCELL_HIGH_VOLUME_MAX_RETRIES"],
        client_requests_per_second=runtime["SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND"], leaderboard_players=runtime["SUPERCELL_LEADERBOARD_PLAYERS"],
        seed_player_limit=runtime["SUPERCELL_POL_SEED_PLAYERS"], battles_per_player=runtime["SUPERCELL_BATTLES_PER_PLAYER"],
        fetch_concurrency=runtime["SUPERCELL_FETCH_CONCURRENCY"], fallback_player_tags=runtime["SUPERCELL_FALLBACK_PLAYER_TAGS"],
        max_refresh_seconds=runtime["SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS"], progress_interval_seconds=runtime["SNAPSHOT_PROGRESS_INTERVAL_SECONDS"],
        refresh_lock_factory=runtime["threading"].Lock, is_path_of_legend_snapshot=runtime["is_path_of_legend_snapshot"],
        snapshot_refresh_due=runtime["snapshot_refresh_due"], live_snapshot_refresh_gate=runtime["live_snapshot_refresh_gate"],
        next_live_refresh_delay_seconds=runtime["next_live_refresh_delay_seconds"], refresh_cooldown_seconds=runtime["_refresh_cooldown_seconds"],
        is_complete_daily_snapshot=runtime["is_complete_daily_snapshot"], publish_daily_snapshot=runtime["publish_daily_snapshot"],
        load_published_snapshot=runtime["load_published_snapshot"], load_published_snapshot_summary=runtime["load_published_snapshot_summary"],
        snapshot_age_seconds=runtime["snapshot_age_seconds"], preheat_retriever=runtime["preheat_retriever"],
        preheat_retriever_in_background=runtime["preheat_retriever_in_background"], activate_snapshot_state=runtime["_activate_snapshot_state"],
        active_snapshot_id=runtime["_active_snapshot_id"], record_live_refresh_attempt=runtime["_record_live_refresh_attempt"],
        record_live_collection_progress=runtime["_record_live_collection_progress"], logger=runtime["logger"],
    )


def build_dataset_runtime_dependencies(dependencies_cls: Any, runtime: dict[str, Any], data_dir: Any = None) -> Any:
    """Bind dataset access providers from the runtime compatibility namespace."""
    return dependencies_cls(
        data_dir=runtime["DATA_DIR"] if data_dir is None else data_dir,
        dataset_scopes=runtime["DATASET_SCOPES"],
        default_dataset_scope=runtime["DEFAULT_DATASET_SCOPE"],
        dataset_window_definitions=runtime["DATASET_WINDOW_DEFINITIONS"],
        rag_source_limits=runtime["RAG_SOURCE_LIMITS"],
        rag_document_count_semantics=runtime["RAG_DOCUMENT_COUNT_SEMANTICS"],
        rag_scope_count_semantics=runtime["RAG_SCOPE_COUNT_SEMANTICS"],
        retrieval={
            "fusion_mode": runtime["RETRIEVAL_FUSION_MODE"],
            "rrf_k": runtime["RETRIEVAL_RRF_K"] if runtime["RETRIEVAL_FUSION_MODE"] == "rrf" else None,
            "bm25_top_k": runtime["RETRIEVAL_TOP_K_BM25"],
            "dense_top_k": runtime["RETRIEVAL_TOP_K_DENSE"],
            "candidate_top_k": runtime["RETRIEVAL_FINAL_TOP_K"],
            "typed_lane_top_k": runtime["META_RETRIEVAL_LANE_TOP_K"],
            "evidence_top_n": runtime["META_RERANK_TOP_N"],
            "context_max_items": runtime["META_COMPRESS_MAX_ITEMS"],
        },
        saturated_source_types=runtime["saturated_source_types"],
        summarize_scope_documents=runtime["summarize_scope_documents"],
        validate_dataset_scope=runtime["validate_dataset_scope"],
        load_active_snapshot_group_manifest=runtime["load_active_snapshot_group_manifest"],
        rag_scope_stats_for_manifest=runtime["rag_scope_stats_for_manifest"],
        build_dataset_catalog_payload=runtime["build_dataset_catalog_payload"],
        resolve_structured_group_repository=runtime["resolve_structured_group_repository"],
        resolve_official_structured_repository=runtime["resolve_official_structured_repository"],
        resolve_rolling_dataset_retriever=runtime["resolve_rolling_dataset_retriever"],
        pointer_loader=runtime["load_json_file"],
        structured_repository_cls=runtime["StructuredStatsRepository"],
        retriever_cls=runtime["HybridRetriever"],
        lock_factory=runtime["threading"].Lock,
        ensure_retriever=runtime["ensure_retriever"],
        logger=runtime["logger"],
    )


__all__ = ["build_snapshot_lifecycle_dependencies", "build_dataset_runtime_dependencies"]
