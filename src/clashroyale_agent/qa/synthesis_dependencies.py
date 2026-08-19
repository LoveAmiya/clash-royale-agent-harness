"""Dependency contract for the evidence-synthesis workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg

from app_config import (
    EXTERNAL_API_REQUIRED,
    META_COMPRESS_CHAR_BUDGET,
    META_COMPRESS_MAX_ITEMS,
    META_RERANK_TOP_N,
    META_RETRIEVAL_LANE_TOP_K,
    MODEL_CALL_TIMEOUT_SECONDS,
    OPENAI_REVIEW_MODEL,
    RAG_FACT_VALIDATION_ENABLED,
    RETRIEVAL_FUSION_MODE,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
    SYNTHESIS_REASONING_EFFORT,
)
from clashroyale_agent.qa.evidence_grounding import (
    build_evidence_ledger as default_build_evidence_ledger,
    create_grounded_stream_buffer as default_create_grounded_stream_buffer,
    filter_completed_answer as default_filter_completed_answer,
    validate_ledger_grounding as default_validate_ledger_grounding,
)
from clashroyale_agent.qa.presentation import emit_chunked_content as default_emit_chunked_content
from clashroyale_agent.qa.retrieval_orchestration import summarize_retrieval as default_summarize_retrieval
from clashroyale_agent.qa.synthesis_contracts import DATA_ANALYSIS_SYSTEM_PROMPT, RequiredExternalAPIError
from clashroyale_agent.qa.synthesis_defaults import (
    build_reviewer_model as default_build_reviewer_model,
    retrieve_meta_candidates as default_retrieve_meta_candidates,
    stream_with_first_token_watchdog as default_stream_with_first_token_watchdog,
)
from clashroyale_agent.qa.synthesis_fallbacks import (
    build_retrieved_evidence_fallback as default_build_retrieved_evidence_fallback,
    build_snapshot_fallback_answer as default_build_snapshot_fallback_answer,
)
from model_gateway import (
    generate_model_text as default_generate_model_text,
    generate_model_text_stream as default_generate_model_text_stream,
    uses_responses_api as default_uses_responses_api,
)
from query_parser import extract_text_content as default_extract_text_content
from retrieval_postprocess import (
    compress_results as default_compress_results,
    rerank_results as default_rerank_results,
    select_diverse_results as default_select_diverse_results,
)
from skills.meta_evidence import build_meta_evidence_pack as default_build_meta_evidence_pack


META_EVIDENCE_LANES = ("archetype", "deck_profile", "card_pair")


@dataclass(slots=True)
class EvidenceSynthesisDependencies:
    build_evidence_ledger: Any = default_build_evidence_ledger
    build_meta_evidence_pack: Any = default_build_meta_evidence_pack
    build_retrieved_evidence_fallback: Any = default_build_retrieved_evidence_fallback
    build_reviewer_model: Any = default_build_reviewer_model
    build_snapshot_fallback_answer: Any = default_build_snapshot_fallback_answer
    compress_results: Any = default_compress_results
    create_grounded_stream_buffer: Any = default_create_grounded_stream_buffer
    emit_chunked_content: Any = default_emit_chunked_content
    extract_text_content: Any = default_extract_text_content
    filter_completed_answer: Any = default_filter_completed_answer
    generate_model_text: Any = default_generate_model_text
    generate_model_text_stream: Any = default_generate_model_text_stream
    logger: Any = logging.getLogger("clashroyale_agent.qa.evidence_synthesis")
    rerank_results: Any = default_rerank_results
    retrieve_meta_candidates: Any = default_retrieve_meta_candidates
    select_diverse_results: Any = default_select_diverse_results
    stream_with_first_token_watchdog: Any = default_stream_with_first_token_watchdog
    summarize_retrieval: Any = default_summarize_retrieval
    uses_responses_api: Any = default_uses_responses_api
    validate_ledger_grounding: Any = default_validate_ledger_grounding
    re_act_agent_cls: Any = ReActAgent
    openai_chat_formatter_cls: Any = OpenAIChatFormatter
    in_memory_memory_cls: Any = InMemoryMemory
    msg_cls: Any = Msg
    required_external_api_error_cls: Any = RequiredExternalAPIError
    data_analysis_system_prompt: str = DATA_ANALYSIS_SYSTEM_PROMPT
    external_api_required: bool = EXTERNAL_API_REQUIRED
    meta_compress_char_budget: int = META_COMPRESS_CHAR_BUDGET
    meta_compress_max_items: int = META_COMPRESS_MAX_ITEMS
    meta_evidence_lanes: tuple[str, ...] = META_EVIDENCE_LANES
    meta_rerank_top_n: int = META_RERANK_TOP_N
    meta_retrieval_lane_top_k: int = META_RETRIEVAL_LANE_TOP_K
    model_call_timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS
    openai_review_model: str = OPENAI_REVIEW_MODEL
    rag_fact_validation_enabled: bool = RAG_FACT_VALIDATION_ENABLED
    retrieval_fusion_mode: str = RETRIEVAL_FUSION_MODE
    retrieval_top_k_bm25: int = RETRIEVAL_TOP_K_BM25
    retrieval_top_k_dense: int = RETRIEVAL_TOP_K_DENSE
    synthesis_reasoning_effort: str = SYNTHESIS_REASONING_EFFORT


__all__ = ["EvidenceSynthesisDependencies", "META_EVIDENCE_LANES"]
