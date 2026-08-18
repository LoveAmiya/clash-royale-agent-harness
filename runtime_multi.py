import json
import logging
import os
import asyncio
import time
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from analysis_boundaries import build_analysis_boundary_answer, detect_unsupported_analysis_request
from answer_presentation import normalize_answer_text

from app_config import (
    DATA_DIR,
    CARD_ALIAS_FILE,
    RUNTIME_HOST,
    RUNTIME_PORT,
    RUNTIME_ROLE,
    SNAPSHOT_FOLLOWER_POLL_SECONDS,
    SNAPSHOT_AUTO_FOLLOW_ENABLED,
    RAG_INDEX_MODE,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
    RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_FUSION_MODE,
    RETRIEVAL_RRF_K,
    META_RETRIEVAL_LANE_TOP_K,
    META_RERANK_TOP_N,
    META_COMPRESS_MAX_ITEMS,
    OPENAI_CLIENT_KWARGS,
    OPENAI_MODEL,
    PARSER_REASONING_EFFORT,
    OPENAI_REASONING_EFFORT,
    OPENAI_WIRE_API,
    PARSER_CALL_TIMEOUT_SECONDS,
    SUPERCELL_API_TOKEN,
    SUPERCELL_LIVE_DATA_ENABLED,
    EXTERNAL_API_REQUIRED,
    SUPERCELL_API_TIMEOUT_SECONDS,
    SUPERCELL_CACHE_TTL_SECONDS,
    SUPERCELL_MAX_TARGET_BATTLES,
    SUPERCELL_TARGET_BATTLES,
    SUPERCELL_POL_SEED_PLAYERS,
    SUPERCELL_LEADERBOARD_PLAYERS,
    SUPERCELL_BATTLES_PER_PLAYER,
    SUPERCELL_FETCH_CONCURRENCY,
    SUPERCELL_FALLBACK_PLAYER_TAGS,
    SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
    SUPERCELL_HIGH_VOLUME_MAX_RETRIES,
    SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
    SNAPSHOT_PROGRESS_INTERVAL_SECONDS,
    LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED,
    ADMIN_API_KEY,
    ALLOWED_ORIGINS,
    MAX_QUERY_CHARS,
    MAX_REQUEST_BODY_BYTES,
    PROCESS_MAX_CONCURRENT,
    PROCESS_RATE_LIMIT_PER_MINUTE,
    PROCESS_QUOTA_BACKEND,
    PROCESS_QUOTA_FAIL_MODE,
    PROCESS_QUOTA_KEY_PREFIX,
    PROCESS_QUOTA_LEASE_SECONDS,
    REDIS_URL,
    TRUST_PROXY_HEADERS,
    RAG_QUALITY_GATE_ENABLED,
    RAG_MIN_DOCUMENTS,
    RAG_MIN_SOURCE_TYPES,
    RAG_MIN_PROBE_RECALL_PERCENT,
    RAG_PROBES_PER_SOURCE,
    RAG_QUALITY_REPORT_DIR,
    FEEDBACK_DB_FILE,
    FEEDBACK_CACHE_MAX_ITEMS,
    FEEDBACK_CACHE_TTL_SECONDS,
    FEEDBACK_MAX_CORRECTION_CHARS,
)
from clashroyale_agent.api.schemas import (
    ProcessRequest,
)
from clashroyale_agent.api.sse import split_stream_chunks
from clashroyale_agent.qa.parser_orchestration import (
    ParserOrchestrationDependencies,
    parse_user_query_with_model,
)
from clashroyale_agent.qa.answer_routing import (
    query_needs_rag as packaged_query_needs_rag,
)
from clashroyale_agent.api.datasets import (
    build_dataset_catalog_payload,
    load_active_snapshot_group_manifest,
    rag_scope_stats_for_manifest,
    resolve_official_structured_repository,
    resolve_rolling_dataset_retriever,
    resolve_structured_group_repository,
    validate_dataset_scope,
)
from clashroyale_agent.api.dataset_runtime import (
    DatasetRuntimeDependencies,
    ensure_dataset_retriever as ensure_dataset_retriever_orchestrated,
    get_dataset_catalog as get_dataset_catalog_orchestrated,
    get_structured_repository as get_structured_repository_orchestrated,
    load_active_manifest as load_active_manifest_orchestrated,
    rag_scope_stats as rag_scope_stats_orchestrated,
    validate_scope as validate_dataset_scope_orchestrated,
)
from clashroyale_agent.api.process_routes import (
    ProcessRuntimeDependencies,
    handle_process_request,
    register_process_routes,
)
from clashroyale_agent.api.status_routes import register_status_routes
from clashroyale_agent.api.status_runtime import (
    StatusRouteDependencies,
)
from clashroyale_agent.api.settings import (
    FixedLiveSampleTargetError,
    build_fixed_live_sample_settings,
    fixed_live_sample_target,
    reject_live_sample_target_update,
)
from clashroyale_agent.api.lifecycle import (
    initialize_runtime_data_state,
    initialize_runtime_services,
    record_api_startup_baseline,
    shutdown_runtime_resources,
)
from clashroyale_agent.api.messages import get_user_text
from clashroyale_agent.api.preheat import (
    acquire_rag_preheat_lock,
    find_active_rag_retriever,
    find_reusable_rag_retriever,
    resolve_rag_preheat_target,
    run_rag_preheat_in_thread,
)
from clashroyale_agent.api.rag_preheat import RAGPreheatDependencies, preheat_rag_retriever
from clashroyale_agent.api.runtime import RuntimeAppDependencies, create_registered_runtime_app
from clashroyale_agent.api.snapshot_lifecycle import (
    SnapshotLifecycleDependencies,
    ensure_live_snapshot as ensure_live_snapshot_orchestrated,
    follow_published_snapshot_loop as follow_published_snapshot_loop_orchestrated,
    refresh_live_snapshot_loop as refresh_live_snapshot_loop_orchestrated,
    refresh_live_snapshot_once as refresh_live_snapshot_once_orchestrated,
    restore_published_snapshot as restore_published_snapshot_orchestrated,
)
from clashroyale_agent.api.snapshot_state import (
    activate_snapshot_state,
    active_snapshot_id,
    live_snapshot_refresh_gate,
    next_live_refresh_delay_seconds,
    record_live_collection_progress,
    record_live_refresh_attempt,
    refresh_cooldown_seconds,
)
from clashroyale_agent.api.status import (
    build_health_payload,
    build_live_sample_settings_payload,
    build_live_snapshot_runtime_state,
    build_live_snapshot_status_payload,
    build_metrics_body,
    build_model_status_payload,
    build_rag_alignment_state,
    build_readiness_status,
    build_runtime_summary,
    get_snapshot_artifact_status as build_snapshot_artifact_status,
    public_rag_validation,
)
from feedback_store import FeedbackStore, RecentAnswerCache
from hybrid_retriever import HybridRetriever, load_docs
from logging_config import configure_logging
from model_gateway import (
    generate_model_text,
    get_model_provider_status,
    record_model_stream_mode,
    render_model_provider_metrics,
)
from rag_quality import RAGQualityGateError, evaluate_rag_quality, persist_quality_report
from runtime_events import RuntimeEventEmitter
from runtime_hardening import (
    ProcessQuota,
    RequestBodyLimitMiddleware,
    RuntimeMetrics,
    authorize_admin,
    create_process_quota,
    normalize_request_id,
    redact_for_client,
    resolve_client_id,
)
from supercell_live import SupercellAPIClient
from structured_query import CARD_RANKING_METRICS, StructuredQueryError, StructuredStatsRepository
from rolling_corpus import DATASET_SCOPES, DATASET_WINDOW_DEFINITIONS, DEFAULT_DATASET_SCOPE
from rag_document_policy import (
    RAG_DOCUMENT_COUNT_SEMANTICS,
    RAG_SCOPE_COUNT_SEMANTICS,
    RAG_SOURCE_LIMITS,
    saturated_source_types,
    summarize_scope_documents,
)
from snapshot_store import (
    DAILY_REFRESH_INTERVAL,
    DAILY_TARGET_BATTLES,
    SNAPSHOT_RETENTION_DAYS,
    SNAPSHOT_RETENTION_MAX_COMPLETE,
    cleanup_snapshot_retention,
    is_complete_daily_snapshot,
    is_path_of_legend_snapshot,
    load_published_snapshot,
    load_published_snapshot_summary,
    publish_daily_snapshot,
    snapshot_age_seconds,
    snapshot_refresh_due,
    validate_snapshot_rag_documents,
)
from query_answering import AnswerResult, answer_query, read_trace
from query_parser import (
    LOCAL_PARSE_CONFIDENCE_HIGH,
    LOCAL_PARSE_CONFIDENCE_LOW,
    LOCAL_PARSE_CONFIDENCE_MEDIUM,
    PARSER_SYSTEM_PROMPT,
    apply_selected_entity_mode,
    build_parse_metadata,
    extract_json_block,
    extract_text_content,
    fallback_parse_multi_intent,
    merge_parse_metadata,
    normalize_multi_intent_query,
    subquery_semantic_key,
)


logger = logging.getLogger(__name__)
RUNTIME_CONTRACT_VERSION = "strict-live-api-v2"
# Deterministic answers are emitted as semantic sections. A short interval lets
# each SSE frame reach the browser before the next section is produced.
SEMANTIC_CONTENT_INTERVAL_SECONDS = 0.12


def load_json_file(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"没有找到数据文件: {path.resolve()}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_card_catalog(path: Path = CARD_ALIAS_FILE) -> list[dict]:
    """Load the committed name catalog without treating it as snapshot metrics."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("card alias configuration is unavailable: %s", exc)
        return []
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(cards, dict):
        logger.warning("card alias configuration has an invalid cards object")
        return []
    return [
        {
            "card_name": name,
            "aliases": entry.get("aliases", []),
            "display_name": entry.get("display_name"),
        }
        for name, entry in cards.items()
        if isinstance(name, str) and name.strip() and isinstance(entry, dict)
    ]


def build_chat_model(api_key: str) -> OpenAIChatModel | OpenAIResponseModel:
    """根据中转站协议创建模型；当前 Codex 中转站使用 Responses API。"""
    common_kwargs = {
        "model_name": OPENAI_MODEL,
        "api_key": api_key,
        "stream": False,
        "client_kwargs": OPENAI_CLIENT_KWARGS,
    }
    if OPENAI_WIRE_API == "responses":
        return OpenAIResponseModel(
            **common_kwargs,
            reasoning_effort=OPENAI_REASONING_EFFORT,
        )
    if OPENAI_WIRE_API == "chat_completions":
        return OpenAIChatModel(
            **common_kwargs,
            reasoning_effort=OPENAI_REASONING_EFFORT,
        )
    raise ValueError(f"Unsupported OPENAI_WIRE_API: {OPENAI_WIRE_API}")


def build_parser_agent(api_key: str) -> ReActAgent:
    parser_agent = ReActAgent(
        name="Parser",
        sys_prompt=PARSER_SYSTEM_PROMPT,
        model=build_chat_model(api_key),
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
    )
    parser_agent.set_console_output_enabled(enabled=False)
    return parser_agent


async def parse_user_query(user_text: str, cards_meta_data: list[dict], api_key: str | None) -> dict:
    return await parse_user_query_with_model(
        user_text,
        cards_meta_data,
        api_key,
        ParserOrchestrationDependencies(
            fallback_parse_multi_intent=fallback_parse_multi_intent,
            extract_json_block=extract_json_block,
            normalize_multi_intent_query=normalize_multi_intent_query,
            merge_parse_metadata=merge_parse_metadata,
            build_parse_metadata=build_parse_metadata,
            subquery_semantic_key=subquery_semantic_key,
            generate_model_text=generate_model_text,
            parser_system_prompt=PARSER_SYSTEM_PROMPT,
            parser_reasoning_effort=PARSER_REASONING_EFFORT,
            parser_timeout_seconds=PARSER_CALL_TIMEOUT_SECONDS,
            high_confidence=LOCAL_PARSE_CONFIDENCE_HIGH,
            medium_confidence=LOCAL_PARSE_CONFIDENCE_MEDIUM,
            low_confidence=LOCAL_PARSE_CONFIDENCE_LOW,
            logger=logger,
        ),
    )


def query_needs_rag(parsed: dict) -> bool:
    return packaged_query_needs_rag(parsed)


def _active_snapshot_id(app: FastAPI) -> str | None:
    return active_snapshot_id(app)


def _rag_alignment_state(app: FastAPI) -> dict:
    return build_rag_alignment_state(app)


def _public_rag_validation(report: object) -> dict | None:
    return public_rag_validation(report)


def _activate_snapshot_state(app: FastAPI, snapshot: dict) -> None:
    activate_snapshot_state(
        app,
        snapshot,
        now_monotonic=time.monotonic(),
        target_battles=DAILY_TARGET_BATTLES,
    )


def preheat_retriever(
    app: FastAPI,
    *,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> HybridRetriever | None:
    """Delegate snapshot-aligned RAG preheat orchestration to the API package."""
    dependencies = RAGPreheatDependencies(
        lock_factory=threading.Lock,
        load_docs=load_docs,
        validate_snapshot_rag_documents=validate_snapshot_rag_documents,
        retriever_factory=HybridRetriever,
        evaluate_rag_quality=evaluate_rag_quality,
        persist_quality_report=persist_quality_report,
        quality_gate_error=RAGQualityGateError,
        cleanup_snapshot_retention=cleanup_snapshot_retention,
        activate_snapshot_state=_activate_snapshot_state,
        logger=logger,
        index_mode=RAG_INDEX_MODE,
        quality_gate_enabled=RAG_QUALITY_GATE_ENABLED,
        external_api_required=EXTERNAL_API_REQUIRED,
        quality_report_dir=RAG_QUALITY_REPORT_DIR,
        min_documents=RAG_MIN_DOCUMENTS,
        min_source_types=RAG_MIN_SOURCE_TYPES,
        min_probe_recall=RAG_MIN_PROBE_RECALL_PERCENT / 100.0,
        probes_per_source=RAG_PROBES_PER_SOURCE,
        data_dir=DATA_DIR,
    )
    return preheat_rag_retriever(
        app,
        dependencies=dependencies,
        candidate_snapshot=candidate_snapshot,
        activate_snapshot=activate_snapshot,
    )


def ensure_retriever(app: FastAPI) -> HybridRetriever | None:
    """Return only an already-preheated retriever matching the active snapshot."""
    return find_active_rag_retriever(app, active_snapshot_id=_active_snapshot_id(app))


async def preheat_retriever_in_background(
    app: FastAPI,
    *,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> None:
    await run_rag_preheat_in_thread(
        preheat_retriever,
        app,
        candidate_snapshot=candidate_snapshot,
        activate_snapshot=activate_snapshot,
    )


def get_live_sample_target(app: FastAPI) -> int:
    """Production answers are always bound to the complete weekly sample."""
    return fixed_live_sample_target(DAILY_TARGET_BATTLES)


def get_live_sample_settings(app: FastAPI, refresh_status: str = "ready") -> dict:
    return build_fixed_live_sample_settings(
        app,
        fixed_target_battles=DAILY_TARGET_BATTLES,
        can_update_target=LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED,
        refresh_status=refresh_status,
    )


def get_runtime_summary(app: FastAPI) -> dict:
    return build_runtime_summary(app)


def _refresh_cooldown_seconds(failures: int) -> int:
    return refresh_cooldown_seconds(failures)


def _record_live_refresh_attempt(
    app: FastAPI,
    *,
    status: str,
    snapshot: dict | None = None,
    error: str | None = None,
    finished_at: str | None = None,
) -> None:
    record_live_refresh_attempt(
        app,
        status=status,
        default_target_battles=DAILY_TARGET_BATTLES,
        snapshot=snapshot,
        error=error,
        finished_at=finished_at,
    )


def _record_live_collection_progress(app: FastAPI, progress: dict) -> None:
    """Publish compact collector progress without invoking parser, RAG, or LLM code."""
    public_progress = record_live_collection_progress(app, progress)
    logger.info(
        "snapshot_collection_progress usable=%s target=%s players=%s requests=%s rate_limited=%s",
        public_progress.get("usable_battles"),
        public_progress.get("target_battles"),
        public_progress.get("fetched_players"),
        public_progress.get("request_count"),
        public_progress.get("rate_limited"),
    )


def _snapshot_lifecycle_dependencies() -> SnapshotLifecycleDependencies:
    """Bind runtime configuration and patchable compatibility symbols."""
    return SnapshotLifecycleDependencies(
        data_dir=DATA_DIR,
        runtime_role=RUNTIME_ROLE,
        live_data_enabled=SUPERCELL_LIVE_DATA_ENABLED,
        external_api_required=EXTERNAL_API_REQUIRED,
        api_token=SUPERCELL_API_TOKEN,
        daily_target_battles=DAILY_TARGET_BATTLES,
        daily_refresh_interval_seconds=DAILY_REFRESH_INTERVAL.total_seconds(),
        follower_poll_seconds=SNAPSHOT_FOLLOWER_POLL_SECONDS,
        client_factory=SupercellAPIClient,
        client_timeout_seconds=SUPERCELL_API_TIMEOUT_SECONDS,
        client_max_retries=SUPERCELL_HIGH_VOLUME_MAX_RETRIES,
        client_requests_per_second=SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
        leaderboard_players=SUPERCELL_LEADERBOARD_PLAYERS,
        seed_player_limit=SUPERCELL_POL_SEED_PLAYERS,
        battles_per_player=SUPERCELL_BATTLES_PER_PLAYER,
        fetch_concurrency=SUPERCELL_FETCH_CONCURRENCY,
        fallback_player_tags=SUPERCELL_FALLBACK_PLAYER_TAGS,
        max_refresh_seconds=SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
        progress_interval_seconds=SNAPSHOT_PROGRESS_INTERVAL_SECONDS,
        refresh_lock_factory=threading.Lock,
        is_path_of_legend_snapshot=is_path_of_legend_snapshot,
        snapshot_refresh_due=snapshot_refresh_due,
        live_snapshot_refresh_gate=live_snapshot_refresh_gate,
        next_live_refresh_delay_seconds=next_live_refresh_delay_seconds,
        refresh_cooldown_seconds=_refresh_cooldown_seconds,
        is_complete_daily_snapshot=is_complete_daily_snapshot,
        publish_daily_snapshot=publish_daily_snapshot,
        load_published_snapshot=load_published_snapshot,
        load_published_snapshot_summary=load_published_snapshot_summary,
        snapshot_age_seconds=snapshot_age_seconds,
        preheat_retriever=preheat_retriever,
        preheat_retriever_in_background=preheat_retriever_in_background,
        activate_snapshot_state=_activate_snapshot_state,
        active_snapshot_id=_active_snapshot_id,
        record_live_refresh_attempt=_record_live_refresh_attempt,
        record_live_collection_progress=_record_live_collection_progress,
        logger=logger,
    )


def _dataset_runtime_dependencies(data_dir: Path | None = None) -> DatasetRuntimeDependencies:
    """Bind runtime configuration and patchable dataset access dependencies."""
    return DatasetRuntimeDependencies(
        data_dir=DATA_DIR if data_dir is None else data_dir,
        dataset_scopes=DATASET_SCOPES,
        default_dataset_scope=DEFAULT_DATASET_SCOPE,
        dataset_window_definitions=DATASET_WINDOW_DEFINITIONS,
        rag_source_limits=RAG_SOURCE_LIMITS,
        rag_document_count_semantics=RAG_DOCUMENT_COUNT_SEMANTICS,
        rag_scope_count_semantics=RAG_SCOPE_COUNT_SEMANTICS,
        retrieval={
            "fusion_mode": RETRIEVAL_FUSION_MODE,
            "rrf_k": RETRIEVAL_RRF_K if RETRIEVAL_FUSION_MODE == "rrf" else None,
            "bm25_top_k": RETRIEVAL_TOP_K_BM25,
            "dense_top_k": RETRIEVAL_TOP_K_DENSE,
            "candidate_top_k": RETRIEVAL_FINAL_TOP_K,
            "typed_lane_top_k": META_RETRIEVAL_LANE_TOP_K,
            "evidence_top_n": META_RERANK_TOP_N,
            "context_max_items": META_COMPRESS_MAX_ITEMS,
        },
        saturated_source_types=saturated_source_types,
        summarize_scope_documents=summarize_scope_documents,
        validate_dataset_scope=validate_dataset_scope,
        load_active_snapshot_group_manifest=load_active_snapshot_group_manifest,
        rag_scope_stats_for_manifest=rag_scope_stats_for_manifest,
        build_dataset_catalog_payload=build_dataset_catalog_payload,
        resolve_structured_group_repository=resolve_structured_group_repository,
        resolve_official_structured_repository=resolve_official_structured_repository,
        resolve_rolling_dataset_retriever=resolve_rolling_dataset_retriever,
        pointer_loader=load_json_file,
        structured_repository_cls=StructuredStatsRepository,
        retriever_cls=HybridRetriever,
        lock_factory=threading.Lock,
        ensure_retriever=ensure_retriever,
        logger=logger,
    )


def _status_route_dependencies() -> StatusRouteDependencies:
    """Bind deferred status-route providers to runtime configuration."""
    return StatusRouteDependencies(
        register_status_routes=register_status_routes,
        build_health_payload=build_health_payload,
        get_readiness_status=get_readiness_status,
        build_model_status_payload=build_model_status_payload,
        get_model_provider_status=get_model_provider_status,
        build_metrics_body=build_metrics_body,
        runtime_metrics_factory=RuntimeMetrics,
        render_model_provider_metrics=render_model_provider_metrics,
        runtime_contract_version=lambda: RUNTIME_CONTRACT_VERSION,
        runtime_file=lambda: __file__,
        runtime_role=lambda: RUNTIME_ROLE,
        supercell_live_data_enabled=lambda: SUPERCELL_LIVE_DATA_ENABLED,
        supercell_api_token=lambda: SUPERCELL_API_TOKEN,
        snapshot_auto_follow_enabled=lambda: SNAPSHOT_AUTO_FOLLOW_ENABLED,
        external_api_required=lambda: EXTERNAL_API_REQUIRED,
        model_api_configured=lambda: bool(os.getenv("OPENAI_API_KEY")),
        live_sample_target_battles=get_live_sample_target,
        process_quota_backend=lambda: PROCESS_QUOTA_BACKEND,
    )


def get_snapshot_artifact_status(data_dir: Path, snapshot_id: str | None) -> dict:
    return build_snapshot_artifact_status(data_dir, snapshot_id)


def get_live_snapshot_status(app: FastAPI) -> dict:
    """Return display-safe provenance for the currently published data snapshot."""
    snapshot = getattr(app.state, "live_snapshot", None)
    runtime_state = build_live_snapshot_runtime_state(app, now_monotonic=time.monotonic())
    return build_live_snapshot_status_payload(
        app,
        snapshot,
        runtime_state=runtime_state,
        rag_alignment=_rag_alignment_state(app),
        live_data_enabled=SUPERCELL_LIVE_DATA_ENABLED,
        daily_target_battles=DAILY_TARGET_BATTLES,
        pol_seed_players=SUPERCELL_POL_SEED_PLAYERS,
        leaderboard_players=SUPERCELL_LEADERBOARD_PLAYERS,
        refresh_interval_seconds=int(DAILY_REFRESH_INTERVAL.total_seconds()),
        retention_days=SNAPSHOT_RETENTION_DAYS,
        retention_max_complete=SNAPSHOT_RETENTION_MAX_COMPLETE,
        data_dir=DATA_DIR,
        snapshot_age_seconds=snapshot_age_seconds,
        is_scope_verified=is_path_of_legend_snapshot,
    )


def get_readiness_status(
    app: FastAPI,
    *,
    external_api_required: bool | None = None,
    model_api_configured: bool | None = None,
) -> dict:
    """Return an operational readiness contract without exposing credentials.

    Liveness answers whether the Python process is alive. Readiness answers
    whether the configured strict data contract can serve a useful request.
    RAG preheating is reported as degraded because structured answers may still
    work while open-ended evidence answers are temporarily unavailable.
    """
    strict = EXTERNAL_API_REQUIRED if external_api_required is None else bool(external_api_required)
    model_configured = bool(os.getenv("OPENAI_API_KEY")) if model_api_configured is None else bool(model_api_configured)
    return build_readiness_status(
        app,
        strict=strict,
        model_configured=model_configured,
        is_snapshot_usable=is_complete_daily_snapshot,
        process_quota_backend=PROCESS_QUOTA_BACKEND,
        process_quota_fail_mode=PROCESS_QUOTA_FAIL_MODE,
        get_model_provider_status=get_model_provider_status,
    )


def configure_live_sample_target(app: FastAPI, target_battles: int) -> dict:
    try:
        reject_live_sample_target_update(target_battles, fixed_target_battles=DAILY_TARGET_BATTLES)
    except FixedLiveSampleTargetError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return get_live_sample_settings(app)


def _validate_dataset_scope(dataset_scope: str) -> str:
    return validate_dataset_scope_orchestrated(
        dataset_scope,
        dependencies=_dataset_runtime_dependencies(),
    )


def _active_snapshot_group_manifest(data_dir: Path = DATA_DIR) -> dict | None:
    return load_active_manifest_orchestrated(
        dependencies=_dataset_runtime_dependencies(data_dir),
    )


def _rag_scope_stats_for_manifest(
    app: FastAPI,
    manifest: dict,
    data_dir: Path,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    return rag_scope_stats_orchestrated(
        app,
        manifest,
        dependencies=_dataset_runtime_dependencies(data_dir),
    )


def get_dataset_catalog(app: FastAPI) -> dict:
    return get_dataset_catalog_orchestrated(
        app,
        dependencies=_dataset_runtime_dependencies(),
    )


def get_structured_repository(
    app: FastAPI,
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
) -> StructuredStatsRepository:
    return get_structured_repository_orchestrated(
        app,
        dataset_scope,
        dependencies=_dataset_runtime_dependencies(),
    )


def ensure_dataset_retriever(app: FastAPI, dataset_scope: str) -> HybridRetriever | None:
    return ensure_dataset_retriever_orchestrated(
        app,
        dataset_scope,
        dependencies=_dataset_runtime_dependencies(),
    )


def restore_published_snapshot(app: FastAPI) -> dict | None:
    return restore_published_snapshot_orchestrated(
        app,
        dependencies=_snapshot_lifecycle_dependencies(),
    )


def ensure_live_snapshot(app: FastAPI) -> dict | None:
    return ensure_live_snapshot_orchestrated(
        app,
        dependencies=_snapshot_lifecycle_dependencies(),
    )


async def refresh_live_snapshot_loop(app: FastAPI) -> None:
    await refresh_live_snapshot_loop_orchestrated(
        app,
        dependencies=_snapshot_lifecycle_dependencies(),
    )


async def follow_published_snapshot_loop(app: FastAPI) -> None:
    await follow_published_snapshot_loop_orchestrated(
        app,
        dependencies=_snapshot_lifecycle_dependencies(),
    )


async def refresh_live_snapshot_once(app: FastAPI) -> None:
    await refresh_live_snapshot_once_orchestrated(
        app,
        dependencies=_snapshot_lifecycle_dependencies(),
    )


def merge_live_card_snapshot(live_cards: list[dict], fallback_cards: list[dict]) -> list[dict]:
    """Prefer sampled live cards while retaining snapshot coverage for named-card queries."""
    seen_names = {str(card.get("card_name", "")).strip().lower() for card in live_cards}
    return list(live_cards) + [
        {**card, "_fallback_only": True}
        for card in fallback_cards
        if str(card.get("card_name", "")).strip().lower() not in seen_names
    ]


def query_requires_official_snapshot(parsed: dict) -> bool:
    """Return whether a parsed request needs the official weekly game snapshot.

    Removed clan-war intents are rejected locally without touching data APIs.
    In strict mode, every card, deck, ranking, or open-analysis subquery must
    receive a complete Supercell-derived snapshot rather than repository JSON.
    """
    intent = str(parsed.get("intent") or "").strip()
    if intent == "multi_intent":
        subqueries = parsed.get("subqueries")
        if not isinstance(subqueries, list) or not subqueries:
            return True
        if any(not isinstance(subquery, dict) for subquery in subqueries):
            return True
        return any(query_requires_official_snapshot(subquery) for subquery in subqueries)
    return intent not in {
        "schedule_query",
        "schedule_summary_query",
        "match_preparation_query",
        "reject",
    }


def build_external_api_unavailable_result(parsed: dict, message: str, live_metadata: dict) -> AnswerResult:
    """Return an explicit failure instead of treating a snapshot as live data."""
    return AnswerResult(
        answer=message,
        trace_id=None,
        parsed=parsed,
        plan=None,
        selected_skill=None,
        mode="unavailable",
        metadata={
            "external_api_required": True,
            "live_data": live_metadata,
            "model_stream": "unavailable",
        },
    )


def describe_parsed_request(parsed: dict) -> str:
    """Render validated routing facts without exposing private model reasoning."""
    intent = parsed.get("intent")
    if intent == "multi_intent":
        parts = [describe_parsed_request(item) for item in parsed.get("subqueries", []) if isinstance(item, dict)]
        return f"识别到 {len(parts)} 个子问题：" + "；".join(parts)
    if intent == "card_query":
        card = parsed.get("card_name") or "卡牌排行"
        metric_values = parsed.get("metrics") or ([parsed.get("metric")] if parsed.get("metric") else [])
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metrics = "、".join(metric_labels.get(metric, str(metric)) for metric in metric_values)
        return f"{card} 的{metrics or '数据'}查询"
    if intent == "card_compare_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("compare_metric"), "表现")
        return f"{' 与 '.join(names) or '两张卡牌'}的{metric}比较"
    if intent == "card_cooccurrence_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        if len(names) >= 2:
            return f"{' 与 '.join(names[:2])}共同出现次数查询"
        return f"{parsed.get('card_name') or '卡牌'}的常见搭配查询"
    if intent == "card_rank_lookup_query":
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("metric"), "表现")
        return f"卡牌{metric}第 {parsed.get('rank') or '?'} 名查询"
    if intent == "deck_query":
        if parsed.get("deck_cards"):
            return "精确八卡卡组查询"
        return f"{parsed.get('card_name') or '热门'}卡组查询"
    if intent == "meta_analysis_query":
        return "当前环境与主流卡组的开放分析"
    if intent == "match_preparation_query":
        return "备战开放分析"
    if intent == "schedule_query":
        return "赛程查询"
    return "未支持的问题类型"


async def build_answer(
    user_text: str,
    app: FastAPI,
    event_sink: RuntimeEventEmitter | None = None,
    request_id: str | None = None,
    intent_hint: Literal["meta_analysis_query"] | None = None,
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
    deck_mode: Literal["base8", "full_loadout"] = "base8",
    entity_mode: Literal["base8", "loadout_entity"] = "base8",
) -> AnswerResult:
    dataset_scope = _validate_dataset_scope(dataset_scope)
    # Bootstrap cards are a parser-only compatibility catalog. They identify
    # card names/aliases before the first official snapshot exists, but strict
    # answer Skills never receive these repository records.
    bootstrap_cards_meta_data = getattr(app.state, "bootstrap_cards_meta_data", app.state.cards_meta_data)
    cards_meta_data = app.state.cards_meta_data
    schedule_data = app.state.schedule_data
    top_decks_data = app.state.top_decks_data
    card_deck_stats_data = getattr(app.state, "card_deck_stats_data", {})
    api_key = os.getenv("OPENAI_API_KEY")

    if event_sink is not None:
        await event_sink.execution(
            step_id="parse",
            phase="parse",
            status="running",
            title="正在解析问题",
            detail=(
                "使用页面的已验证功能契约进入环境 RAG 分析。"
                if intent_hint == "meta_analysis_query"
                else "使用模型 API 识别可执行意图，不展示内部推理。"
            ),
        )

    analysis_boundary = detect_unsupported_analysis_request(user_text)
    if analysis_boundary is not None:
        parsed = {
            "intent": "reject",
            "parse_source": "analysis_boundary",
            "parse_confidence": LOCAL_PARSE_CONFIDENCE_HIGH,
            "parse_reason": "request requires evidence or a model not provided by the current snapshot",
            "boundary_code": analysis_boundary["code"],
            "model_parser_attempted": False,
            "model_parser_status": "not_required",
        }
        if event_sink is not None:
            await event_sink.execution(
                step_id="parse",
                phase="parse",
                status="completed",
                title="已确认数据边界",
                detail="该问题要求当前观测快照无法支持的预测、精确概率、因果效果或历史趋势。",
            )
        logger.info(
            "request rejected by analysis boundary request_id=%s boundary=%s",
            request_id,
            analysis_boundary["code"],
        )
        return AnswerResult(
            answer=build_analysis_boundary_answer(analysis_boundary),
            trace_id=None,
            parsed=parsed,
            plan=None,
            selected_skill=None,
            mode="boundary_reject",
            metadata={
                "boundary": analysis_boundary,
                "model_parser_attempted": False,
                "data_context": {"snapshot": "observational_only"},
            },
        )

    if EXTERNAL_API_REQUIRED and not api_key:
        parsed = {
            "intent": "reject",
            "parse_source": "model_api_unavailable",
            "parse_reason": "OPENAI_API_KEY is not configured",
        }
        return build_external_api_unavailable_result(
            parsed,
            "Model API is unavailable. Strict external API mode will not use local parsing as a substitute.",
            {"status": "not_checked"},
        )

    if intent_hint == "meta_analysis_query":
        parsed = {
            "intent": "meta_analysis_query",
            "parse_source": "interface_contract",
            "parse_confidence": LOCAL_PARSE_CONFIDENCE_HIGH,
            "parse_reason": "validated environment-analysis page contract",
            "model_parser_attempted": False,
            "model_parser_status": "not_required",
        }
    else:
        parsed = await parse_user_query(user_text, bootstrap_cards_meta_data, api_key)
    parsed = apply_selected_entity_mode(parsed, entity_mode)
    parsed_subqueries = parsed.get("subqueries") if isinstance(parsed.get("subqueries"), list) else []
    if parsed.get("entity_mode") == "loadout_entity" or any(
        subquery.get("entity_mode") == "loadout_entity"
        for subquery in parsed_subqueries
        if isinstance(subquery, dict)
    ):
        entity_mode = "loadout_entity"
        deck_mode = "full_loadout"
    logger.info(
        "request parsed request_id=%s intent=%s source=%s subqueries=%s",
        request_id,
        parsed.get("intent"),
        parsed.get("parse_source"),
        len(parsed.get("subqueries", [])) if isinstance(parsed.get("subqueries"), list) else 0,
    )
    if event_sink is not None:
        await event_sink.execution(
            step_id="parse",
            phase="parse",
            status="completed",
            title="已解析问题",
            detail=describe_parsed_request(parsed),
        )
    parse_source = parsed.get("parse_source")
    validated_fallback = (
        parse_source == "validated_fallback"
        and parsed.get("model_parser_attempted") is True
        and parsed.get("parse_confidence") in {LOCAL_PARSE_CONFIDENCE_HIGH, LOCAL_PARSE_CONFIDENCE_MEDIUM}
        and parsed.get("intent") != "reject"
    )
    parser_status = (
        "api"
        if parse_source == "llm_parser"
        else "interface_contract"
        if parse_source == "interface_contract"
        else "degraded"
        if validated_fallback
        else "fallback"
    )
    parser_api = {
        "status": parser_status,
        "parse_source": parsed.get("parse_source"),
        "model_status": parsed.get("model_parser_status"),
        "model": OPENAI_MODEL,
    }
    if EXTERNAL_API_REQUIRED and parser_api["status"] not in {"api", "degraded", "interface_contract"}:
        unavailable = build_external_api_unavailable_result(
            parsed,
            "Model parser did not return a validated API result. Strict external API mode will not use local parsing as a substitute.",
            {"status": "not_checked"},
        )
        unavailable.metadata["parser_api"] = {**parser_api, "status": "unavailable"}
        return unavailable

    needs_official_snapshot = query_requires_official_snapshot(parsed)
    data_context = {
        "schedule": "disabled_clan_war_feature",
        "cards": "not_used" if not needs_official_snapshot else "not_loaded",
        "decks": "not_used" if not needs_official_snapshot else "not_loaded",
        "rag_documents": "not_used" if not query_needs_rag(parsed) else "not_loaded",
        "snapshot_id": None,
    }
    live_metadata = {"status": "not_required" if not needs_official_snapshot else "disabled"}
    rolling_manifest = _active_snapshot_group_manifest(DATA_DIR)
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
    elif SUPERCELL_LIVE_DATA_ENABLED and SUPERCELL_API_TOKEN and (needs_official_snapshot or not EXTERNAL_API_REQUIRED):
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
            if EXTERNAL_API_REQUIRED:
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
                "static_card_fallback_count": 0 if EXTERNAL_API_REQUIRED else len(cards_meta_data) - len(live_snapshot["cards_meta"]),
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
            live_metadata = {"status": "unavailable" if EXTERNAL_API_REQUIRED else "fallback_snapshot", "error": getattr(app.state, "live_error", None)}
            if EXTERNAL_API_REQUIRED and needs_official_snapshot:
                return build_external_api_unavailable_result(
                    parsed,
                    "Supercell official API is unavailable. Live-data mode will not use cards_meta.json as a substitute.",
                    live_metadata,
                )
    elif EXTERNAL_API_REQUIRED and needs_official_snapshot:
        return build_external_api_unavailable_result(
            parsed,
            "Supercell official API is unavailable. Live-data mode will not use cards_meta.json as a substitute.",
            {"status": "unavailable", "error": "SUPERCELL_API_TOKEN is not configured"},
        )

    if EXTERNAL_API_REQUIRED and not needs_official_snapshot:
        # Keep the local schedule usable while the first official game-data
        # snapshot is collecting; no card/deck repository data crosses here.
        cards_meta_data = []
        top_decks_data = []
        card_deck_stats_data = {}

    retriever = ensure_dataset_retriever(app, dataset_scope) if query_needs_rag(parsed) else None
    if rolling_manifest is not None:
        rag_metadata = {
            "status": "ready" if retriever is not None else "not_ready",
            "snapshot_group_id": rolling_manifest["snapshot_group_id"],
            "snapshot_id": rolling_manifest["datasets"][dataset_scope]["snapshot_id"],
            "dataset_scope": dataset_scope,
            "docs_fingerprint": rolling_manifest.get("rag_docs_fingerprint"),
        }
    else:
        rag_metadata = {
            "status": getattr(app.state, "rag_status", "not_required"),
            "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
            "dataset_scope": dataset_scope,
            "docs_fingerprint": getattr(app.state, "rag_docs_fingerprint", None),
        }
    if query_needs_rag(parsed):
        # RAG indexing is preheated on startup and snapshot publication. User
        # requests only read an already activated index and never embed docs.
        retriever = ensure_dataset_retriever(app, dataset_scope)

    if event_sink is not None:
        await event_sink.execution(
            step_id="route",
            phase="route",
            status="completed",
            title="已确定执行路径",
            detail=(
                "多意图子任务将并发执行并按提问顺序汇总。"
                if parsed.get("intent") == "multi_intent"
                else "将执行 RAG 证据分析。"
                if query_needs_rag(parsed)
                else "将执行已验证的结构化查询，不调用 RAG。"
            ),
        )

    result = await answer_query(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        card_deck_stats=card_deck_stats_data,
        structured_repository=structured_repository,
        retriever=retriever,
        api_key=api_key or "",
        include_metadata=True,
        runtime_metadata={
            "request_id": request_id,
            "rag_status": rag_metadata["status"],
            "rag_snapshot_id": rag_metadata["snapshot_id"],
            "dataset_scope": dataset_scope,
            "deck_mode": deck_mode,
            "entity_mode": entity_mode,
            "data_context": data_context,
        },
        event_sink=event_sink,
        # A single RAG answer can stream sentence-level chunks after grounding
        # validation. Multi-intent answers stay buffered so concurrent branches
        # cannot interleave unrelated text in the same response.
        stream_content=parsed.get("intent") != "multi_intent" and query_needs_rag(parsed),
    )
    assert isinstance(result, AnswerResult)
    result.answer = normalize_answer_text(result.answer)
    # Direct deterministic Skills do not invoke text generation. Keep the
    # stream contract explicit rather than leaving a caller to infer it from a
    # missing field; RAG Skills overwrite this with streaming/fallback_chunked.
    result.metadata.setdefault("model_stream", "unavailable")
    result.metadata["live_data"] = live_metadata
    result.metadata["parser_api"] = parser_api
    result.metadata["rag"] = rag_metadata
    result.metadata["data_context"] = data_context
    result.metadata["presentation"] = "plain_text_zh_cn_v1"
    if request_id:
        result.metadata["request_id"] = request_id
    if event_sink is not None and event_sink.content_count == 0:
        await emit_semantic_content(event_sink, result.answer)
    return result


def split_answer_semantic_chunks(text: str):
    """Split deterministic answers at visible titles, lists, boundaries, and sources."""
    if not text:
        return
    chunks: list[str] = []
    current: list[str] = []
    current_kind = "paragraph"

    def flush() -> None:
        if current:
            chunks.append("".join(current))
            current.clear()

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            current.append(line)
            continue

        if stripped.startswith("## "):
            flush()
            current_kind = "heading"
        elif stripped.startswith("数据边界："):
            flush()
            current_kind = "boundary"
        elif stripped.startswith("参考来源："):
            flush()
            current_kind = "sources"
        elif stripped.startswith("**") and "请求指标" in stripped:
            flush()
            current_kind = "title"
        elif stripped.startswith(("- ", "* ", "+ ")):
            if current_kind != "list":
                flush()
                current_kind = "list"
        elif current_kind in {"heading", "title", "list"}:
            flush()
            current_kind = "paragraph"

        current.append(line)

    flush()
    yield from chunks


async def emit_semantic_content(event_sink: RuntimeEventEmitter, text: str) -> None:
    """Emit deterministic answer sections visibly, without simulating tokens."""
    chunks = list(split_answer_semantic_chunks(text))
    for index, chunk in enumerate(chunks):
        await event_sink.content(chunk, delta=True)
        if index < len(chunks) - 1:
            await asyncio.sleep(SEMANTIC_CONTENT_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_started_at = time.perf_counter()
    app.state.initialized = False
    await initialize_runtime_services(
        app,
        runtime_metrics_factory=RuntimeMetrics,
        recent_answer_cache_factory=RecentAnswerCache,
        feedback_store_factory=FeedbackStore,
        process_quota_factory=create_process_quota,
        feedback_db_file=FEEDBACK_DB_FILE,
        feedback_cache_max_items=FEEDBACK_CACHE_MAX_ITEMS,
        feedback_cache_ttl_seconds=FEEDBACK_CACHE_TTL_SECONDS,
        feedback_max_correction_chars=FEEDBACK_MAX_CORRECTION_CHARS,
        process_quota_backend=PROCESS_QUOTA_BACKEND,
        process_max_concurrent=PROCESS_MAX_CONCURRENT,
        process_rate_limit_per_minute=PROCESS_RATE_LIMIT_PER_MINUTE,
        redis_url=REDIS_URL,
        process_quota_lease_seconds=PROCESS_QUOTA_LEASE_SECONDS,
        process_quota_key_prefix=PROCESS_QUOTA_KEY_PREFIX,
        process_quota_fail_mode=PROCESS_QUOTA_FAIL_MODE,
    )
    # Metrics and RAG evidence come only from a published private snapshot.
    # The committed card catalog is retained solely for name normalization before
    # a snapshot-backed structured query is selected.
    initialize_runtime_data_state(
        app,
        bootstrap_cards_meta_data=load_card_catalog(),
        live_sample_target_battles=SUPERCELL_TARGET_BATTLES,
        lock_factory=threading.Lock,
    )

    logger.info(
        "startup complete schedule=%s decks=%s cards=%s retriever=lazy",
        len(app.state.schedule_data),
        len(app.state.top_decks_data),
        len(app.state.cards_meta_data),
    )
    if RUNTIME_ROLE == "api":
        app.state.rag_status = "not_ready"
        restore_published_snapshot(app)
        if getattr(app.state, "live_snapshot", None) is not None:
            app.state.rag_preheat_task = asyncio.create_task(preheat_retriever_in_background(app))
        if SNAPSHOT_AUTO_FOLLOW_ENABLED:
            app.state.live_refresh_task = asyncio.create_task(follow_published_snapshot_loop(app))
        else:
            logger.info("snapshot auto-follow disabled; API remains pinned until restart")
    elif SUPERCELL_LIVE_DATA_ENABLED and SUPERCELL_API_TOKEN:
        app.state.rag_status = "not_ready"
        restore_published_snapshot(app)
        if RUNTIME_ROLE != "collector" and getattr(app.state, "live_snapshot", None) is not None:
            app.state.rag_status = "not_ready"
            app.state.rag_preheat_task = asyncio.create_task(preheat_retriever_in_background(app))
        app.state.live_refresh_task = asyncio.create_task(refresh_live_snapshot_loop(app))
    elif EXTERNAL_API_REQUIRED:
        app.state.live_refresh_status = "unavailable"
    app.state.initialized = True
    record_api_startup_baseline(
        app,
        started_at=startup_started_at,
        completed_at=time.perf_counter(),
    )
    try:
        yield
    finally:
        await shutdown_runtime_resources(app)


app = create_registered_runtime_app(
    dependencies=RuntimeAppDependencies(
        title="ClashRoyaleMatchCoordinator",
        lifespan=lifespan,
        allowed_origins=ALLOWED_ORIGINS,
        request_body_limit_middleware_class=RequestBodyLimitMiddleware,
        max_request_body_bytes=MAX_REQUEST_BODY_BYTES,
        normalize_request_id=normalize_request_id,
        structured_error_type=StructuredQueryError,
        status_dependencies=_status_route_dependencies(),
        settings_route_options=lambda runtime_app: {
            "get_settings_payload": lambda: get_live_sample_settings(runtime_app),
            "configure_target": lambda target: configure_live_sample_target(runtime_app, target),
            "refresh_live_snapshot_once": lambda: refresh_live_snapshot_once(runtime_app),
            "live_sample_settings_admin_enabled": lambda: LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED,
            "admin_api_key": lambda: ADMIN_API_KEY,
            "authorize_admin": authorize_admin,
        },
        snapshot_route_options=lambda runtime_app: {
            "get_snapshot_status_payload": lambda: get_live_snapshot_status(runtime_app),
        },
        structured_route_options=lambda runtime_app: {
            "default_dataset_scope": DEFAULT_DATASET_SCOPE,
            "get_dataset_catalog": lambda: get_dataset_catalog(runtime_app),
            "get_repository": lambda scope: get_structured_repository(runtime_app, scope),
            "card_ranking_metrics": CARD_RANKING_METRICS,
        },
        feedback_route_options=lambda runtime_app: {
            "get_recent_answers": lambda: getattr(runtime_app.state, "recent_answers", None),
            "get_feedback_store": lambda: getattr(runtime_app.state, "feedback_store", None),
        },
    )
)


def _process_runtime_dependencies() -> ProcessRuntimeDependencies:
    """Bind the compatibility runtime's mutable configuration at request time."""
    return ProcessRuntimeDependencies(
        app=app,
        validate_dataset_scope=_validate_dataset_scope,
        load_active_manifest=lambda: _active_snapshot_group_manifest(DATA_DIR),
        default_dataset_scope=DEFAULT_DATASET_SCOPE,
        structured_query_error=StructuredQueryError,
        get_user_text=get_user_text,
        max_query_chars=MAX_QUERY_CHARS,
        normalize_request_id=normalize_request_id,
        resolve_client_id=resolve_client_id,
        trust_proxy_headers=TRUST_PROXY_HEADERS,
        runtime_metrics_factory=RuntimeMetrics,
        process_quota_factory=lambda: create_process_quota(
            backend=PROCESS_QUOTA_BACKEND,
            max_concurrent=PROCESS_MAX_CONCURRENT,
            requests_per_minute=PROCESS_RATE_LIMIT_PER_MINUTE,
            redis_url=REDIS_URL,
            lease_seconds=PROCESS_QUOTA_LEASE_SECONDS,
            key_prefix=PROCESS_QUOTA_KEY_PREFIX,
            fail_mode=PROCESS_QUOTA_FAIL_MODE,
        ),
        logger=logger,
        openai_model=OPENAI_MODEL,
        build_answer=build_answer,
        read_trace=read_trace,
        redact_for_client=redact_for_client,
        record_model_stream_mode=record_model_stream_mode,
        semantic_content_interval_seconds=SEMANTIC_CONTENT_INTERVAL_SECONDS,
    )


async def process(request: Request, payload: ProcessRequest | None = None):
    return await handle_process_request(
        request,
        payload,
        dependencies=_process_runtime_dependencies(),
    )

register_process_routes(app, process_endpoint=process)


if __name__ == "__main__":
    configure_logging()
    import uvicorn

    # Pass the in-memory application so Windows does not spawn a second interpreter
    # which may resolve a different runtime_multi module from its import path.
    uvicorn.run(app, host=RUNTIME_HOST, port=RUNTIME_PORT, reload=False)
