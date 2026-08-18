import json
import logging
import asyncio
import time
from dataclasses import dataclass, field

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from answer_builder import build_retrieval_query
from app_config import (
    COMPRESS_CHAR_BUDGET,
    COMPRESS_MAX_ITEMS,
    META_COMPRESS_CHAR_BUDGET,
    META_COMPRESS_MAX_ITEMS,
    META_RERANK_TOP_N,
    META_RETRIEVAL_LANE_TOP_K,
    RERANK_TOP_N,
    RETRIEVAL_ALPHA,
    RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_FUSION_MODE,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
    OPENAI_CLIENT_KWARGS,
    OPENAI_MODEL,
    OPENAI_REVIEW_MODEL,
    OPENAI_REASONING_EFFORT,
    SYNTHESIS_REASONING_EFFORT,
    OPENAI_WIRE_API,
    MODEL_CALL_TIMEOUT_SECONDS,
    MODEL_FIRST_TOKEN_TIMEOUT_SECONDS,
    MODEL_PROGRESS_INTERVAL_SECONDS,
    EXTERNAL_API_REQUIRED,
    RAG_FACT_VALIDATION_ENABLED,
)
from clashroyale_agent.qa.answer_routing import (
    subquery_needs_rag as packaged_subquery_needs_rag,
    subquery_title as packaged_subquery_title,
    subquery_user_text as packaged_subquery_user_text,
)
from clashroyale_agent.qa.evidence_synthesis import (
    EvidenceSynthesisDependencies,
    build_evidence_synthesis_answer as packaged_build_evidence_synthesis_answer,
)
from clashroyale_agent.qa.evidence_grounding import (
    GroundingValidationError,
    append_references_if_missing,
    build_evidence_ledger,
    build_reference_suffix,
    create_grounded_stream_buffer,
    filter_completed_answer,
    validate_ledger_grounding,
)
from clashroyale_agent.qa.presentation import emit_chunked_content
from clashroyale_agent.qa.rag_answering import (
    RagAnswerDependencies,
    build_rag_answer as packaged_build_rag_answer,
)
from clashroyale_agent.qa.retrieval_orchestration import (
    RetrievalConfig,
    retrieve_meta_candidates as packaged_retrieve_meta_candidates,
    summarize_retrieval as packaged_summarize_retrieval,
)
from clashroyale_agent.qa.reviewer_models import (
    ReviewerModelConfig,
    build_reviewer_model as packaged_build_reviewer_model,
)
from clashroyale_agent.qa.multi_intent_answering import (
    MultiIntentDependencies,
    answer_multi_intent_query as packaged_answer_multi_intent_query,
    compose_multi_intent_answer as packaged_compose_multi_intent_answer,
    execute_subquery as packaged_execute_subquery,
)
from clashroyale_agent.qa.structured_answering import (
    StructuredAnswerDependencies,
    answer_structured_query,
)
from clashroyale_agent.qa.streaming import (
    ModelFirstTokenTimeout,
    ModelStreamStartupError,
    stream_with_first_token_watchdog as _stream_with_first_token_watchdog,
)
from clashroyale_agent.qa.synthesis_fallbacks import (
    build_retrieved_evidence_fallback,
    build_snapshot_fallback_answer,
)
from clashroyale_agent.qa.traces import read_trace as packaged_read_trace
from harness.executor import SkillExecutor
from hybrid_retriever import HybridRetriever
from model_gateway import generate_model_text, generate_model_text_stream, uses_responses_api
from runtime_events import RuntimeEventEmitter
from planner.planner import RuleBasedPlanner
from query_parser import extract_text_content, subquery_semantic_key
from retrieval_postprocess import (
    compress_results,
    rerank_results,
    select_diverse_results,
)
from skills.base import SkillContext
from skills.meta_evidence import build_meta_evidence_pack
from skills.registry import build_default_registry


logger = logging.getLogger(__name__)
META_EVIDENCE_LANES = ("archetype", "deck_profile", "card_pair")


DATA_ANALYSIS_SYSTEM_PROMPT = (
    "你是皇室战争数据分析助手，使用中文回答。\n"
    "所有卡牌名必须使用系统标准中文名，不得直接输出英文卡牌名。\n"
    "输出必须是适合纯文本前端的自然中文：不要使用 Markdown 标题井号、星号粗体或英文占位标题。\n"
    "数据结论只能来自提供的结构化证据或当前快照 RAG 检索证据，不得虚构统计、日期、来源、卡组或对手信息。\n"
    "涉及使用率、胜率、场次等数字时必须原样引用证据，不得自行四舍五入、改写精度或推算新数值。\n"
    "可以给出由使用率、胜率、样本量、常见搭配和对阵数据支持的配卡分析，但必须明确区分观测与推断。\n"
    "当前数据不支持未来预测、精确概率或因果效果；只有检索到同口径 meta_delta 证据时才可报告相邻七天分段的观测变化，不能用当前排名或样本比例冒充历史趋势。\n"
    "不得提供战队赛赛程或战队备战建议。不得提供具体打法；问题超出数据证据时要清楚说明边界。\n"
    "不要展示内部推理过程。"
)


async def stream_with_first_token_watchdog(
    stream,
    *,
    event_sink: RuntimeEventEmitter,
    step_id: str,
    subquery_id: str,
):
    """Yield model deltas while making silent reasoning time observable."""
    async for delta in _stream_with_first_token_watchdog(
        stream,
        event_sink=event_sink,
        step_id=step_id,
        subquery_id=subquery_id,
        first_token_timeout_seconds=MODEL_FIRST_TOKEN_TIMEOUT_SECONDS,
        progress_interval_seconds=MODEL_PROGRESS_INTERVAL_SECONDS,
    ):
        yield delta


class RequiredExternalAPIError(RuntimeError):
    """Raised when strict mode forbids a local substitute for an API result."""


@dataclass(slots=True)
class AnswerResult:
    answer: str
    trace_id: str | None
    parsed: dict
    plan: dict | None
    selected_skill: str | None
    mode: str | None
    metadata: dict
    sub_results: list[dict] = field(default_factory=list)


def build_reviewer_model(api_key: str) -> OpenAIChatModel | OpenAIResponseModel:
    return packaged_build_reviewer_model(
        api_key,
        config=ReviewerModelConfig(
            model_name=OPENAI_REVIEW_MODEL,
            client_kwargs=OPENAI_CLIENT_KWARGS,
            reasoning_effort=OPENAI_REASONING_EFFORT,
            wire_api=OPENAI_WIRE_API,
        ),
        chat_model_cls=OpenAIChatModel,
        response_model_cls=OpenAIResponseModel,
    )


def retrieve_meta_candidates(
    retriever: HybridRetriever,
    query: str,
    *,
    dataset_scope: str | None,
    deck_mode: str | None,
    entity_mode: str | None,
    source_type: str | None = None,
) -> tuple[list[dict], list[str]]:
    return packaged_retrieve_meta_candidates(
        retriever,
        query,
        dataset_scope=dataset_scope,
        deck_mode=deck_mode,
        entity_mode=entity_mode,
        source_type=source_type,
        config=RetrievalConfig(
            top_k_bm25=RETRIEVAL_TOP_K_BM25,
            top_k_dense=RETRIEVAL_TOP_K_DENSE,
            final_top_k=RETRIEVAL_FINAL_TOP_K,
            alpha=RETRIEVAL_ALPHA,
            meta_lane_top_k=META_RETRIEVAL_LANE_TOP_K,
            lane_source_types=META_EVIDENCE_LANES,
        ),
    )


def summarize_retrieval(results: list[dict], *, lanes: list[str] | None = None) -> dict:
    return packaged_summarize_retrieval(results, lanes=lanes)


async def build_rag_answer(
    user_text: str,
    parsed: dict,
    retriever: HybridRetriever,
    source_type: str,
    reviewer_model: OpenAIChatModel,
    api_key: str = "",
    metadata: dict | None = None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = True,
) -> str:
    return await packaged_build_rag_answer(
        user_text,
        parsed,
        retriever,
        source_type,
        reviewer_model,
        api_key=api_key,
        metadata=metadata,
        event_sink=event_sink,
        stream_content=stream_content,
        dependencies=RagAnswerDependencies(
            append_references_if_missing=append_references_if_missing,
            build_evidence_ledger=build_evidence_ledger,
            build_reference_suffix=build_reference_suffix,
            build_retrieval_query=build_retrieval_query,
            compress_results=compress_results,
            create_grounded_stream_buffer=create_grounded_stream_buffer,
            emit_chunked_content=emit_chunked_content,
            extract_text_content=extract_text_content,
            generate_model_text=generate_model_text,
            generate_model_text_stream=generate_model_text_stream,
            logger=logger,
            rerank_results=rerank_results,
            summarize_retrieval=summarize_retrieval,
            uses_responses_api=uses_responses_api,
            validate_ledger_grounding=validate_ledger_grounding,
            compress_char_budget=COMPRESS_CHAR_BUDGET,
            compress_max_items=COMPRESS_MAX_ITEMS,
            external_api_required=EXTERNAL_API_REQUIRED,
            model_call_timeout_seconds=MODEL_CALL_TIMEOUT_SECONDS,
            rag_fact_validation_enabled=RAG_FACT_VALIDATION_ENABLED,
            rerank_top_n=RERANK_TOP_N,
            retrieval_alpha=RETRIEVAL_ALPHA,
            retrieval_final_top_k=RETRIEVAL_FINAL_TOP_K,
            retrieval_top_k_bm25=RETRIEVAL_TOP_K_BM25,
            retrieval_top_k_dense=RETRIEVAL_TOP_K_DENSE,
            synthesis_reasoning_effort=SYNTHESIS_REASONING_EFFORT,
        ),
    )


async def build_evidence_synthesis_answer(
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever,
    api_key: str,
    metadata: dict | None = None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = True,
) -> str:
    return await packaged_build_evidence_synthesis_answer(
        user_text,
        parsed,
        schedule_data,
        top_decks_data,
        cards_meta_data,
        retriever,
        api_key,
        metadata=metadata,
        event_sink=event_sink,
        stream_content=stream_content,
        dependencies=EvidenceSynthesisDependencies(
            build_evidence_ledger=build_evidence_ledger,
            build_meta_evidence_pack=build_meta_evidence_pack,
            build_retrieved_evidence_fallback=build_retrieved_evidence_fallback,
            build_reviewer_model=build_reviewer_model,
            build_snapshot_fallback_answer=build_snapshot_fallback_answer,
            compress_results=compress_results,
            create_grounded_stream_buffer=create_grounded_stream_buffer,
            emit_chunked_content=emit_chunked_content,
            extract_text_content=extract_text_content,
            filter_completed_answer=filter_completed_answer,
            generate_model_text=generate_model_text,
            generate_model_text_stream=generate_model_text_stream,
            logger=logger,
            rerank_results=rerank_results,
            retrieve_meta_candidates=retrieve_meta_candidates,
            select_diverse_results=select_diverse_results,
            stream_with_first_token_watchdog=stream_with_first_token_watchdog,
            summarize_retrieval=summarize_retrieval,
            uses_responses_api=uses_responses_api,
            validate_ledger_grounding=validate_ledger_grounding,
            re_act_agent_cls=ReActAgent,
            openai_chat_formatter_cls=OpenAIChatFormatter,
            in_memory_memory_cls=InMemoryMemory,
            msg_cls=Msg,
            required_external_api_error_cls=RequiredExternalAPIError,
            data_analysis_system_prompt=DATA_ANALYSIS_SYSTEM_PROMPT,
            external_api_required=EXTERNAL_API_REQUIRED,
            meta_compress_char_budget=META_COMPRESS_CHAR_BUDGET,
            meta_compress_max_items=META_COMPRESS_MAX_ITEMS,
            meta_evidence_lanes=META_EVIDENCE_LANES,
            meta_rerank_top_n=META_RERANK_TOP_N,
            meta_retrieval_lane_top_k=META_RETRIEVAL_LANE_TOP_K,
            model_call_timeout_seconds=MODEL_CALL_TIMEOUT_SECONDS,
            openai_review_model=OPENAI_REVIEW_MODEL,
            rag_fact_validation_enabled=RAG_FACT_VALIDATION_ENABLED,
            retrieval_fusion_mode=RETRIEVAL_FUSION_MODE,
            retrieval_top_k_bm25=RETRIEVAL_TOP_K_BM25,
            retrieval_top_k_dense=RETRIEVAL_TOP_K_DENSE,
            synthesis_reasoning_effort=SYNTHESIS_REASONING_EFFORT,
        ),
    )


DIRECT_SKILL_REGISTRY = build_default_registry(
    rag_answer_builder=build_rag_answer,
    reviewer_model_builder=build_reviewer_model,
    evidence_synthesis_builder=build_evidence_synthesis_answer,
)
SKILL_EXECUTOR = SkillExecutor(DIRECT_SKILL_REGISTRY)
RULE_BASED_PLANNER = RuleBasedPlanner()


def subquery_needs_rag(parsed: dict) -> bool:
    return packaged_subquery_needs_rag(parsed)


def subquery_title(parsed: dict) -> str:
    return packaged_subquery_title(parsed)


def subquery_user_text(parsed: dict, original_text: str) -> str:
    return packaged_subquery_user_text(parsed, original_text)


def _multi_intent_dependencies(*, execute_subquery_func=None) -> MultiIntentDependencies:
    return MultiIntentDependencies(
        answer_result_cls=AnswerResult,
        execute_subquery=execute_subquery_func,
        recorder=SKILL_EXECUTOR.recorder,
        subquery_semantic_key=subquery_semantic_key,
        skill_context_cls=SkillContext,
        skill_executor=SKILL_EXECUTOR,
        planner=RULE_BASED_PLANNER,
        skill_registry=DIRECT_SKILL_REGISTRY,
        subquery_needs_rag=subquery_needs_rag,
        subquery_title=subquery_title,
        subquery_user_text=subquery_user_text,
        logger=logger,
    )


def compose_multi_intent_answer(results: list[dict]) -> str:
    return packaged_compose_multi_intent_answer(results)


async def execute_subquery(
    *,
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever | None,
    api_key: str,
    trace_id: str,
    runtime_metadata: dict | None = None,
    card_deck_stats: dict[str, list[dict]] | None = None,
    structured_repository=None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = False,
) -> dict:
    return await packaged_execute_subquery(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        retriever=retriever,
        api_key=api_key,
        trace_id=trace_id,
        dependencies=_multi_intent_dependencies(),
        runtime_metadata=runtime_metadata,
        card_deck_stats=card_deck_stats,
        structured_repository=structured_repository,
        event_sink=event_sink,
        stream_content=stream_content,
    )


async def answer_multi_intent_query(
    *,
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever | None,
    api_key: str,
    runtime_metadata: dict | None = None,
    card_deck_stats: dict[str, list[dict]] | None = None,
    structured_repository=None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = False,
) -> AnswerResult:
    return await packaged_answer_multi_intent_query(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        retriever=retriever,
        api_key=api_key,
        dependencies=_multi_intent_dependencies(execute_subquery_func=execute_subquery),
        runtime_metadata=runtime_metadata,
        card_deck_stats=card_deck_stats,
        structured_repository=structured_repository,
        event_sink=event_sink,
        stream_content=stream_content,
    )


async def answer_query(
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever | None,
    api_key: str,
    include_metadata: bool = False,
    runtime_metadata: dict | None = None,
    card_deck_stats: dict[str, list[dict]] | None = None,
    structured_repository=None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = True,
) -> str | AnswerResult:
    intent = parsed["intent"]
    if intent == "multi_intent":
        result = await answer_multi_intent_query(
            user_text=user_text,
            parsed=parsed,
            schedule_data=schedule_data,
            top_decks_data=top_decks_data,
            cards_meta_data=cards_meta_data,
            retriever=retriever,
            api_key=api_key,
            runtime_metadata=runtime_metadata,
            card_deck_stats=card_deck_stats,
            structured_repository=structured_repository,
            event_sink=event_sink,
            stream_content=stream_content,
        )
        return result if include_metadata else result.answer
    structured_result = await answer_structured_query(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        retriever=retriever,
        api_key=api_key,
        runtime_metadata=runtime_metadata,
        card_deck_stats=card_deck_stats,
        structured_repository=structured_repository,
        event_sink=event_sink,
        stream_content=stream_content,
        dependencies=StructuredAnswerDependencies(
            skill_executor=SKILL_EXECUTOR,
            planner=RULE_BASED_PLANNER,
        ),
    )
    if structured_result.answer is not None:
        logger.info("answer route intent=%s mode=skill_executor", intent)
        if include_metadata:
            return AnswerResult(
                answer=structured_result.answer,
                trace_id=structured_result.trace_id,
                parsed=parsed,
                plan=structured_result.plan,
                selected_skill=structured_result.selected_skill,
                mode=structured_result.mode,
                metadata=dict(structured_result.metadata),
            )
        return structured_result.answer

    logger.info("answer route intent=reject mode=fallback")
    fallback_answer = (
        "当前系统主要支持：\n"
        "- 单卡使用率/胜率查询\n"
        "- 卡牌比较与排行榜查询\n"
        "- 热门卡组查询\n"
        "- 基于证据的环境分析"
    )
    if include_metadata:
        return AnswerResult(
            answer=fallback_answer,
            trace_id=structured_result.trace_id,
            parsed=parsed,
            plan=structured_result.plan,
            selected_skill=None,
            mode="fallback",
            metadata=dict(structured_result.metadata),
        )
    return fallback_answer


def read_trace(trace_id: str | None) -> list[dict]:
    return packaged_read_trace(trace_id, recorder=SKILL_EXECUTOR.recorder)
