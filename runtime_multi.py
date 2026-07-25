import json
import logging
import os
import asyncio
import time
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from app_config import (
    DATA_DIR,
    CARDS_META_FILE,
    RUNTIME_HOST,
    RUNTIME_PORT,
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
    SUPERCELL_LEADERBOARD_PLAYERS,
    SUPERCELL_BATTLES_PER_PLAYER,
    SUPERCELL_FETCH_CONCURRENCY,
    SUPERCELL_FALLBACK_PLAYER_TAGS,
    SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
    SUPERCELL_HIGH_VOLUME_MAX_RETRIES,
    SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
    LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED,
)
from hybrid_retriever import HybridRetriever, load_docs
from model_gateway import generate_model_text
from runtime_events import RuntimeEventEmitter
from supercell_live import SupercellAPIClient
from snapshot_store import (
    DAILY_REFRESH_INTERVAL,
    DAILY_TARGET_BATTLES,
    is_complete_daily_snapshot,
    load_published_snapshot,
    publish_daily_snapshot,
    snapshot_age_seconds,
    snapshot_refresh_due,
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
    input: list[dict]


class LiveSampleSettingsRequest(BaseModel):
    target_battles: int


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
        return local_parsed

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
        logger.debug("parser raw output=%s", parse_text)

        parsed = extract_json_block(parse_text)
        if parsed is None:
            logger.warning("parser returned non-json output, using fallback parser")
            return merge_parse_metadata(
                local_parsed,
                build_parse_metadata(
                    parse_source=local_parsed.get("parse_source", "local_rule"),
                    parse_confidence=local_parsed.get("parse_confidence", LOCAL_PARSE_CONFIDENCE_LOW),
                    parse_reason="llm parser returned non-json output; kept local parse",
                ),
            )

        normalized = normalize_multi_intent_query(parsed, user_text, cards_meta_data)
        local_intents = [item.get("intent") for item in local_parsed.get("subqueries", [])]
        normalized_intents = [item.get("intent") for item in normalized.get("subqueries", [])]
        if local_parsed.get("intent") == "multi_intent" and (
            normalized.get("intent") != "multi_intent" or normalized_intents != local_intents
        ):
            return merge_parse_metadata(
                local_parsed,
                build_parse_metadata(
                    parse_source="llm_parser",
                    parse_confidence=LOCAL_PARSE_CONFIDENCE_HIGH,
                    parse_reason=(
                        "gpt-5.5 parser output was reconciled to the high-confidence "
                        "local multi-intent decomposition"
                    ),
                ),
            )
        if normalized.get("intent") == "reject" and local_parsed.get("intent") != "reject":
            # The model remains the primary parser, but a validated local card
            # alias must not be discarded merely because the model rejected it.
            return merge_parse_metadata(
                local_parsed,
                build_parse_metadata(
                    parse_source="local_rule",
                    parse_confidence=local_parsed.get("parse_confidence", LOCAL_PARSE_CONFIDENCE_HIGH),
                    parse_reason="llm parser rejected a high-confidence local parse; kept local parse",
                ),
            )
        return merge_parse_metadata(
            normalized,
            build_parse_metadata(
                parse_source="llm_parser",
                parse_confidence=LOCAL_PARSE_CONFIDENCE_HIGH,
                parse_reason="gpt-5.5 structured parser output validated locally",
            ),
        )
    except Exception as exc:
        logger.warning("parser agent failed, using fallback parser: %s", exc)
        return merge_parse_metadata(
            local_parsed,
            build_parse_metadata(
                parse_source=local_parsed.get("parse_source", "local_rule"),
                parse_confidence=local_parsed.get("parse_confidence", LOCAL_PARSE_CONFIDENCE_LOW),
                parse_reason=f"llm parser failed; kept local parse: {exc}",
            ),
        )


def query_needs_rag(parsed: dict) -> bool:
    if parsed.get("intent") == "multi_intent":
        return any(query_needs_rag(subquery) for subquery in parsed.get("subqueries", []))
    intent = parsed.get("intent")
    if intent in {"meta_analysis_query", "match_preparation_query"}:
        return True
    if intent == "deck_query":
        return (
            parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    if intent == "card_query":
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


def preheat_retriever(app: FastAPI) -> HybridRetriever | None:
    """Build a new snapshot index before atomically activating it for requests."""
    snapshot_id = _active_snapshot_id(app)
    if not snapshot_id:
        app.state.rag_status = "not_ready"
        return None

    existing = getattr(app.state, "retriever", None)
    if getattr(app.state, "rag_snapshot_id", None) == snapshot_id and existing is not None:
        return existing

    lock = getattr(app.state, "rag_preheat_lock", None)
    if lock is None:
        lock = threading.Lock()
        app.state.rag_preheat_lock = lock
    if not lock.acquire(blocking=False):
        return None

    try:
        app.state.rag_status = "building"
        app.state.rag_error = None
        rag_docs = load_docs()
        if not rag_docs or any(doc.get("metadata", {}).get("snapshot_id") != snapshot_id for doc in rag_docs):
            raise ValueError("RAG documents do not match the active official daily snapshot")
        candidate = HybridRetriever(rag_docs)
        if candidate.snapshot_id != snapshot_id:
            raise ValueError("built retriever does not match the active official daily snapshot")
        if _active_snapshot_id(app) != snapshot_id:
            # A newer snapshot was published while embedding. Do not replace it
            # with a retriever built for an older evidence boundary.
            app.state.rag_status = "not_ready"
            return None

        app.state.retriever = candidate
        app.state.rag_snapshot_id = snapshot_id
        app.state.rag_status = "ready" if candidate.dense_available else "bm25_only"
        logger.info(
            "rag_preheat_complete snapshot_id=%s documents=%s mode=%s",
            snapshot_id,
            len(rag_docs),
            app.state.rag_status,
        )
        return candidate
    except Exception as exc:
        # Keep an old retriever in memory for rollback, but never use it for a
        # newer snapshot because the evidence boundary would be wrong.
        app.state.rag_status = "failed"
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
    return retriever


async def preheat_retriever_in_background(app: FastAPI) -> None:
    await asyncio.to_thread(preheat_retriever, app)


def get_live_sample_target(app: FastAPI) -> int:
    """Production answers are always bound to the complete daily sample."""
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


def get_live_snapshot_status(app: FastAPI) -> dict:
    """Return display-safe provenance for the currently published data snapshot."""
    snapshot = getattr(app.state, "live_snapshot", None)
    refresh_status = getattr(app.state, "live_refresh_status", "unavailable")
    if not isinstance(snapshot, dict):
        return {
            "source": "Supercell Official API",
            "source_type": "daily leaderboard battle-log snapshot",
            "status": refresh_status,
            "snapshot_status": refresh_status,
            "snapshot_id": None,
            "fetched_at": None,
            "published_at": None,
            "sample_battles": 0,
            "target_battles": DAILY_TARGET_BATTLES,
            "shortfall_battles": DAILY_TARGET_BATTLES,
            "leaderboard": {
                "candidate_limit": SUPERCELL_LEADERBOARD_PLAYERS,
                "rank_start": 1,
                "scanned_rank_end": None,
                "ranked_players_returned": 0,
                "sampled_players": 0,
                "failed_players": 0,
            },
            "collection_metrics": {},
            "rag": {
                "status": "not_required" if not SUPERCELL_LIVE_DATA_ENABLED else getattr(app.state, "rag_status", "not_ready"),
                "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
                "document_counts": {},
            },
            "rag_status": "not_required" if not SUPERCELL_LIVE_DATA_ENABLED else getattr(app.state, "rag_status", "not_ready"),
            "data_sources": {
                "schedule": "local_schedule_json",
                "cards": "not_available",
                "decks": "not_available",
                "rag_documents": "not_available",
            },
            "error": getattr(app.state, "live_error", None),
        }

    fetched_players = int(snapshot.get("fetched_players", 0) or 0)
    return {
        "source": "Supercell Official API",
        "source_type": "daily leaderboard battle-log snapshot",
        "status": refresh_status,
        "snapshot_status": refresh_status,
        "snapshot_id": snapshot.get("snapshot_id"),
        "fetched_at": snapshot.get("fetched_at"),
        "published_at": snapshot.get("published_at"),
        "age_seconds": snapshot_age_seconds(snapshot),
        "sample_battles": snapshot.get("sample_battles", 0),
        "target_battles": snapshot.get("target_battles", DAILY_TARGET_BATTLES),
        "shortfall_battles": snapshot.get("shortfall_battles", DAILY_TARGET_BATTLES),
        "leaderboard": {
            "candidate_limit": snapshot.get("leaderboard_candidate_limit", SUPERCELL_LEADERBOARD_PLAYERS),
            "rank_start": snapshot.get("leaderboard_start_rank", 1),
            "scanned_rank_end": snapshot.get("leaderboard_last_scanned_rank", fetched_players or None),
            "ranked_players_returned": snapshot.get("ranked_players", 0),
            "sampled_players": snapshot.get("sampled_players", 0),
            "failed_players": snapshot.get("failed_players", 0),
        },
        "collection_metrics": snapshot.get("collection_metrics", {}),
        "rag": {
            "status": getattr(app.state, "rag_status", "not_ready"),
            "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
            "document_counts": snapshot.get("rag_document_counts", {}),
        },
        "rag_status": getattr(app.state, "rag_status", "not_ready"),
        "data_sources": {
            "schedule": "local_schedule_json",
            "cards": "official_daily_snapshot",
            "decks": "official_daily_snapshot",
            "rag_documents": "official_daily_snapshot",
        },
        "error": getattr(app.state, "live_error", None),
    }


def configure_live_sample_target(app: FastAPI, target_battles: int) -> dict:
    raise HTTPException(
        status_code=409,
        detail=f"daily official sampling is fixed at {DAILY_TARGET_BATTLES} battles",
    )


def restore_published_snapshot(app: FastAPI) -> dict | None:
    """Restore the last complete official dataset before scheduling a refresh."""
    snapshot = load_published_snapshot(DATA_DIR)
    if snapshot is None:
        return None
    app.state.live_snapshot = snapshot
    age_seconds = snapshot_age_seconds(snapshot)
    app.state.live_snapshot_at = time.monotonic() - (age_seconds or 0.0)
    app.state.live_snapshot_target_battles = DAILY_TARGET_BATTLES
    app.state.cards_meta_data = list(snapshot.get("cards_meta", []))
    app.state.top_decks_data = list(snapshot.get("top_decks", []))
    app.state.card_deck_stats_data = dict(snapshot.get("card_deck_stats", {}))
    app.state.live_error = None
    app.state.live_refresh_status = "ready" if not snapshot_refresh_due(snapshot) else "stale"
    logger.info(
        "restored official daily snapshot id=%s battles=%s age_seconds=%.1f",
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
    if cached is not None and not snapshot_refresh_due(cached):
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
            battles_per_player=SUPERCELL_BATTLES_PER_PLAYER,
            concurrency=SUPERCELL_FETCH_CONCURRENCY,
            fallback_player_tags=SUPERCELL_FALLBACK_PLAYER_TAGS,
            max_duration_seconds=SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
        )
        if not is_complete_daily_snapshot(snapshot):
            app.state.live_error = (
                "IncompleteOfficialSnapshot: "
                f"sample_battles={snapshot.get('sample_battles')} target_battles={target_battles}"
            )
            app.state.live_refresh_status = "stale" if cached is not None else "unavailable"
            logger.warning("discarded incomplete official daily snapshot %s", app.state.live_error)
            return cached

        snapshot = publish_daily_snapshot(snapshot, DATA_DIR)
        app.state.live_snapshot = snapshot
        app.state.live_snapshot_at = time.monotonic()
        app.state.live_snapshot_target_battles = target_battles
        app.state.cards_meta_data = list(snapshot.get("cards_meta", []))
        app.state.top_decks_data = list(snapshot.get("top_decks", []))
        app.state.card_deck_stats_data = dict(snapshot.get("card_deck_stats", {}))
        app.state.retriever = None
        app.state.rag_status = "not_ready"
        app.state.rag_error = None
        app.state.live_error = None
        if snapshot.get("collection_metrics", {}).get("rate_limited", 0):
            failures = getattr(app.state, "live_refresh_failures", 0) + 1
            app.state.live_refresh_failures = failures
            cooldown_seconds = (300, 900, 1800)[min(failures - 1, 2)]
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
        cooldown_seconds = (300, 900, 1800)[min(failures - 1, 2)]
        app.state.live_cooldown_until = time.monotonic() + cooldown_seconds
        app.state.live_refresh_status = "cooldown"
        # A prior successful response is still official API data. Preserve it as
        # a clearly stale cache instead of substituting repository JSON.
        return cached
    finally:
        refresh_lock.release()


async def refresh_live_snapshot_loop(app: FastAPI) -> None:
    """Load once, then refresh a complete official dataset every 24 hours."""
    while True:
        snapshot = await asyncio.to_thread(ensure_live_snapshot, app)
        snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
        if snapshot_id and snapshot_id != getattr(app.state, "rag_snapshot_id", None):
            await preheat_retriever_in_background(app)
        if getattr(app.state, "live_refresh_status", None) == "cooldown":
            delay = max(60.0, getattr(app.state, "live_cooldown_until", 0.0) - time.monotonic())
        elif snapshot is None:
            delay = 1800.0
        else:
            age_seconds = snapshot_age_seconds(snapshot) or 0.0
            delay = max(1.0, DAILY_REFRESH_INTERVAL.total_seconds() - age_seconds)
        await asyncio.sleep(delay)


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
    """Return whether a parsed request needs the official daily game snapshot.

    Schedule data is maintained locally and is the sole intentional exception.
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
    return intent not in {"schedule_query", "reject"}


def build_external_api_unavailable_result(parsed: dict, message: str, live_metadata: dict) -> AnswerResult:
    """Return an explicit failure instead of treating a snapshot as live data."""
    return AnswerResult(
        answer=message,
        trace_id=None,
        parsed=parsed,
        plan=None,
        selected_skill=None,
        mode="unavailable",
        metadata={"external_api_required": True, "live_data": live_metadata},
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
            "net_win_rate": "净胜率",
        }
        metrics = "、".join(metric_labels.get(metric, str(metric)) for metric in metric_values)
        return f"{card} 的{metrics or '数据'}查询"
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
) -> AnswerResult:
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
            detail="使用模型 API 识别可执行意图，不展示内部推理。",
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

    parsed = await parse_user_query(user_text, bootstrap_cards_meta_data, api_key)
    logger.info("request parsed intent=%s parsed=%s", parsed.get("intent"), parsed)
    if event_sink is not None:
        await event_sink.execution(
            step_id="parse",
            phase="parse",
            status="completed",
            title="已解析问题",
            detail=describe_parsed_request(parsed),
        )
    parser_api = {
        "status": "api" if parsed.get("parse_source") == "llm_parser" else "fallback",
        "parse_source": parsed.get("parse_source"),
        "model": OPENAI_MODEL,
    }
    if EXTERNAL_API_REQUIRED and parser_api["status"] != "api":
        unavailable = build_external_api_unavailable_result(
            parsed,
            "Model parser did not return a validated API result. Strict external API mode will not use local parsing as a substitute.",
            {"status": "not_checked"},
        )
        unavailable.metadata["parser_api"] = {**parser_api, "status": "unavailable"}
        return unavailable

    needs_official_snapshot = query_requires_official_snapshot(parsed)
    data_context = {
        "schedule": "local_schedule_json",
        "cards": "not_used" if not needs_official_snapshot else "not_loaded",
        "decks": "not_used" if not needs_official_snapshot else "not_loaded",
        "rag_documents": "not_used" if not query_needs_rag(parsed) else "not_loaded",
        "snapshot_id": None,
    }
    live_metadata = {"status": "not_required" if not needs_official_snapshot else "disabled"}
    if SUPERCELL_LIVE_DATA_ENABLED and SUPERCELL_API_TOKEN and (needs_official_snapshot or not EXTERNAL_API_REQUIRED):
        if event_sink is not None:
            await event_sink.execution(
                step_id="snapshot",
                phase="data",
                status="running",
                title="正在确认官方数据快照",
                detail="读取当前完整 Supercell 官方排行榜战斗日志快照。",
            )
        live_snapshot = await asyncio.to_thread(ensure_live_snapshot, app)
        if live_snapshot is not None:
            if EXTERNAL_API_REQUIRED:
                cards_meta_data = list(live_snapshot["cards_meta"])
            else:
                cards_meta_data = merge_live_card_snapshot(live_snapshot["cards_meta"], cards_meta_data)
            top_decks_data = live_snapshot["top_decks"]
            card_deck_stats_data = dict(live_snapshot.get("card_deck_stats", {}))
            data_context.update(
                {
                    "cards": "official_daily_snapshot",
                    "decks": "official_daily_snapshot",
                    "rag_documents": "official_daily_snapshot" if query_needs_rag(parsed) else "not_used",
                    "snapshot_id": live_snapshot.get("snapshot_id"),
                }
            )
            live_metadata = {
                "status": "live_sample",
                "source": "supercell_api",
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

    retriever = ensure_retriever(app)
    rag_metadata = {
        "status": getattr(app.state, "rag_status", "not_required"),
        "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
    }
    if query_needs_rag(parsed):
        # RAG indexing is preheated on startup and snapshot publication. User
        # requests only read an already activated index and never embed docs.
        retriever = ensure_retriever(app)

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
            "rag_status": rag_metadata["status"],
            "rag_snapshot_id": rag_metadata["snapshot_id"],
            "data_context": data_context,
        },
        event_sink=event_sink,
        stream_content=parsed.get("intent") != "multi_intent",
    )
    assert isinstance(result, AnswerResult)
    result.metadata["live_data"] = live_metadata
    result.metadata["parser_api"] = parser_api
    result.metadata["rag"] = rag_metadata
    result.metadata["data_context"] = data_context
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
    app.state.schedule_data = load_json_file(SCHEDULE_FILE)
    app.state.bootstrap_top_decks_data = load_json_file(TOP_DECKS_FILE)
    app.state.bootstrap_cards_meta_data = load_json_file(CARDS_META_FILE)
    # Repository snapshots are only a non-strict fallback. In strict mode the
    # active answer data starts empty and is populated by a complete official
    # daily snapshot (or restored official snapshot) only.
    app.state.top_decks_data = [] if EXTERNAL_API_REQUIRED else list(app.state.bootstrap_top_decks_data)
    app.state.cards_meta_data = [] if EXTERNAL_API_REQUIRED else list(app.state.bootstrap_cards_meta_data)
    app.state.card_deck_stats_data = {}
    app.state.retriever = None
    app.state.rag_snapshot_id = None
    app.state.rag_status = "not_required"
    app.state.rag_error = None
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

    logger.info(
        "startup complete schedule=%s decks=%s cards=%s retriever=lazy",
        len(app.state.schedule_data),
        len(app.state.top_decks_data),
        len(app.state.cards_meta_data),
    )
    if SUPERCELL_LIVE_DATA_ENABLED and SUPERCELL_API_TOKEN:
        app.state.rag_status = "not_ready"
        restore_published_snapshot(app)
        if getattr(app.state, "live_snapshot", None) is not None:
            app.state.rag_status = "not_ready"
            app.state.rag_preheat_task = asyncio.create_task(preheat_retriever_in_background(app))
        app.state.live_refresh_task = asyncio.create_task(refresh_live_snapshot_loop(app))
    elif EXTERNAL_API_REQUIRED:
        app.state.live_refresh_status = "unavailable"
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


app = FastAPI(title="ClashRoyaleMatchCoordinator", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
        "runtime_file": str(Path(__file__).resolve()),
        "live_data_enabled": SUPERCELL_LIVE_DATA_ENABLED and bool(SUPERCELL_API_TOKEN),
        "external_api_required": EXTERNAL_API_REQUIRED,
        "model_api_configured": bool(os.getenv("OPENAI_API_KEY")),
        "live_sample_target_battles": get_live_sample_target(app),
    }


@app.get("/settings/live-sample")
async def get_live_sample_settings_endpoint():
    return get_live_sample_settings(app)


@app.get("/snapshot/status")
async def get_snapshot_status_endpoint():
    return get_live_snapshot_status(app)


@app.put("/settings/live-sample")
async def update_live_sample_settings(request: LiveSampleSettingsRequest):
    if not LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED:
        raise HTTPException(status_code=403, detail="live sample target updates are restricted to administrators")
    settings = configure_live_sample_target(app, request.target_battles)
    asyncio.create_task(refresh_live_snapshot_once(app))
    return settings


@app.post("/process")
async def process(request: ProcessRequest):
    user_text = get_user_text(request)
    logger.info("request received text=%r", user_text)

    response_id = f"resp-{uuid.uuid4().hex}"
    message_id = f"msg-{uuid.uuid4().hex}"

    async def event_stream():
        yield sse_data(
            {
                "object": "response",
                "id": response_id,
                "status": "in_progress",
                "session_id": request.session_id,
            }
        )
        yield sse_data(
            {
                "object": "message",
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
            }
        )
        yield sse_data(
            {
                "object": "progress",
                "status": "in_progress",
                "stage": "parse",
                "label": "正在解析问题并选择执行路径...",
            }
        )

        event_sink = RuntimeEventEmitter()
        answer_task = asyncio.create_task(build_answer(user_text, app, event_sink=event_sink))
        stages = [
            ("route", "正在确定结构化查询或 RAG 路径..."),
            ("retrieve", "正在检索本地知识库与证据来源..."),
            ("synthesize", "正在调用模型生成可追溯回答..."),
        ]
        stage_index = 0
        while not answer_task.done() or not event_sink.empty():
            try:
                event = await asyncio.wait_for(event_sink.next_event(), timeout=0.7)
                yield sse_data(event)
            except asyncio.TimeoutError:
                if answer_task.done():
                    continue
                stage, label = stages[min(stage_index, len(stages) - 1)]
                yield sse_data(
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
            logger.exception("answer generation failed")
            yield sse_data(
                {
                    "object": "error",
                    "status": "failed",
                    "message": "生成回答失败，请检查后端日志、模型配置和检索服务。",
                }
            )
            yield sse_data(
                {
                    "object": "response",
                    "id": response_id,
                    "status": "failed",
                }
            )
            return

        answer_text = answer_result.answer
        if event_sink.content_count == 0:
            yield sse_data(
                {
                    "object": "progress",
                    "status": "in_progress",
                    "stage": "stream",
                    "label": "正在逐段输出回答...",
                }
            )
            chunks = list(split_stream_chunks(answer_text))
            for index, chunk in enumerate(chunks):
                yield sse_data(
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
        yield sse_data(
            {
                "object": "trace",
                "status": "completed",
                "trace_id": answer_result.trace_id,
                "parsed": answer_result.parsed,
                "plan": answer_result.plan,
                "selected_skill": answer_result.selected_skill,
                "mode": answer_result.mode,
                "metadata": answer_result.metadata,
                "sub_results": answer_result.sub_results,
                "events": trace_events,
            }
        )
        yield sse_data(
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
        yield sse_data(
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

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    import uvicorn

    # Pass the in-memory application so Windows does not spawn a second interpreter
    # which may resolve a different runtime_multi module from its import path.
    uvicorn.run(app, host=RUNTIME_HOST, port=RUNTIME_PORT, reload=False)
