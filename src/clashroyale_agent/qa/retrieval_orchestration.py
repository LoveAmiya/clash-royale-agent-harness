"""Retrieval orchestration helpers for QA/RAG answer generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    top_k_bm25: int
    top_k_dense: int
    final_top_k: int
    alpha: float
    meta_lane_top_k: int
    lane_source_types: tuple[str, ...]


def retrieve_meta_candidates(
    retriever: Any,
    query: str,
    *,
    dataset_scope: str | None,
    deck_mode: str | None,
    entity_mode: str | None,
    config: RetrievalConfig,
    source_type: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Recall global and typed evidence lanes, deduplicated by stable document ID."""
    lane_types: tuple[str | None, ...] = (
        (source_type,) if source_type else (None, *config.lane_source_types)
    )
    embed_query = getattr(retriever, "embed_text", None)
    query_vector = (
        embed_query(query)
        if getattr(retriever, "dense_available", False) and callable(embed_query)
        else None
    )
    merged: dict[str, dict[str, Any]] = {}
    lanes: list[str] = []
    for lane_type in lane_types:
        lane_name = lane_type or "global"
        lane_results = retriever.hybrid_search(
            query=query,
            top_k_bm25=config.top_k_bm25,
            top_k_dense=config.top_k_dense,
            final_top_k=(
                config.final_top_k if lane_type is None else config.meta_lane_top_k
            ),
            alpha=config.alpha,
            source_type=lane_type,
            dataset_scope=dataset_scope,
            deck_mode=deck_mode,
            entity_mode=entity_mode,
            query_vector=query_vector,
        )
        lanes.append(lane_name)
        for item in lane_results:
            doc_id = str(item.get("doc", {}).get("doc_id") or "")
            if not doc_id:
                continue
            previous = merged.get(doc_id)
            lane_pools = {lane_name: dict(item.get("candidate_pool") or {})}
            if previous is None:
                candidate = dict(item)
                candidate["retrieval_lanes"] = [lane_name]
                candidate["retrieval_lane_candidate_pools"] = lane_pools
                merged[doc_id] = candidate
                continue
            combined_lanes = list(dict.fromkeys([*previous.get("retrieval_lanes", []), lane_name]))
            combined_pools = {
                **previous.get("retrieval_lane_candidate_pools", {}),
                **lane_pools,
            }
            if float(item.get("final_score", 0.0)) > float(previous.get("final_score", 0.0)):
                candidate = dict(item)
                candidate["retrieval_lanes"] = combined_lanes
                candidate["retrieval_lane_candidate_pools"] = combined_pools
                merged[doc_id] = candidate
            else:
                previous["retrieval_lanes"] = combined_lanes
                previous["retrieval_lane_candidate_pools"] = combined_pools
    return list(merged.values()), lanes


def summarize_retrieval(
    results: list[dict[str, Any]],
    *,
    lanes: list[str] | None = None,
) -> dict[str, Any]:
    """Return bounded operational diagnostics without evidence text or model reasoning."""
    if not results:
        return {
            "retrieval_mode": "none",
            "fusion_mode": "none",
            "lanes": list(lanes or []),
            "candidate_count": 0,
            "lane_candidate_pools": {},
        }
    first = results[0]
    lane_candidate_pools: dict[str, dict[str, Any]] = {}
    for item in results:
        for lane, pool in (item.get("retrieval_lane_candidate_pools") or {}).items():
            lane_candidate_pools.setdefault(str(lane), dict(pool or {}))
    if not lane_candidate_pools:
        lane_candidate_pools["single"] = dict(first.get("candidate_pool") or {})
    return {
        "retrieval_mode": first.get("retrieval_mode", "unknown"),
        "fusion_mode": first.get("fusion_mode", "weighted"),
        "rrf_k": first.get("rrf_k"),
        "lanes": list(lanes or ["single"]),
        "candidate_count": len(results),
        "lane_candidate_pools": lane_candidate_pools,
    }


__all__ = ["RetrievalConfig", "retrieve_meta_candidates", "summarize_retrieval"]
