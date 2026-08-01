import json
import logging
import os
import asyncio
import time
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from analysis_boundaries import build_analysis_boundary_answer, detect_unsupported_analysis_request
from answer_presentation import normalize_answer_text

from app_config import (
    DATA_DIR,
    CARDS_META_FILE,
    RUNTIME_HOST,
    RUNTIME_PORT,
    RUNTIME_ROLE,
    SNAPSHOT_FOLLOWER_POLL_SECONDS,
    SNAPSHOT_AUTO_FOLLOW_ENABLED,
    RAG_INDEX_MODE,
    SCHEDULE_FILE,
    OPENAI_CLIENT_KWARGS,
    OPENAI_MODEL,
    PARSER_REASONING_EFFORT,
    OPENAI_REASONING_EFFORT,
    OPENAI_WIRE_API,
    PARSER_CALL_TIMEOUT_SECONDS,
    TOP_DECKS_FILE,
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
    build_parse_metadata,
    extract_json_block,
    extract_text_content,
    fallback_parse_multi_intent,
    merge_parse_metadata,
    normalize_multi_intent_query,
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


def _loadout_request_payload(value: FullLoadoutRequest | None) -> dict:
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


def get_user_text(request: ProcessRequest) -> str:
    for message in request.input:
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if block.get("type") == "text":
                return str(block.get("text", "")).strip()
    return ""


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
    local_parsed = fallback_parse_multi_intent(user_text, cards_meta_data)
    if not api_key:
        logger.warning("no api key available, using fallback parser result")
        return {
            **local_parsed,
            "model_parser_attempted": False,
            "model_parser_status": "not_configured",
        }

    def reconciled_local_parse(reason: str) -> dict:
        return {
            **merge_parse_metadata(
                local_parsed,
                build_parse_metadata(
                    parse_source="llm_parser",
                    parse_confidence=local_parsed.get("parse_confidence", LOCAL_PARSE_CONFIDENCE_HIGH),
                    parse_reason=reason,
                ),
            ),
            "model_parser_attempted": True,
            "model_parser_status": "validated_reconciled",
        }

    def validated_fallback(reason: str, status: str) -> dict:
        confidence = local_parsed.get("parse_confidence", LOCAL_PARSE_CONFIDENCE_LOW)
        can_continue = (
            local_parsed.get("intent") != "reject"
            and confidence in {LOCAL_PARSE_CONFIDENCE_HIGH, LOCAL_PARSE_CONFIDENCE_MEDIUM}
        )
        return {
            **merge_parse_metadata(
                local_parsed,
                build_parse_metadata(
                    parse_source="validated_fallback" if can_continue else local_parsed.get("parse_source", "local_rule"),
                    parse_confidence=confidence,
                    parse_reason=reason,
                ),
            ),
            "model_parser_attempted": True,
            "model_parser_status": status,
        }

    try:
        parse_result = await asyncio.wait_for(
            generate_model_text(
                api_key=api_key,
                instructions=PARSER_SYSTEM_PROMPT,
                input_text=user_text,
                reasoning_effort=PARSER_REASONING_EFFORT,
            ),
            timeout=PARSER_CALL_TIMEOUT_SECONDS,
        )
        parse_text = parse_result
        logger.debug("parser returned public text chars=%s", len(parse_text))

        parsed = extract_json_block(parse_text)
        if parsed is None:
            logger.warning("parser returned non-json output, using fallback parser")
            return validated_fallback(
                "llm parser returned non-json output; kept locally validated structured parse",
                "invalid_response",
            )

        normalized = normalize_multi_intent_query(parsed, user_text, cards_meta_data)
        local_intents = [item.get("intent") for item in local_parsed.get("subqueries", [])]
        normalized_intents = [item.get("intent") for item in normalized.get("subqueries", [])]
        if local_parsed.get("intent") == "multi_intent" and (
            normalized.get("intent") != "multi_intent" or normalized_intents != local_intents
        ):
            return reconciled_local_parse(
                "gpt-5.5 parser output was reconciled to the high-confidence local multi-intent decomposition",
            )
        if normalized.get("intent") == "reject" and local_parsed.get("intent") != "reject":
            # A valid model response was received. The final route is the
            # deterministic reconciliation of that response with known cards
            # and supported intents, so it remains an API-validated parse.
            return reconciled_local_parse(
                "gpt-5.5 parser output was reconciled to the locally validated supported route",
            )
        return {
            **merge_parse_metadata(
                normalized,
                build_parse_metadata(
                    parse_source="llm_parser",
                    parse_confidence=LOCAL_PARSE_CONFIDENCE_HIGH,
                    parse_reason="gpt-5.5 structured parser output validated locally",
                ),
            ),
            "model_parser_attempted": True,
            "model_parser_status": "validated",
        }
    except Exception as exc:
        status = "timeout" if isinstance(exc, TimeoutError) else "error"
        logger.warning(
            "parser agent failed, using validated fallback error_type=%s",
            type(exc).__name__,
        )
        return validated_fallback(
            f"llm parser failed; kept locally validated structured parse: {type(exc).__name__}",
            status,
        )


def query_needs_rag(parsed: dict) -> bool:
    if parsed.get("intent") == "multi_intent":
        return any(query_needs_rag(subquery) for subquery in parsed.get("subqueries", []))
    intent = parsed.get("intent")
    if intent == "meta_analysis_query":
        return True
    if intent == "deck_query":
        return (
            parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    if intent == "card_query":
        if parsed.get("entity_mode") == "loadout_entity":
            return True
        return (
            parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    return False


def _active_snapshot_id(app: FastAPI) -> str | None:
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    return snapshot_id if isinstance(snapshot_id, str) and snapshot_id else None


def _rag_alignment_state(app: FastAPI) -> dict:
    snapshot = getattr(app.state, "live_snapshot", None)
    retriever = getattr(app.state, "retriever", None)
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    rag_snapshot_id = getattr(app.state, "rag_snapshot_id", None)
    snapshot_fingerprint = snapshot.get("rag_docs_fingerprint") if isinstance(snapshot, dict) else None
    active_fingerprint = getattr(app.state, "rag_docs_fingerprint", None)
    index_fingerprint = getattr(retriever, "docs_fingerprint", None)
    snapshot_aligned = bool(snapshot_id and snapshot_id == rag_snapshot_id)
    fingerprint_aligned = bool(
        snapshot_fingerprint
        and snapshot_fingerprint == active_fingerprint
        and snapshot_fingerprint == index_fingerprint
    )
    return {
        "snapshot_id": rag_snapshot_id,
        "snapshot_docs_fingerprint": snapshot_fingerprint,
        "active_docs_fingerprint": active_fingerprint,
        "index_docs_fingerprint": index_fingerprint,
        "snapshot_aligned": snapshot_aligned,
        "fingerprint_aligned": fingerprint_aligned,
        "fully_aligned": snapshot_aligned and fingerprint_aligned,
    }


def _public_rag_validation(report: object) -> dict | None:
    if not isinstance(report, dict):
        return None
    invalid_ids = report.get("invalid_doc_ids") if isinstance(report.get("invalid_doc_ids"), list) else []
    return {
        key: report.get(key)
        for key in (
            "schema_version",
            "snapshot_id",
            "docs_fingerprint",
            "document_count",
            "source_counts",
            "card_documents_checked",
            "deck_documents_checked",
            "matchup_documents_checked",
            "passed",
            "failures",
        )
    } | {
        "invalid_document_count": len(invalid_ids),
        "invalid_doc_ids_sample": invalid_ids[:20],
    }


def _activate_snapshot_state(app: FastAPI, snapshot: dict) -> None:
    """Switch every structured-data view to one already validated snapshot."""
    app.state.live_snapshot = snapshot
    app.state.live_snapshot_at = time.monotonic()
    app.state.live_snapshot_target_battles = DAILY_TARGET_BATTLES
    app.state.cards_meta_data = list(snapshot.get("cards_meta", []))
    app.state.top_decks_data = list(snapshot.get("top_decks", []))
    app.state.card_deck_stats_data = dict(snapshot.get("card_deck_stats", {}))


def preheat_retriever(
    app: FastAPI,
    *,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> HybridRetriever | None:
    """Validate and build an index, then atomically activate its evidence boundary."""
    target_snapshot = candidate_snapshot if isinstance(candidate_snapshot, dict) else getattr(app.state, "live_snapshot", None)
    snapshot_id = target_snapshot.get("snapshot_id") if isinstance(target_snapshot, dict) else None
    if not snapshot_id:
        app.state.rag_status = "not_ready"
        return None

    lock = getattr(app.state, "rag_preheat_lock", None)
    if lock is None:
        lock = threading.Lock()
        app.state.rag_preheat_lock = lock
    if not lock.acquire(blocking=False):
        return None

    previous_status = getattr(app.state, "rag_status", "not_ready")
    previous_retriever = getattr(app.state, "retriever", None)
    previous_snapshot_id = getattr(app.state, "rag_snapshot_id", None)
    previous_fingerprint = getattr(app.state, "rag_docs_fingerprint", None)
    try:
        app.state.rag_candidate_status = "building"
        if previous_retriever is None:
            app.state.rag_status = "building"
        app.state.rag_error = None
        rag_docs = load_docs()
        validation = validate_snapshot_rag_documents(target_snapshot, rag_docs)
        app.state.rag_candidate_validation = validation
        if not validation["passed"]:
            raise ValueError("RAG documents failed full snapshot evidence validation")
        docs_fingerprint = validation["docs_fingerprint"]
        snapshot_fingerprint = target_snapshot.get("rag_docs_fingerprint")
        if snapshot_fingerprint != docs_fingerprint:
            raise ValueError("RAG document fingerprint does not match the active official snapshot")

        existing = getattr(app.state, "retriever", None)
        existing_fingerprint = getattr(existing, "docs_fingerprint", None)
        if (
            not activate_snapshot
            and getattr(app.state, "rag_snapshot_id", None) == snapshot_id
            and getattr(app.state, "rag_docs_fingerprint", None) == docs_fingerprint
            and existing_fingerprint == docs_fingerprint
            and existing is not None
        ):
            app.state.rag_status = "ready" if getattr(existing, "dense_available", False) else "bm25_only"
            app.state.rag_candidate_status = app.state.rag_status
            app.state.rag_document_validation = validation
            return existing

        candidate = HybridRetriever(rag_docs, in_memory=RAG_INDEX_MODE == "memory")
        if candidate.snapshot_id != snapshot_id:
            raise ValueError("built retriever does not match the active official weekly snapshot")
        if candidate.docs_fingerprint != docs_fingerprint:
            raise ValueError("built retriever fingerprint does not match validated RAG documents")
        if RAG_QUALITY_GATE_ENABLED and EXTERNAL_API_REQUIRED:
            quality_report = evaluate_rag_quality(
                snapshot_id,
                rag_docs,
                candidate,
                min_documents=RAG_MIN_DOCUMENTS,
                min_source_types=RAG_MIN_SOURCE_TYPES,
                min_probe_recall=RAG_MIN_PROBE_RECALL_PERCENT / 100.0,
                probes_per_source=RAG_PROBES_PER_SOURCE,
            )
            persist_quality_report(quality_report, RAG_QUALITY_REPORT_DIR)
            app.state.rag_quality_report = quality_report
            if not quality_report["passed"]:
                raise RAGQualityGateError("RAG index did not meet the configured snapshot quality gate")
        if not activate_snapshot and _active_snapshot_id(app) != snapshot_id:
            # A newer snapshot was published while embedding. Do not replace it
            # with a retriever built for an older evidence boundary.
            app.state.rag_status = "not_ready"
            return None

        if activate_snapshot:
            _activate_snapshot_state(app, target_snapshot)
        app.state.retriever = candidate
        app.state.rag_snapshot_id = snapshot_id
        app.state.rag_docs_fingerprint = docs_fingerprint
        app.state.rag_document_validation = validation
        app.state.rag_status = "ready" if candidate.dense_available else "bm25_only"
        app.state.rag_candidate_status = app.state.rag_status
        app.state.rag_candidate_error = None
        if previous_retriever is not None and previous_retriever is not candidate:
            close_previous = getattr(previous_retriever, "close", None)
            if callable(close_previous):
                close_previous()
        logger.info(
            "rag_preheat_complete snapshot_id=%s documents=%s docs_fingerprint=%s mode=%s",
            snapshot_id,
            len(rag_docs),
            docs_fingerprint[:12],
            app.state.rag_status,
        )
        if RAG_INDEX_MODE != "memory":
            retention = cleanup_snapshot_retention(
                DATA_DIR,
                active_snapshot_id=str(snapshot_id),
            )
            logger.info(
                "snapshot_retention_complete retained=%s removed=%s",
                retention["retained_snapshot_ids"],
                retention["removed_snapshot_ids"],
            )
        return candidate
    except Exception as exc:
        # Keep an old retriever in memory for rollback, but never use it for a
        # newer snapshot because the evidence boundary would be wrong.
        active_snapshot = getattr(app.state, "live_snapshot", None)
        old_index_usable = bool(
            previous_status in {"ready", "bm25_only"}
            and previous_retriever is not None
            and previous_snapshot_id
            and previous_fingerprint
            and isinstance(active_snapshot, dict)
            and active_snapshot.get("snapshot_id") == previous_snapshot_id
            and active_snapshot.get("rag_docs_fingerprint") == previous_fingerprint
            and getattr(previous_retriever, "docs_fingerprint", None) == previous_fingerprint
        )
        app.state.rag_status = previous_status if old_index_usable else "failed"
        app.state.rag_candidate_status = "failed"
        app.state.rag_candidate_error = type(exc).__name__
        app.state.rag_error = type(exc).__name__
        logger.warning("rag_preheat_failed snapshot_id=%s error_type=%s", snapshot_id, type(exc).__name__)
        return None
    finally:
        lock.release()


def ensure_retriever(app: FastAPI) -> HybridRetriever | None:
    """Return only an already-preheated retriever matching the active snapshot."""
    retriever = getattr(app.state, "retriever", None)
    if retriever is None:
        return None
    if getattr(app.state, "rag_snapshot_id", None) != _active_snapshot_id(app):
        return None
    if getattr(app.state, "rag_status", None) not in {"ready", "bm25_only"}:
        return None
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_fingerprint = snapshot.get("rag_docs_fingerprint") if isinstance(snapshot, dict) else None
    active_fingerprint = getattr(app.state, "rag_docs_fingerprint", None)
    if not snapshot_fingerprint or snapshot_fingerprint != active_fingerprint:
        return None
    if getattr(retriever, "docs_fingerprint", None) != snapshot_fingerprint:
        return None
    return retriever


async def preheat_retriever_in_background(
    app: FastAPI,
    *,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> None:
    await asyncio.to_thread(
        preheat_retriever,
        app,
        candidate_snapshot=candidate_snapshot,
        activate_snapshot=activate_snapshot,
    )


def get_live_sample_target(app: FastAPI) -> int:
    """Production answers are always bound to the complete weekly sample."""
    return DAILY_TARGET_BATTLES


def get_live_sample_settings(app: FastAPI, refresh_status: str = "ready") -> dict:
    return {
        "target_battles": get_live_sample_target(app),
        "min_target_battles": DAILY_TARGET_BATTLES,
        "max_target_battles": DAILY_TARGET_BATTLES,
        "refresh_status": getattr(app.state, "live_refresh_status", refresh_status),
        "can_update_target": LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED,
        "cooldown_until": getattr(app.state, "live_cooldown_until", 0.0),
    }


def get_runtime_summary(app: FastAPI) -> dict:
    metrics = getattr(app.state, "runtime_metrics", None)
    if metrics is None:
        return {
            "process_requests": 0,
            "successes": 0,
            "failures": 0,
            "cancelled": 0,
            "rate_limited": 0,
            "process_p95_ms": 0.0,
            "sample_size": 0,
        }
    return metrics.public_summary()


def _refresh_cooldown_seconds(failures: int) -> int:
    return (300, 900, 1800)[min(max(int(failures), 1) - 1, 2)]


def _record_live_refresh_attempt(
    app: FastAPI,
    *,
    status: str,
    snapshot: dict | None = None,
    error: str | None = None,
    finished_at: str | None = None,
) -> None:
    collection_metrics = snapshot.get("collection_metrics", {}) if isinstance(snapshot, dict) else {}
    sample_battles = int(snapshot.get("sample_battles", 0) or 0) if isinstance(snapshot, dict) else 0
    target_battles = (
        int(snapshot.get("target_battles", DAILY_TARGET_BATTLES) or DAILY_TARGET_BATTLES)
        if isinstance(snapshot, dict)
        else DAILY_TARGET_BATTLES
    )
    shortfall_battles = (
        int(snapshot.get("shortfall_battles", max(0, target_battles - sample_battles)) or 0)
        if isinstance(snapshot, dict)
        else DAILY_TARGET_BATTLES
    )
    app.state.live_last_refresh_attempt = {
        "status": status,
        "finished_at": finished_at or datetime.now(timezone.utc).isoformat(),
        "sample_battles": sample_battles,
        "target_battles": target_battles,
        "shortfall_battles": shortfall_battles,
        "collection_metrics": collection_metrics,
        "error": error,
    }
    metrics = getattr(app.state, "runtime_metrics", None)
    if metrics is not None:
        metrics.record_snapshot_collection(collection_metrics)


def _record_live_collection_progress(app: FastAPI, progress: dict) -> None:
    """Publish compact collector progress without invoking parser, RAG, or LLM code."""
    app.state.live_collection_progress = dict(progress)
    logger.info(
        "snapshot_collection_progress usable=%s target=%s players=%s requests=%s rate_limited=%s",
        progress.get("usable_battles"),
        progress.get("target_battles"),
        progress.get("fetched_players"),
        progress.get("request_count"),
        progress.get("rate_limited"),
    )


def get_snapshot_artifact_status(data_dir: Path, snapshot_id: str | None) -> dict:
    """Report compact local artifact readiness without hashing or heavy initialization."""
    def manifest_status(root: str) -> dict:
        if not snapshot_id:
            return {"status": "unavailable", "snapshot_id": None, "counts": {}}
        path = Path(data_dir) / root / snapshot_id / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "unavailable", "snapshot_id": snapshot_id, "counts": {}}
        aligned = isinstance(manifest, dict) and manifest.get("snapshot_id") == snapshot_id
        return {
            "status": "ready" if aligned else "misaligned",
            "snapshot_id": manifest.get("snapshot_id") if isinstance(manifest, dict) else None,
            "counts": manifest.get("counts", {}) if isinstance(manifest, dict) else {},
        }

    review = {"status": "not_imported", "snapshot_id": snapshot_id}
    if snapshot_id:
        report_path = Path(data_dir) / "external_reviews" / snapshot_id / "validation_report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = None
        if isinstance(report, dict):
            review = {
                "status": "validated" if report.get("passed") is True else "rejected",
                "snapshot_id": report.get("snapshot_id"),
                "document_count": report.get("document_count"),
                "activation": report.get("activation"),
            }
    return {
        "audit_export": manifest_status("audit_exports"),
        "structured_stats": manifest_status("structured_stats"),
        "external_review": review,
    }


def get_live_snapshot_status(app: FastAPI) -> dict:
    """Return display-safe provenance for the currently published data snapshot."""
    snapshot = getattr(app.state, "live_snapshot", None)
    refresh_status = getattr(app.state, "live_refresh_status", "unavailable")
    cooldown_remaining = max(0.0, getattr(app.state, "live_cooldown_until", 0.0) - time.monotonic())
    last_refresh_attempt = getattr(app.state, "live_last_refresh_attempt", None)
    rag_alignment = _rag_alignment_state(app)
    if not isinstance(snapshot, dict):
        return {
            "source": "Supercell Official API",
            "source_type": "weekly leaderboard battle-log snapshot",
            "status": refresh_status,
            "snapshot_status": refresh_status,
            "snapshot_id": None,
            "fetched_at": None,
            "published_at": None,
            "sample_battles": 0,
            "target_battles": DAILY_TARGET_BATTLES,
            "shortfall_battles": DAILY_TARGET_BATTLES,
            "collection_scope": None,
            "scope_verified": False,
            "leaderboard": {
                "candidate_limit": SUPERCELL_POL_SEED_PLAYERS,
                "queue_capacity": SUPERCELL_LEADERBOARD_PLAYERS,
                "rank_start": 1,
                "scanned_rank_end": None,
                "ranked_players_returned": 0,
                "sampled_players": 0,
                "failed_players": 0,
            },
            "collection_metrics": {},
            "collection_progress": getattr(app.state, "live_collection_progress", None),
            "special_fields_probe": None,
            "refresh_interval_seconds": int(DAILY_REFRESH_INTERVAL.total_seconds()),
            "retention": {"days": SNAPSHOT_RETENTION_DAYS, "max_complete_snapshots": SNAPSHOT_RETENTION_MAX_COMPLETE},
            "artifacts": get_snapshot_artifact_status(DATA_DIR, None),
            "runtime": get_runtime_summary(app),
            "rag": {
                "status": "not_required" if not SUPERCELL_LIVE_DATA_ENABLED else getattr(app.state, "rag_status", "not_ready"),
                "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
                "document_counts": {},
                "quality": getattr(app.state, "rag_quality_report", None),
                **rag_alignment,
                "validation": _public_rag_validation(getattr(app.state, "rag_document_validation", None)),
                "candidate_status": getattr(app.state, "rag_candidate_status", "not_ready"),
                "candidate_error": getattr(app.state, "rag_candidate_error", None),
                "candidate_validation": _public_rag_validation(getattr(app.state, "rag_candidate_validation", None)),
            },
            "rag_status": "not_required" if not SUPERCELL_LIVE_DATA_ENABLED else getattr(app.state, "rag_status", "not_ready"),
            "data_sources": {
                "schedule": "disabled_clan_war_feature",
                "cards": "not_available",
                "decks": "not_available",
                "rag_documents": "not_available",
            },
            "last_refresh_attempt": last_refresh_attempt,
            "cooldown_remaining_seconds": round(cooldown_remaining, 1),
            "error": getattr(app.state, "live_error", None),
        }

    fetched_players = int(snapshot.get("fetched_players", 0) or 0)
    return {
        "source": "Supercell Official API",
        "source_type": "weekly leaderboard battle-log snapshot",
        "status": refresh_status,
        "snapshot_status": refresh_status,
        "snapshot_id": snapshot.get("snapshot_id"),
        "fetched_at": snapshot.get("fetched_at"),
        "published_at": snapshot.get("published_at"),
        "age_seconds": snapshot_age_seconds(snapshot),
        "sample_battles": snapshot.get("sample_battles", 0),
        "target_battles": snapshot.get("target_battles", DAILY_TARGET_BATTLES),
        "shortfall_battles": snapshot.get("shortfall_battles", DAILY_TARGET_BATTLES),
        "collection_scope": snapshot.get("collection_scope", "legacy_mixed_or_unverified"),
        "scope_verified": is_path_of_legend_snapshot(snapshot),
        "leaderboard": {
            "candidate_limit": snapshot.get("leaderboard_candidate_limit", SUPERCELL_LEADERBOARD_PLAYERS),
            "queue_capacity": snapshot.get("collection_metrics", {}).get(
                "player_queue_capacity", SUPERCELL_LEADERBOARD_PLAYERS
            ),
            "rank_start": snapshot.get("leaderboard_start_rank", 1),
            "scanned_rank_end": snapshot.get("leaderboard_last_scanned_rank", fetched_players or None),
            "ranked_players_returned": snapshot.get("ranked_players", 0),
            "sampled_players": snapshot.get("sampled_players", 0),
            "failed_players": snapshot.get("failed_players", 0),
        },
        "collection_metrics": snapshot.get("collection_metrics", {}),
        "collection_progress": getattr(app.state, "live_collection_progress", None),
        "special_fields_probe": snapshot.get("special_fields_probe"),
        "refresh_interval_seconds": int(DAILY_REFRESH_INTERVAL.total_seconds()),
        "retention": {"days": SNAPSHOT_RETENTION_DAYS, "max_complete_snapshots": SNAPSHOT_RETENTION_MAX_COMPLETE},
        "artifacts": get_snapshot_artifact_status(DATA_DIR, str(snapshot.get("snapshot_id") or "") or None),
        "runtime": get_runtime_summary(app),
        "rag": {
            "status": getattr(app.state, "rag_status", "not_ready"),
            "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
            "document_counts": snapshot.get("rag_document_counts", {}),
            "quality": getattr(app.state, "rag_quality_report", None),
            **rag_alignment,
            "validation": _public_rag_validation(
                getattr(app.state, "rag_document_validation", snapshot.get("rag_document_validation"))
            ),
            "candidate_status": getattr(app.state, "rag_candidate_status", "not_ready"),
            "candidate_error": getattr(app.state, "rag_candidate_error", None),
            "candidate_validation": _public_rag_validation(getattr(app.state, "rag_candidate_validation", None)),
        },
        "rag_status": getattr(app.state, "rag_status", "not_ready"),
        "data_sources": {
            "schedule": "disabled_clan_war_feature",
            "cards": "official_weekly_snapshot",
            "decks": "official_weekly_snapshot",
            "rag_documents": "official_weekly_snapshot",
        },
        "last_refresh_attempt": last_refresh_attempt,
        "cooldown_remaining_seconds": round(cooldown_remaining, 1),
        "error": getattr(app.state, "live_error", None),
    }


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
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_usable = is_complete_daily_snapshot(snapshot)
    snapshot_status = getattr(app.state, "live_refresh_status", "missing")
    rag_status = getattr(app.state, "rag_status", "not_required")
    initialized = bool(getattr(app.state, "initialized", False))
    quota = getattr(app.state, "process_quota", None)
    quota_status = quota.status() if quota is not None else {
        "backend": PROCESS_QUOTA_BACKEND,
        "available": PROCESS_QUOTA_BACKEND == "memory",
    }
    blockers: list[str] = []
    degraded_reasons: list[str] = []
    if not initialized:
        blockers.append("runtime_initializing")
    if not quota_status.get("available", False) and PROCESS_QUOTA_FAIL_MODE == "closed":
        blockers.append("process_quota_unavailable")
    if strict and not model_configured:
        blockers.append("model_api_unconfigured")
    model_provider = get_model_provider_status()
    if strict and model_provider.get("circuit_state") == "open":
        blockers.append("model_provider_circuit_open")
    if strict and not snapshot_usable:
        blockers.append("official_snapshot_unavailable")
    if snapshot_usable and snapshot_status in {"refreshing", "cooldown", "stale"}:
        degraded_reasons.append(f"snapshot_{snapshot_status}")
    if rag_status not in {"ready", "bm25_only", "not_required"}:
        degraded_reasons.append(f"rag_{rag_status}")
    rag_snapshot_id = getattr(app.state, "rag_snapshot_id", None)
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    if snapshot_usable and rag_status in {"ready", "bm25_only"} and snapshot_id != rag_snapshot_id:
        degraded_reasons.append("snapshot_rag_misaligned")
    rag_alignment = _rag_alignment_state(app)
    if snapshot_usable and rag_status in {"ready", "bm25_only"} and not rag_alignment["fingerprint_aligned"]:
        degraded_reasons.append("snapshot_rag_fingerprint_misaligned")

    if blockers:
        status = "unavailable"
        http_status = 503
    elif degraded_reasons:
        status = "degraded"
        http_status = 200
    else:
        status = "ready"
        http_status = 200

    return {
        "status": status,
        "http_status": http_status,
        "initialized": initialized,
        "model_api_configured": model_configured,
        "model_provider": model_provider,
        "quota": quota_status,
        "external_api_required": strict,
        "snapshot_status": snapshot_status,
        "snapshot_usable": snapshot_usable,
        "snapshot_id": snapshot_id,
        "rag_status": rag_status,
        "rag_snapshot_id": rag_snapshot_id,
        "snapshot_rag_aligned": bool(
            isinstance(snapshot, dict)
            and snapshot.get("snapshot_id")
            and snapshot.get("snapshot_id") == getattr(app.state, "rag_snapshot_id", None)
        ),
        "snapshot_rag_fingerprint_aligned": rag_alignment["fingerprint_aligned"],
        "snapshot_docs_fingerprint": rag_alignment["snapshot_docs_fingerprint"],
        "active_rag_docs_fingerprint": rag_alignment["active_docs_fingerprint"],
        "index_docs_fingerprint": rag_alignment["index_docs_fingerprint"],
        "rag_document_validation": _public_rag_validation(getattr(app.state, "rag_document_validation", None)),
        "blockers": blockers,
        "degraded_reasons": degraded_reasons,
    }


def configure_live_sample_target(app: FastAPI, target_battles: int) -> dict:
    raise HTTPException(
        status_code=409,
        detail=f"weekly official sampling is fixed at {DAILY_TARGET_BATTLES} battles",
    )


def _validate_dataset_scope(dataset_scope: str) -> str:
    scope = str(dataset_scope or DEFAULT_DATASET_SCOPE).strip()
    if scope not in DATASET_SCOPES:
        raise StructuredQueryError(
            "INVALID_DATASET_SCOPE",
            "dataset_scope must be one of the published rolling dataset scopes.",
            details={"dataset_scope": scope, "allowed": list(DATASET_SCOPES)},
        )
    return scope


def _active_snapshot_group_manifest(data_dir: Path = DATA_DIR) -> dict | None:
    pointer_path = Path(data_dir) / "active_snapshot_group.json"
    if not pointer_path.is_file():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        group_id = str(pointer.get("snapshot_group_id") or "").strip()
        manifest_path = Path(data_dir) / "snapshot_groups" / group_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise StructuredQueryError(
            "DATASET_SCOPE_NOT_READY",
            "The active rolling snapshot group is incomplete.",
            status_code=503,
        ) from exc
    if (
        not group_id
        or manifest.get("snapshot_group_id") != group_id
        or manifest.get("fully_aligned") is not True
        or not set((manifest.get("datasets") or {}).keys())
        or not set((manifest.get("datasets") or {}).keys()).issubset(set(DATASET_SCOPES))
        or manifest.get("rag_docs_fingerprint") != manifest.get("index_docs_fingerprint")
    ):
        raise StructuredQueryError(
            "DATASET_SCOPE_NOT_READY",
            "The active rolling snapshot group failed alignment validation.",
            status_code=503,
            details={"snapshot_group_id": group_id or None},
        )
    return manifest


def get_dataset_catalog(app: FastAPI) -> dict:
    def scope_parts(scope: str) -> tuple[str, str]:
        prefix = next(
            (candidate for candidate in DATASET_WINDOW_DEFINITIONS if scope.startswith(f"{candidate}_")),
            "",
        )
        return prefix, scope[len(prefix) + 1 :] if prefix else scope

    def display_name(scope: str) -> str:
        window, level = scope_parts(scope)
        window_labels = {
            "7d": "最近7天",
            "d7_14": "7至14天前",
            "d14_21": "14至21天前",
            "d21_28": "21至28天前",
            "d28_35": "28至35天前",
            "35d": "最近35天",
        }
        level_name = "全量" if level == "all" else f"前{level.rsplit('_', 1)[-1]}"
        return f"{window_labels.get(window, window)} · {level_name}"

    def unavailable_dataset(scope: str) -> dict:
        prefix, _ = scope_parts(scope)
        definition = DATASET_WINDOW_DEFINITIONS[prefix]
        return {
            "dataset_scope": scope,
            "name": display_name(scope),
            "window_days": definition["end_offset_days"] - definition["start_offset_days"],
            "window_kind": definition["window_kind"],
            "window_start_offset_days": definition["start_offset_days"],
            "window_end_offset_days": definition["end_offset_days"],
            "rank_limit": int(scope.rsplit("_", 1)[-1]) if "_top_" in scope else None,
            "ready": False,
            "complete_loadout_ready": False,
            "entity_stats_ready": False,
            "delta_ready": False,
        }

    manifest = _active_snapshot_group_manifest(DATA_DIR)
    if manifest is None:
        return {
            "snapshot_group_id": None,
            "default_dataset_scope": DEFAULT_DATASET_SCOPE,
            "datasets": [unavailable_dataset(scope) for scope in DATASET_SCOPES],
        }
    return {
        "snapshot_group_id": manifest["snapshot_group_id"],
        "published_at": manifest.get("published_at"),
        "default_dataset_scope": manifest.get("default_dataset_scope", DEFAULT_DATASET_SCOPE),
        "rag": {
            "status": "ready",
            "document_count": manifest.get("rag_document_count"),
            "fully_aligned": manifest.get("fully_aligned") is True,
        },
        "datasets": [
            (
                {
                    **unavailable_dataset(scope),
                    **manifest["datasets"][scope],
                    "dataset_scope": scope,
                    "name": display_name(scope),
                    "ready": (
                        manifest["datasets"][scope].get("ready") is True
                        if "ready" in manifest["datasets"][scope]
                        else int(manifest["datasets"][scope].get("unique_battles") or 0) > 0
                    ),
                    "complete_loadout_ready": (
                        manifest["datasets"][scope].get("complete_loadout_ready") is True
                        if "complete_loadout_ready" in manifest["datasets"][scope]
                        else int((manifest["datasets"][scope].get("structured_counts") or {}).get("full_loadout_side_records") or 0) > 0
                    ),
                    "entity_stats_ready": manifest["datasets"][scope].get("entity_stats_ready") is True,
                    "delta_ready": manifest["datasets"][scope].get("delta_ready") is True,
                }
                if scope in manifest["datasets"] else unavailable_dataset(scope)
            )
            for scope in DATASET_SCOPES
        ],
    }


def get_structured_repository(
    app: FastAPI,
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
) -> StructuredStatsRepository:
    scope = _validate_dataset_scope(dataset_scope)
    group_manifest = _active_snapshot_group_manifest(DATA_DIR)
    if group_manifest is not None:
        group_id = group_manifest["snapshot_group_id"]
        repositories = getattr(app.state, "structured_group_repositories", None)
        if not isinstance(repositories, dict):
            repositories = {}
            app.state.structured_group_repositories = repositories
        cache_key = (group_id, scope)
        repository = repositories.get(cache_key)
        if not isinstance(repository, StructuredStatsRepository):
            repository = StructuredStatsRepository.for_snapshot_group(DATA_DIR, group_id, scope)
            repositories.clear()
            repositories[cache_key] = repository
        return repository
    if scope != DEFAULT_DATASET_SCOPE:
        raise StructuredQueryError(
            "DATASET_SCOPE_NOT_READY",
            "The requested rolling dataset scope has not been published yet.",
            status_code=503,
            details={"dataset_scope": scope},
        )
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_id = str(snapshot.get("snapshot_id") or "") if isinstance(snapshot, dict) else ""
    if not snapshot_id:
        try:
            pointer = load_json_file(DATA_DIR / "official_snapshot_pointer.json")
            snapshot_id = str(pointer.get("snapshot_id") or "") if isinstance(pointer, dict) else ""
        except (OSError, json.JSONDecodeError):
            snapshot_id = ""
    if not snapshot_id:
        raise StructuredQueryError(
            "STRUCTURED_INDEX_UNAVAILABLE",
            "No active official snapshot is available for structured queries.",
            status_code=503,
        )
    repository = getattr(app.state, "structured_repository", None)
    if not isinstance(repository, StructuredStatsRepository) or repository.snapshot_id != snapshot_id:
        repository = StructuredStatsRepository(DATA_DIR, snapshot_id)
        app.state.structured_repository = repository
    return repository


def ensure_dataset_retriever(app: FastAPI, dataset_scope: str) -> HybridRetriever | None:
    scope = _validate_dataset_scope(dataset_scope)
    manifest = _active_snapshot_group_manifest(DATA_DIR)
    if manifest is None:
        if scope != DEFAULT_DATASET_SCOPE:
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The requested rolling dataset scope has not been published yet.",
                status_code=503,
                details={"dataset_scope": scope},
            )
        return ensure_retriever(app)
    group_id = manifest["snapshot_group_id"]
    retriever = getattr(app.state, "rolling_retriever", None)
    if getattr(app.state, "rolling_retriever_group_id", None) == group_id and retriever is not None:
        return retriever
    lock = getattr(app.state, "rolling_retriever_lock", None)
    if lock is None:
        lock = threading.Lock()
        app.state.rolling_retriever_lock = lock
    with lock:
        retriever = getattr(app.state, "rolling_retriever", None)
        if getattr(app.state, "rolling_retriever_group_id", None) == group_id and retriever is not None:
            return retriever
        group_dir = DATA_DIR / "snapshot_groups" / group_id
        try:
            documents = json.loads((group_dir / "rag_documents.json").read_text(encoding="utf-8"))
            candidate = HybridRetriever(
                documents,
                index_path=group_dir / "qdrant",
                lazy_scope_bm25=True,
                bm25_scope_cache_size=2,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The rolling RAG index could not be loaded.",
                status_code=503,
                details={"snapshot_group_id": group_id, "dataset_scope": scope},
            ) from exc
        expected = manifest.get("rag_docs_fingerprint")
        if not candidate.dense_available or candidate.docs_fingerprint != expected:
            candidate.close()
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The rolling RAG index is not aligned with its documents.",
                status_code=503,
                details={"snapshot_group_id": group_id, "dataset_scope": scope},
            )
        previous = getattr(app.state, "rolling_retriever", None)
        app.state.rolling_retriever = candidate
        app.state.rolling_retriever_group_id = group_id
        if previous is not None and previous is not candidate:
            previous.close()
        return candidate


def restore_published_snapshot(app: FastAPI) -> dict | None:
    """Restore the last complete official dataset before scheduling a refresh."""
    snapshot = (
        load_published_snapshot_summary(DATA_DIR)
        if RUNTIME_ROLE == "collector"
        else load_published_snapshot(DATA_DIR)
    )
    if snapshot is None:
        return None
    app.state.live_snapshot = snapshot
    app.state.rag_document_validation = snapshot.get("rag_document_validation")
    age_seconds = snapshot_age_seconds(snapshot)
    app.state.live_snapshot_at = time.monotonic() - (age_seconds or 0.0)
    app.state.live_snapshot_target_battles = DAILY_TARGET_BATTLES
    app.state.cards_meta_data = list(snapshot.get("cards_meta", []))
    app.state.top_decks_data = list(snapshot.get("top_decks", []))
    app.state.card_deck_stats_data = dict(snapshot.get("card_deck_stats", {}))
    app.state.live_error = None
    app.state.live_refresh_status = "ready" if not snapshot_refresh_due(snapshot) else "stale"
    _record_live_refresh_attempt(
        app,
        status="restored",
        snapshot=snapshot,
        finished_at=snapshot.get("published_at") or snapshot.get("fetched_at"),
    )
    logger.info(
        "restored official weekly snapshot id=%s battles=%s age_seconds=%.1f",
        snapshot.get("snapshot_id"),
        snapshot.get("sample_battles"),
        age_seconds or 0.0,
    )
    return snapshot


def ensure_live_snapshot(app: FastAPI) -> dict | None:
    if not SUPERCELL_LIVE_DATA_ENABLED or not SUPERCELL_API_TOKEN:
        app.state.live_refresh_status = "unavailable" if EXTERNAL_API_REQUIRED else "missing"
        return None

    target_battles = DAILY_TARGET_BATTLES
    if time.monotonic() < getattr(app.state, "live_cooldown_until", 0.0):
        app.state.live_refresh_status = "cooldown"
        return getattr(app.state, "live_snapshot", None)
    cached = getattr(app.state, "live_snapshot", None)
    cached_at = getattr(app.state, "live_snapshot_at", 0.0)
    legacy_scope_refresh = RUNTIME_ROLE == "collector" and not is_path_of_legend_snapshot(cached)
    if cached is not None and not legacy_scope_refresh and not snapshot_refresh_due(cached):
        return cached

    refresh_lock = getattr(app.state, "live_refresh_lock", None)
    if refresh_lock is None:
        refresh_lock = threading.Lock()
        app.state.live_refresh_lock = refresh_lock
    if not refresh_lock.acquire(blocking=False):
        # A background refresh is already running. Serve the last successful
        # official snapshot if one exists; otherwise wait for the first sample.
        if cached is not None:
            return cached
        with refresh_lock:
            return getattr(app.state, "live_snapshot", None)

    try:
        app.state.live_refresh_status = "refreshing"
        app.state.live_collection_progress = {
            "status": "starting",
            "target_battles": target_battles,
            "usable_battles": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if cached is None:
            app.state.rag_status = "not_ready"
        client = SupercellAPIClient(
            SUPERCELL_API_TOKEN,
            timeout_seconds=SUPERCELL_API_TIMEOUT_SECONDS,
            max_retries=SUPERCELL_HIGH_VOLUME_MAX_RETRIES,
            requests_per_second=SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
        )
        snapshot = client.fetch_snapshot(
            target_battles=target_battles,
            player_limit=SUPERCELL_LEADERBOARD_PLAYERS,
            seed_player_limit=SUPERCELL_POL_SEED_PLAYERS,
            battles_per_player=SUPERCELL_BATTLES_PER_PLAYER,
            concurrency=SUPERCELL_FETCH_CONCURRENCY,
            fallback_player_tags=SUPERCELL_FALLBACK_PLAYER_TAGS,
            max_duration_seconds=SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
            progress_callback=lambda progress: _record_live_collection_progress(app, progress),
            progress_interval_seconds=SNAPSHOT_PROGRESS_INTERVAL_SECONDS,
            spool_dir=DATA_DIR / "snapshot_work",
        )
        if not is_complete_daily_snapshot(snapshot):
            app.state.live_error = (
                "IncompleteOfficialSnapshot: "
                f"sample_battles={snapshot.get('sample_battles')} target_battles={target_battles}"
            )
            source_exhausted = bool(snapshot.get("collection_metrics", {}).get("source_exhausted"))
            if source_exhausted:
                app.state.live_refresh_status = "source_exhausted"
                app.state.live_cooldown_until = time.monotonic() + DAILY_REFRESH_INTERVAL.total_seconds()
            else:
                failures = getattr(app.state, "live_refresh_failures", 0) + 1
                app.state.live_refresh_failures = failures
                app.state.live_cooldown_until = time.monotonic() + _refresh_cooldown_seconds(failures)
                app.state.live_refresh_status = "cooldown"
            _record_live_refresh_attempt(
                app,
                status="source_exhausted" if source_exhausted else "incomplete",
                snapshot=snapshot,
                error=app.state.live_error,
            )
            logger.warning("discarded incomplete official weekly snapshot %s", app.state.live_error)
            return cached

        snapshot = publish_daily_snapshot(snapshot, DATA_DIR)
        if RUNTIME_ROLE != "collector" and snapshot.get("raw_battles_storage"):
            snapshot = load_published_snapshot(DATA_DIR)
            if snapshot is None:
                raise ValueError("streamed snapshot publication could not be reloaded")
        if RUNTIME_ROLE == "collector":
            _activate_snapshot_state(app, snapshot)
        else:
            candidate = preheat_retriever(
                app,
                candidate_snapshot=snapshot,
                activate_snapshot=True,
            )
            if candidate is None:
                app.state.live_error = "RAGActivationFailed"
                app.state.live_refresh_status = "stale" if cached is not None else "unavailable"
                _record_live_refresh_attempt(
                    app,
                    status="rag_activation_failed",
                    snapshot=snapshot,
                    error=app.state.live_error,
                )
                return cached
        app.state.live_error = None
        _record_live_refresh_attempt(app, status="success", snapshot=snapshot)
        if snapshot.get("collection_metrics", {}).get("rate_limited", 0):
            failures = getattr(app.state, "live_refresh_failures", 0) + 1
            app.state.live_refresh_failures = failures
            cooldown_seconds = _refresh_cooldown_seconds(failures)
            app.state.live_cooldown_until = time.monotonic() + cooldown_seconds
            app.state.live_refresh_status = "cooldown"
        else:
            app.state.live_refresh_status = "ready"
            app.state.live_refresh_failures = 0
        logger.info(
            "official live snapshot refreshed battles=%s target=%s players=%s failed=%s",
            snapshot.get("sample_battles"),
            snapshot.get("target_battles"),
            snapshot.get("sampled_players"),
            snapshot.get("failed_players"),
        )
        return snapshot
    except Exception as exc:
        # Adapter validation errors are actionable and do not contain credentials. Do not
        # expose remote HTTP response bodies or request details through the client trace.
        if isinstance(exc, ValueError):
            app.state.live_error = f"ValueError: {str(exc)[:240]}"
        else:
            app.state.live_error = type(exc).__name__
        logger.warning("official live snapshot refresh failed: %s", exc)
        failures = getattr(app.state, "live_refresh_failures", 0) + 1
        app.state.live_refresh_failures = failures
        cooldown_seconds = _refresh_cooldown_seconds(failures)
        app.state.live_cooldown_until = time.monotonic() + cooldown_seconds
        app.state.live_refresh_status = "cooldown"
        _record_live_refresh_attempt(app, status="failed", error=app.state.live_error)
        # A prior successful response is still official API data. Preserve it as
        # a clearly stale cache instead of substituting repository JSON.
        return cached
    finally:
        refresh_lock.release()


async def refresh_live_snapshot_loop(app: FastAPI) -> None:
    """Load once, then refresh a complete official dataset every week."""
    while True:
        snapshot = await asyncio.to_thread(ensure_live_snapshot, app)
        snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
        snapshot_fingerprint = snapshot.get("rag_docs_fingerprint") if isinstance(snapshot, dict) else None
        if RUNTIME_ROLE != "collector" and snapshot_id and (
            snapshot_id != getattr(app.state, "rag_snapshot_id", None)
            or snapshot_fingerprint != getattr(app.state, "rag_docs_fingerprint", None)
        ):
            await preheat_retriever_in_background(app)
        if getattr(app.state, "live_refresh_status", None) == "cooldown":
            delay = max(60.0, getattr(app.state, "live_cooldown_until", 0.0) - time.monotonic())
        elif getattr(app.state, "live_refresh_status", None) == "source_exhausted":
            delay = max(3600.0, getattr(app.state, "live_cooldown_until", 0.0) - time.monotonic())
        elif snapshot is None:
            delay = 1800.0
        else:
            age_seconds = snapshot_age_seconds(snapshot) or 0.0
            delay = max(1.0, DAILY_REFRESH_INTERVAL.total_seconds() - age_seconds)
        await asyncio.sleep(delay)


async def follow_published_snapshot_loop(app: FastAPI) -> None:
    """Reload atomically published snapshots without ever contacting Supercell."""
    while True:
        published = await asyncio.to_thread(load_published_snapshot, DATA_DIR)
        published_id = published.get("snapshot_id") if isinstance(published, dict) else None
        published_fingerprint = published.get("rag_docs_fingerprint") if isinstance(published, dict) else None
        if published_id and (
            published_id != _active_snapshot_id(app)
            or published_fingerprint != getattr(app.state, "rag_docs_fingerprint", None)
        ):
            await preheat_retriever_in_background(
                app,
                candidate_snapshot=published,
                activate_snapshot=True,
            )
        await asyncio.sleep(SNAPSHOT_FOLLOWER_POLL_SECONDS)


async def refresh_live_snapshot_once(app: FastAPI) -> None:
    """Refresh after a settings change, retrying once if another refresh held the lock."""
    snapshot = await asyncio.to_thread(ensure_live_snapshot, app)
    if snapshot is None and getattr(app.state, "live_snapshot", None) is None:
        await asyncio.to_thread(ensure_live_snapshot, app)


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
        names = [str(name) for name in parsed.get("card_names", []) if name]
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("compare_metric"), "表现")
        return f"{' 与 '.join(names) or '两张卡牌'}的{metric}比较"
    if intent == "card_rank_lookup_query":
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("metric"), "表现")
        return f"卡牌{metric}第 {parsed.get('rank') or '?'} 名查询"
    if intent == "deck_query":
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
    if rolling_manifest is not None and needs_official_snapshot:
        rolling_repository = get_structured_repository(app, dataset_scope)
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
                else "将执行已验证的结构化查询或 RAG 证据分析。"
            ),
        )

    result = await answer_query(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        card_deck_stats=card_deck_stats_data,
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
        # Buffer model text until grounding validation and presentation
        # normalization finish. Raw model Markdown must never leak to the UI.
        stream_content=False,
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


def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_stream_chunks(text: str, chunk_size: int = 80):
    """将最终文本分成稳定小块，保证不支持 token 流的模型也能渐进显示。"""
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]


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
    app.state.initialized = False
    app.state.runtime_metrics = RuntimeMetrics()
    app.state.recent_answers = RecentAnswerCache(
        max_items=FEEDBACK_CACHE_MAX_ITEMS,
        ttl_seconds=FEEDBACK_CACHE_TTL_SECONDS,
    )
    app.state.feedback_store = FeedbackStore(
        FEEDBACK_DB_FILE,
        max_correction_chars=FEEDBACK_MAX_CORRECTION_CHARS,
        answer_ttl_seconds=FEEDBACK_CACHE_TTL_SECONDS,
    )
    app.state.process_quota = create_process_quota(
        backend=PROCESS_QUOTA_BACKEND,
        max_concurrent=PROCESS_MAX_CONCURRENT,
        requests_per_minute=PROCESS_RATE_LIMIT_PER_MINUTE,
        redis_url=REDIS_URL,
        lease_seconds=PROCESS_QUOTA_LEASE_SECONDS,
        key_prefix=PROCESS_QUOTA_KEY_PREFIX,
        fail_mode=PROCESS_QUOTA_FAIL_MODE,
    )
    await app.state.process_quota.probe()
    app.state.schedule_data = load_json_file(SCHEDULE_FILE)
    app.state.bootstrap_top_decks_data = load_json_file(TOP_DECKS_FILE)
    app.state.bootstrap_cards_meta_data = load_json_file(CARDS_META_FILE)
    # Repository snapshots are only a non-strict fallback. In strict mode the
    # active answer data starts empty and is populated by a complete official
    # weekly snapshot (or restored official snapshot) only.
    app.state.top_decks_data = [] if EXTERNAL_API_REQUIRED else list(app.state.bootstrap_top_decks_data)
    app.state.cards_meta_data = [] if EXTERNAL_API_REQUIRED else list(app.state.bootstrap_cards_meta_data)
    app.state.card_deck_stats_data = {}
    app.state.retriever = None
    app.state.rolling_retriever = None
    app.state.rolling_retriever_group_id = None
    app.state.rolling_retriever_lock = threading.Lock()
    app.state.structured_group_repositories = {}
    app.state.rag_snapshot_id = None
    app.state.rag_docs_fingerprint = None
    app.state.rag_document_validation = None
    app.state.rag_candidate_status = "not_ready"
    app.state.rag_candidate_error = None
    app.state.rag_candidate_validation = None
    app.state.rag_status = "not_required"
    app.state.rag_error = None
    app.state.rag_quality_report = None
    app.state.rag_preheat_lock = threading.Lock()
    app.state.rag_preheat_task = None
    app.state.live_snapshot = None
    app.state.live_snapshot_at = 0.0
    app.state.live_error = None
    app.state.live_sample_target_battles = SUPERCELL_TARGET_BATTLES
    app.state.live_snapshot_target_battles = None
    app.state.live_refresh_lock = threading.Lock()
    app.state.live_refresh_task = None
    app.state.live_refresh_status = "missing"
    app.state.live_battle_log_cache = {}
    app.state.live_cooldown_until = 0.0
    app.state.live_refresh_failures = 0
    app.state.live_last_refresh_attempt = None

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
    try:
        yield
    finally:
        refresh_task = app.state.live_refresh_task
        if refresh_task is not None:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass
        rag_task = app.state.rag_preheat_task
        if rag_task is not None:
            rag_task.cancel()
            try:
                await rag_task
            except asyncio.CancelledError:
                pass
        rolling_retriever = getattr(app.state, "rolling_retriever", None)
        if rolling_retriever is not None:
            rolling_retriever.close()
        quota = getattr(app.state, "process_quota", None)
        if quota is not None:
            await quota.close()


app = FastAPI(title="ClashRoyaleMatchCoordinator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "X-Request-ID", "X-Admin-Key"],
)


@app.middleware("http")
async def runtime_protection_middleware(request: Request, call_next):
    """Attach correlation, metrics, and browser security headers."""
    request_id = getattr(request.state, "request_id", None) or normalize_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    metrics = getattr(app.state, "runtime_metrics", None)
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        if metrics is not None:
            metrics.record_http(route=request.url.path, status_code=500, duration_seconds=time.perf_counter() - started_at)
        raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if metrics is not None:
        metrics.record_http(route=request.url.path, status_code=response.status_code, duration_seconds=time.perf_counter() - started_at)
    return response


# Added after the BaseHTTP middleware so raw ASGI body bytes are bounded before
# Starlette or Pydantic consume them, including requests without Content-Length.
app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=MAX_REQUEST_BODY_BYTES)


@app.exception_handler(StructuredQueryError)
async def structured_query_error_handler(_request: Request, exc: StructuredQueryError):
    return JSONResponse(status_code=exc.status_code, content=exc.response())


@app.get("/health")
async def health():
    quota = getattr(app.state, "process_quota", None)
    return {
        "status": "healthy",
        "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
        "runtime_file": str(Path(__file__).resolve()),
        "runtime_role": RUNTIME_ROLE,
        "live_data_enabled": (
            RUNTIME_ROLE in {"all", "collector"}
            and SUPERCELL_LIVE_DATA_ENABLED
            and bool(SUPERCELL_API_TOKEN)
        ),
        "official_collection_enabled": RUNTIME_ROLE in {"all", "collector"},
        "snapshot_auto_follow_enabled": RUNTIME_ROLE == "api" and SNAPSHOT_AUTO_FOLLOW_ENABLED,
        "external_api_required": EXTERNAL_API_REQUIRED,
        "model_api_configured": bool(os.getenv("OPENAI_API_KEY")),
        "live_sample_target_battles": get_live_sample_target(app),
        "quota": quota.status() if quota is not None else {"backend": PROCESS_QUOTA_BACKEND, "available": False},
    }


@app.get("/ready")
async def ready():
    readiness = get_readiness_status(app)
    payload = {key: value for key, value in readiness.items() if key != "http_status"}
    return JSONResponse(status_code=readiness["http_status"], content=payload)


@app.get("/model/status")
async def model_status():
    """Expose sanitized provider health and detected capabilities."""
    return get_model_provider_status()


@app.get("/metrics")
async def metrics():
    snapshot_status = getattr(app.state, "live_refresh_status", "missing")
    rag_status = getattr(app.state, "rag_status", "not_required")
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    metrics_registry = getattr(app.state, "runtime_metrics", None) or RuntimeMetrics()
    body = metrics_registry.render_prometheus(
        snapshot_status=snapshot_status,
        rag_status=rag_status,
        snapshot_aligned=bool(snapshot_id and snapshot_id == getattr(app.state, "rag_snapshot_id", None)),
    )
    body += render_model_provider_metrics()
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


@app.get("/settings/live-sample")
async def get_live_sample_settings_endpoint():
    return get_live_sample_settings(app)


@app.get("/snapshot/status")
async def get_snapshot_status_endpoint():
    return get_live_snapshot_status(app)


@app.get("/api/datasets")
async def structured_datasets():
    return get_dataset_catalog(app)


@app.get("/api/cards/catalog")
async def structured_card_catalog(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return get_structured_repository(app, dataset_scope).card_catalog()


@app.get("/api/cards/rankings")
async def structured_card_rankings(
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
    sort_by: str = "usage_rate",
):
    if sort_by not in CARD_RANKING_METRICS:
        raise StructuredQueryError(
            "INVALID_CARD_RANKING_METRIC",
            "sort_by must be usage_rate, clean_win_rate, or rating.",
            details={"sort_by": sort_by, "allowed": list(CARD_RANKING_METRICS)},
        )
    return get_structured_repository(app, dataset_scope).card_rankings(sort_by)


@app.get("/api/cards/{card_id}/stats")
async def structured_card_stats(card_id: str, dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return get_structured_repository(app, dataset_scope).card_stats(card_id)


@app.get("/api/entities/catalog")
async def structured_entity_catalog(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return get_structured_repository(app, dataset_scope).entity_catalog()


@app.get("/api/entities/rankings")
async def structured_entity_rankings(
    dataset_scope: str = DEFAULT_DATASET_SCOPE,
    sort_by: str = "usage_rate",
):
    return get_structured_repository(app, dataset_scope).entity_rankings(sort_by)


@app.get("/api/entities/{entity_id}/stats")
async def structured_entity_stats(entity_id: str, dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return get_structured_repository(app, dataset_scope).entity_stats(entity_id)


@app.get("/api/loadouts/catalog")
async def structured_loadout_catalog(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return get_structured_repository(app, dataset_scope).loadout_catalog()


@app.post("/api/cards/compare")
async def structured_card_compare(payload: CardCompareRequest):
    return get_structured_repository(app, payload.dataset_scope).compare_cards(payload.card_ids)


@app.post("/api/entities/compare")
async def structured_entity_compare(payload: EntityCompareRequest):
    return get_structured_repository(app, payload.dataset_scope).compare_entities(payload.entity_ids)


@app.post("/api/decks/profile")
async def structured_deck_profile(payload: DeckProfileRequest):
    repository = get_structured_repository(app, payload.dataset_scope)
    if payload.deck_mode == "full_loadout":
        return repository.full_loadout_profile(_loadout_request_payload(payload.loadout))
    return repository.deck_profile(payload.cards or [])


@app.post("/api/decks/matchup")
async def structured_deck_matchup(payload: DeckMatchupRequest):
    repository = get_structured_repository(app, payload.dataset_scope)
    if payload.deck_mode == "full_loadout":
        return repository.full_loadout_matchup(
            _loadout_request_payload(payload.loadout_a),
            _loadout_request_payload(payload.loadout_b),
        )
    return repository.deck_matchup(payload.deck_a or [], payload.deck_b or [])


@app.get("/api/meta/archetypes")
async def structured_archetypes(dataset_scope: str = DEFAULT_DATASET_SCOPE):
    return get_structured_repository(app, dataset_scope).archetypes()


@app.post("/feedback")
async def submit_feedback(payload: FeedbackRequest):
    cache = getattr(app.state, "recent_answers", None)
    store = getattr(app.state, "feedback_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="feedback service is initializing")
    try:
        answer = (cache.get(payload.request_id) if cache is not None else None) or store.get_answer(payload.request_id)
        record = store.submit(
            answer=answer,
            rating=payload.rating,
            correction=payload.correction,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "recorded", **record}


@app.get("/feedback/stats")
async def feedback_stats():
    store = getattr(app.state, "feedback_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="feedback service is initializing")
    return store.stats()


@app.put("/settings/live-sample")
async def update_live_sample_settings(request: LiveSampleSettingsRequest, x_admin_key: str | None = Header(default=None)):
    if not LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED:
        raise HTTPException(status_code=403, detail="live sample target updates are restricted to administrators")
    if not authorize_admin(ADMIN_API_KEY, x_admin_key):
        raise HTTPException(status_code=401 if ADMIN_API_KEY else 403, detail="administrator credentials required")
    settings = configure_live_sample_target(app, request.target_battles)
    asyncio.create_task(refresh_live_snapshot_once(app))
    return settings


@app.post("/process")
async def process(request: Request, payload: ProcessRequest | None = None):
    # Unit tests and local harnesses historically called this endpoint function
    # directly with ProcessRequest. Keep that narrow compatibility path while
    # FastAPI continues to inject Request plus a validated JSON payload.
    request_object = request if isinstance(request, Request) else None
    if payload is None:
        payload = request
    dataset_scope = _validate_dataset_scope(payload.dataset_scope)
    active_group = _active_snapshot_group_manifest(DATA_DIR)
    if active_group is None and dataset_scope != DEFAULT_DATASET_SCOPE:
        raise StructuredQueryError(
            "DATASET_SCOPE_NOT_READY",
            "The requested rolling dataset scope has not been published yet.",
            status_code=503,
            details={"dataset_scope": dataset_scope},
        )
    user_text = get_user_text(payload)
    if not user_text:
        raise HTTPException(status_code=422, detail="a non-empty user question is required")
    if len(user_text) > MAX_QUERY_CHARS:
        raise HTTPException(status_code=413, detail=f"user question exceeds {MAX_QUERY_CHARS} characters")

    incoming_request_id = request_object.headers.get("X-Request-ID") if request_object is not None else None
    request_id = (
        getattr(request_object.state, "request_id", normalize_request_id(incoming_request_id))
        if request_object is not None
        else normalize_request_id(None)
    )
    client_id = (
        resolve_client_id(
            request_object.client.host if request_object.client is not None else None,
            request_object.headers.get("X-Forwarded-For"),
            trust_proxy_headers=TRUST_PROXY_HEADERS,
        )
        if request_object is not None
        else "local-test"
    )
    metrics = getattr(app.state, "runtime_metrics", None)
    if metrics is None:
        metrics = RuntimeMetrics()
        app.state.runtime_metrics = metrics
    quota = getattr(app.state, "process_quota", None)
    if quota is None:
        quota = create_process_quota(
            backend=PROCESS_QUOTA_BACKEND,
            max_concurrent=PROCESS_MAX_CONCURRENT,
            requests_per_minute=PROCESS_RATE_LIMIT_PER_MINUTE,
            redis_url=REDIS_URL,
            lease_seconds=PROCESS_QUOTA_LEASE_SECONDS,
            key_prefix=PROCESS_QUOTA_KEY_PREFIX,
            fail_mode=PROCESS_QUOTA_FAIL_MODE,
        )
        app.state.process_quota = quota
    decision = await quota.try_acquire(client_id)
    if not decision.allowed:
        backend_unavailable = decision.reason == "quota_backend_unavailable"
        metrics.record_process(
            outcome="failure" if backend_unavailable else "rate_limited",
            total_seconds=0.0,
        )
        raise HTTPException(
            status_code=503 if backend_unavailable else 429,
            detail=(
                "process quota backend is unavailable"
                if backend_unavailable
                else "process request rate or concurrency limit exceeded"
            ),
            headers={"Retry-After": str(decision.retry_after_seconds or 1)},
        )

    logger.info("request received request_id=%s query_chars=%s", request_id, len(user_text))

    response_id = f"resp-{uuid.uuid4().hex}"
    message_id = f"msg-{uuid.uuid4().hex}"
    started_at = time.perf_counter()
    first_execution_at: float | None = None
    first_content_at: float | None = None
    answer_result: AnswerResult | None = None
    outcome = "failure"
    answer_task_holder: list[asyncio.Task] = []

    def encode(event: dict) -> str:
        return sse_data({"request_id": request_id, **event})

    async def _event_stream():
        nonlocal first_execution_at, first_content_at, answer_result
        yield encode(
            {
                "object": "response",
                "id": response_id,
                "status": "in_progress",
                "session_id": payload.session_id,
            }
        )
        yield encode(
            {
                "object": "message",
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
            }
        )
        yield encode(
            {
                "object": "progress",
                "status": "in_progress",
                "stage": "parse",
                "label": "正在解析问题并选择执行路径...",
            }
        )

        event_sink = RuntimeEventEmitter(
            request_id=request_id,
            question=user_text,
            attributes={
                "snapshot_group_id": active_group.get("snapshot_group_id") if active_group else None,
                "snapshot_id": (
                    active_group.get("datasets", {}).get(dataset_scope, {}).get("snapshot_id")
                    if active_group else None
                ),
                "dataset_scope": dataset_scope,
                "deck_mode": payload.deck_mode,
                "entity_mode": payload.entity_mode,
                "model": OPENAI_MODEL,
            },
        )
        answer_kwargs = {
            "event_sink": event_sink,
            "request_id": request_id,
        }
        if dataset_scope != DEFAULT_DATASET_SCOPE:
            answer_kwargs["dataset_scope"] = dataset_scope
        if payload.deck_mode != "base8":
            answer_kwargs["deck_mode"] = payload.deck_mode
        if payload.entity_mode != "base8":
            answer_kwargs["entity_mode"] = payload.entity_mode
        if payload.intent_hint is not None:
            answer_kwargs["intent_hint"] = payload.intent_hint
        answer_task = asyncio.create_task(build_answer(user_text, app, **answer_kwargs))
        answer_task_holder.append(answer_task)
        stages = [
            ("route", "正在确定结构化查询或 RAG 路径..."),
            ("retrieve", "正在检索本地知识库与证据来源..."),
            ("synthesize", "正在调用模型生成可追溯回答..."),
        ]
        stage_index = 0
        while not answer_task.done() or not event_sink.empty():
            try:
                event = await asyncio.wait_for(event_sink.next_event(), timeout=0.7)
                if event.get("object") == "execution" and first_execution_at is None:
                    first_execution_at = time.perf_counter()
                if event.get("object") == "content" and first_content_at is None:
                    first_content_at = time.perf_counter()
                yield encode(event)
            except asyncio.TimeoutError:
                if answer_task.done():
                    continue
                stage, label = stages[min(stage_index, len(stages) - 1)]
                yield encode(
                    {
                        "object": "progress",
                        "status": "in_progress",
                        "stage": stage,
                        "label": label,
                    }
                )
                stage_index += 1

        try:
            answer_result = answer_task.result()
        except Exception as exc:
            logger.error(
                "answer generation failed type=%s detail=%s",
                type(exc).__name__,
                str(exc)[:500],
                exc_info=True,
            )
            yield encode(
                {
                    "object": "error",
                    "status": "failed",
                    "message": "生成回答失败，请检查后端日志、模型配置和检索服务。",
                }
            )
            yield encode(
                {
                    "object": "response",
                    "id": response_id,
                    "status": "failed",
                }
            )
            return

        answer_result.metadata["request_id"] = request_id
        answer_text = answer_result.answer
        recent_answers = getattr(app.state, "recent_answers", None)
        feedback_store = getattr(app.state, "feedback_store", None)
        answer_record = None
        if recent_answers is not None:
            snapshot = getattr(app.state, "live_snapshot", None)
            answer_record = {
                "request_id": request_id,
                "question": user_text,
                "answer": answer_text,
                "snapshot_id": snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None,
                "parsed": answer_result.parsed,
                "selected_skill": answer_result.selected_skill,
            }
            recent_answers.put(**answer_record)
        if feedback_store is not None and answer_record is not None:
            feedback_store.register_answer(answer_record)
        if event_sink.content_count == 0:
            yield encode(
                {
                    "object": "progress",
                    "status": "in_progress",
                    "stage": "stream",
                    "label": "正在逐段输出回答...",
                }
            )
            chunks = list(split_stream_chunks(answer_text))
            for index, chunk in enumerate(chunks):
                if first_content_at is None:
                    first_content_at = time.perf_counter()
                yield encode(
                    {
                        "object": "content",
                        "type": "text",
                        "status": "in_progress",
                        "msg_id": message_id,
                        "text": chunk,
                        "delta": True,
                    }
                )
                if index < len(chunks) - 1:
                    await asyncio.sleep(SEMANTIC_CONTENT_INTERVAL_SECONDS)
        trace_events = read_trace(answer_result.trace_id)
        yield encode(
            {
                "object": "trace",
                "status": "completed",
                "trace_id": answer_result.trace_id,
                "parsed": answer_result.parsed,
                "plan": answer_result.plan,
                "selected_skill": answer_result.selected_skill,
                "mode": answer_result.mode,
                "metadata": redact_for_client(answer_result.metadata),
                "sub_results": redact_for_client(answer_result.sub_results),
                "events": redact_for_client(trace_events),
            }
        )
        yield encode(
            {
                "object": "message",
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "text",
                        "text": answer_text,
                    }
                ],
            }
        )
        yield encode(
            {
                "object": "response",
                "id": response_id,
                "status": "completed",
                "output": [
                    {
                        "object": "message",
                        "id": message_id,
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": answer_text,
                            }
                        ],
                    }
                ],
            }
        )

    async def event_stream():
        nonlocal outcome
        completed = False
        try:
            async for event in _event_stream():
                yield event
            completed = True
            if answer_result is not None:
                outcome = "success"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            unfinished_tasks = [task for task in answer_task_holder if not task.done()]
            if not completed:
                outcome = "cancelled"
            for task in unfinished_tasks:
                task.cancel()
            finished_at = time.perf_counter()
            if answer_result is not None:
                live_metadata = answer_result.metadata.get("live_data", {})
                if isinstance(live_metadata, dict):
                    metrics.record_snapshot_collection(live_metadata.get("collection_metrics"))
                metrics.record_model_stream(
                    answer_result.metadata.get("model_stream"),
                    first_content_seconds=(first_content_at - started_at) if first_content_at else None,
                    total_seconds=finished_at - started_at,
                )
                record_model_stream_mode(answer_result.metadata.get("model_stream"))
            metrics.record_process(
                outcome=outcome,
                total_seconds=finished_at - started_at,
                first_execution_seconds=(first_execution_at - started_at) if first_execution_at else None,
                first_content_seconds=(first_content_at - started_at) if first_content_at else None,
            )
            await quota.release(decision.lease_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Request-ID": request_id,
        },
    )


if __name__ == "__main__":
    configure_logging()
    import uvicorn

    # Pass the in-memory application so Windows does not spawn a second interpreter
    # which may resolve a different runtime_multi module from its import path.
    uvicorn.run(app, host=RUNTIME_HOST, port=RUNTIME_PORT, reload=False)
