"""Default adapters used by evidence synthesis orchestration."""

from __future__ import annotations

from typing import Any

from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from app_config import (
    META_RETRIEVAL_LANE_TOP_K, OPENAI_CLIENT_KWARGS, OPENAI_REASONING_EFFORT,
    OPENAI_REVIEW_MODEL, OPENAI_WIRE_API, RETRIEVAL_ALPHA, RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_TOP_K_BM25, RETRIEVAL_TOP_K_DENSE, MODEL_FIRST_TOKEN_TIMEOUT_SECONDS,
    MODEL_PROGRESS_INTERVAL_SECONDS,
)
from clashroyale_agent.qa.retrieval_orchestration import RetrievalConfig, retrieve_meta_candidates as _retrieve_meta_candidates
from clashroyale_agent.qa.reviewer_models import ReviewerModelConfig, build_reviewer_model as _build_reviewer_model
from clashroyale_agent.qa.streaming import stream_with_first_token_watchdog as _stream_with_first_token_watchdog


def build_reviewer_model(api_key: str) -> OpenAIChatModel | OpenAIResponseModel:
    return _build_reviewer_model(api_key, config=ReviewerModelConfig(model_name=OPENAI_REVIEW_MODEL, client_kwargs=OPENAI_CLIENT_KWARGS, reasoning_effort=OPENAI_REASONING_EFFORT, wire_api=OPENAI_WIRE_API), chat_model_cls=OpenAIChatModel, response_model_cls=OpenAIResponseModel)


def retrieve_meta_candidates(retriever: Any, query: str, *, dataset_scope: str | None, deck_mode: str | None, entity_mode: str | None, source_type: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    return _retrieve_meta_candidates(retriever, query, dataset_scope=dataset_scope, deck_mode=deck_mode, entity_mode=entity_mode, source_type=source_type, config=RetrievalConfig(top_k_bm25=RETRIEVAL_TOP_K_BM25, top_k_dense=RETRIEVAL_TOP_K_DENSE, final_top_k=RETRIEVAL_FINAL_TOP_K, alpha=RETRIEVAL_ALPHA, meta_lane_top_k=META_RETRIEVAL_LANE_TOP_K, lane_source_types=("archetype", "deck_profile", "card_pair")))


async def stream_with_first_token_watchdog(stream, *, event_sink: Any, step_id: str, subquery_id: str):
    async for delta in _stream_with_first_token_watchdog(stream, event_sink=event_sink, step_id=step_id, subquery_id=subquery_id, first_token_timeout_seconds=MODEL_FIRST_TOKEN_TIMEOUT_SECONDS, progress_interval_seconds=MODEL_PROGRESS_INTERVAL_SECONDS):
        yield delta


__all__ = ["build_reviewer_model", "retrieve_meta_candidates", "stream_with_first_token_watchdog"]
