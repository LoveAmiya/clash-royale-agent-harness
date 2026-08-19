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
from clashroyale_agent.qa.runtime_data_context import (
    merge_live_card_snapshot as merge_live_card_snapshot_orchestrated,
    select_answer_data_context,
)
from clashroyale_agent.qa.runtime_dependencies import (
    build_dataset_runtime_dependencies,
    build_snapshot_lifecycle_dependencies,
)
from clashroyale_agent.qa.runtime_parsing import (
    AnswerParseDependencies,
    ParsedAnswerRequest,
    parse_answer_request,
)
from clashroyale_agent.qa.runtime_pipeline import run_runtime_answer_pipeline
from clashroyale_agent.qa.runtime_answering import (
    AnswerExecutionContext,
    AnswerPipelineDependencies,
    build_external_api_unavailable_result,
    describe_parsed_request,
    execute_grounded_answer,
    prepare_answer_execution_context,
    query_requires_official_snapshot,
    run_answer_pipeline,
)
from clashroyale_agent.qa.runtime_streaming import (
    emit_semantic_content,
    split_answer_semantic_chunks,
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
    build_process_runtime_dependencies,
    handle_process_request,
    register_process_routes,
)
from clashroyale_agent.api.status_routes import register_status_routes
from clashroyale_agent.api.status_runtime import (
    StatusRouteDependencies,
    build_live_snapshot_status_from_runtime,
    build_readiness_status_from_runtime,
    build_status_route_dependencies,
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
from clashroyale_agent.api.lifespan import (
    RuntimeLifespanDependencies,
    build_runtime_lifespan_dependencies,
    runtime_lifespan,
)
from clashroyale_agent.api.messages import get_user_text
from clashroyale_agent.api.preheat import (
    acquire_rag_preheat_lock,
    find_active_rag_retriever,
    find_reusable_rag_retriever,
    resolve_rag_preheat_target,
    run_rag_preheat_in_thread,
)
from clashroyale_agent.api.rag_preheat import (
    RAGPreheatDependencies,
    build_rag_preheat_dependencies,
    preheat_rag_retriever,
)
from clashroyale_agent.api.runtime import (
    RuntimeAppDependencies,
    build_runtime_app_dependencies,
    create_registered_runtime_app,
)
from clashroyale_agent.api.runtime_facade import RuntimeFacade
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
try:
    from clashroyale_agent.qa.runtime_model import (
        build_chat_model as build_chat_model_orchestrated,
        build_parser_agent as build_parser_agent_orchestrated,
        load_card_catalog as load_card_catalog_orchestrated,
        load_json_file as load_json_file_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.qa.runtime_model import (
        build_chat_model as build_chat_model_orchestrated,
        build_parser_agent as build_parser_agent_orchestrated,
        load_card_catalog as load_card_catalog_orchestrated,
        load_json_file as load_json_file_orchestrated,
    )


logger = logging.getLogger(__name__)
RUNTIME_CONTRACT_VERSION = "strict-live-api-v2"
# Deterministic answers are emitted as semantic sections. A short interval lets
# each SSE frame reach the browser before the next section is produced.
SEMANTIC_CONTENT_INTERVAL_SECONDS = 0.12


def load_json_file(path: Path):
    if False:
        return load_json_file_orchestrated(path)
        raise FileNotFoundError(f"没有找到数据文件: {path.resolve()}")


def load_card_catalog(path: Path = CARD_ALIAS_FILE) -> list[dict]:
    return load_card_catalog_orchestrated(path, logger=logger)
    """Load the committed name catalog without treating it as snapshot metrics."""


def build_chat_model(api_key: str) -> OpenAIChatModel | OpenAIResponseModel:
    return build_chat_model_orchestrated(
        api_key,
        model_name=OPENAI_MODEL,
        client_kwargs=OPENAI_CLIENT_KWARGS,
        wire_api=OPENAI_WIRE_API,
        reasoning_effort=OPENAI_REASONING_EFFORT,
        chat_model=OpenAIChatModel,
        response_model=OpenAIResponseModel,
    )
    """根据中转站协议创建模型；当前 Codex 中转站使用 Responses API。"""


def build_parser_agent(api_key: str) -> ReActAgent:
    return build_parser_agent_orchestrated(
        api_key,
        parser_system_prompt=PARSER_SYSTEM_PROMPT,
        build_model=build_chat_model,
        agent_type=ReActAgent,
        formatter_type=OpenAIChatFormatter,
        memory_type=InMemoryMemory,
    )


# Keep a clean module-level binding for callers that patch the historical name.
load_json_file = load_json_file_orchestrated


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
    return RuntimeFacade(globals()).active_snapshot_id(app)


def _rag_alignment_state(app: FastAPI) -> dict:
    return RuntimeFacade(globals()).rag_alignment_state(app)


def _public_rag_validation(report: object) -> dict | None:
    return RuntimeFacade(globals()).public_rag_validation(report)


def _activate_snapshot_state(app: FastAPI, snapshot: dict) -> None:
    RuntimeFacade(globals()).activate_snapshot_state(app, snapshot)


def preheat_retriever(
    app: FastAPI,
    *,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> HybridRetriever | None:
    return RuntimeFacade(globals()).preheat_retriever(
        app,
        candidate_snapshot=candidate_snapshot,
        activate_snapshot=activate_snapshot,
    )


def ensure_retriever(app: FastAPI) -> HybridRetriever | None:
    return RuntimeFacade(globals()).ensure_retriever(app)


async def preheat_retriever_in_background(
    app: FastAPI,
    *,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> None:
    await RuntimeFacade(globals()).preheat_retriever_in_background(
        app,
        candidate_snapshot=candidate_snapshot,
        activate_snapshot=activate_snapshot,
    )


def get_live_sample_target(app: FastAPI) -> int:
    return RuntimeFacade(globals()).live_sample_target(app)


def get_live_sample_settings(app: FastAPI, refresh_status: str = "ready") -> dict:
    return RuntimeFacade(globals()).live_sample_settings(app, refresh_status)


def get_runtime_summary(app: FastAPI) -> dict:
    return RuntimeFacade(globals()).runtime_summary(app)


def _refresh_cooldown_seconds(failures: int) -> int:
    return RuntimeFacade(globals()).refresh_cooldown_seconds(failures)


def _record_live_refresh_attempt(
    app: FastAPI,
    *,
    status: str,
    snapshot: dict | None = None,
    error: str | None = None,
    finished_at: str | None = None,
) -> None:
    RuntimeFacade(globals()).record_live_refresh_attempt(
        app, status=status, snapshot=snapshot, error=error, finished_at=finished_at
    )


def _record_live_collection_progress(app: FastAPI, progress: dict) -> None:
    RuntimeFacade(globals()).record_live_collection_progress(app, progress)


def _snapshot_lifecycle_dependencies() -> SnapshotLifecycleDependencies:
    return RuntimeFacade(globals()).snapshot_lifecycle_dependencies()


def _dataset_runtime_dependencies(data_dir: Path | None = None) -> DatasetRuntimeDependencies:
    return RuntimeFacade(globals()).dataset_runtime_dependencies(data_dir)


def _status_route_dependencies() -> StatusRouteDependencies:
    return RuntimeFacade(globals()).status_route_dependencies()


def get_snapshot_artifact_status(data_dir: Path, snapshot_id: str | None) -> dict:
    return RuntimeFacade(globals()).snapshot_artifact_status(data_dir, snapshot_id)


def get_live_snapshot_status(app: FastAPI) -> dict:
    return RuntimeFacade(globals()).live_snapshot_status(app)


def get_readiness_status(
    app: FastAPI,
    *,
    external_api_required: bool | None = None,
    model_api_configured: bool | None = None,
) -> dict:
    return RuntimeFacade(globals()).readiness_status(
        app,
        external_api_required=external_api_required,
        model_api_configured=model_api_configured,
    )


def configure_live_sample_target(app: FastAPI, target_battles: int) -> dict:
    return RuntimeFacade(globals()).configure_live_sample_target(app, target_battles)


def _validate_dataset_scope(dataset_scope: str) -> str:
    return RuntimeFacade(globals()).validate_dataset_scope(dataset_scope)


def _active_snapshot_group_manifest(data_dir: Path = DATA_DIR) -> dict | None:
    return RuntimeFacade(globals()).active_snapshot_group_manifest(data_dir)


def _rag_scope_stats_for_manifest(
    app: FastAPI,
    manifest: dict,
    data_dir: Path,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    return RuntimeFacade(globals()).rag_scope_stats_for_manifest(app, manifest, data_dir)


def get_dataset_catalog(app: FastAPI) -> dict:
    return RuntimeFacade(globals()).dataset_catalog(app)


def get_structured_repository(
    app: FastAPI,
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
) -> StructuredStatsRepository:
    return RuntimeFacade(globals()).structured_repository(app, dataset_scope)


def ensure_dataset_retriever(app: FastAPI, dataset_scope: str) -> HybridRetriever | None:
    return RuntimeFacade(globals()).dataset_retriever(app, dataset_scope)


def restore_published_snapshot(app: FastAPI) -> dict | None:
    return RuntimeFacade(globals()).restore_published_snapshot(app)


def ensure_live_snapshot(app: FastAPI) -> dict | None:
    return RuntimeFacade(globals()).ensure_live_snapshot(app)


async def refresh_live_snapshot_loop(app: FastAPI) -> None:
    await RuntimeFacade(globals()).refresh_live_snapshot_loop(app)


async def follow_published_snapshot_loop(app: FastAPI) -> None:
    await RuntimeFacade(globals()).follow_published_snapshot_loop(app)


async def refresh_live_snapshot_once(app: FastAPI) -> None:
    await RuntimeFacade(globals()).refresh_live_snapshot_once(app)


def merge_live_card_snapshot(live_cards: list[dict], fallback_cards: list[dict]) -> list[dict]:
    return merge_live_card_snapshot_orchestrated(live_cards, fallback_cards)

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
    return await run_runtime_answer_pipeline(
        runtime=globals(),
        user_text=user_text,
        app=app,
        event_sink=event_sink,
        request_id=request_id,
        intent_hint=intent_hint,
        dataset_scope=dataset_scope,
        deck_mode=deck_mode,
        entity_mode=entity_mode,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    dependencies = build_runtime_lifespan_dependencies(RuntimeLifespanDependencies, globals())
    async with runtime_lifespan(app, dependencies):
        yield


app = create_registered_runtime_app(
    dependencies=build_runtime_app_dependencies(RuntimeAppDependencies, globals())
)


def _process_runtime_dependencies() -> ProcessRuntimeDependencies:
    """Bind the compatibility runtime's mutable configuration at request time."""
    return build_process_runtime_dependencies(ProcessRuntimeDependencies, globals())


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
