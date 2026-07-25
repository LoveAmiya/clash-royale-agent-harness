"""Snapshot-aware retrieval benchmark for the official RAG evidence corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from hybrid_retriever import HybridRetriever, load_docs
from retrieval_postprocess import rerank_results


BENCHMARK_SOURCE_LIMITS = {
    "snapshot": 1,
    "card": 12,
    "card_profile": 12,
    "deck": 12,
    "deck_profile": 12,
    "archetype": 12,
    "card_pair": 12,
    "counter": 12,
    "matchup": 12,
}


def score_ranking(rows: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    if k <= 0:
        raise ValueError("k must be positive")
    hits = 0
    reciprocal_rank_sum = 0.0
    for row in rows:
        retrieved = row.get("retrieved_doc_ids", [])[:k]
        relevant = row["relevant_doc_id"]
        if relevant in retrieved:
            hits += 1
            reciprocal_rank_sum += 1.0 / (retrieved.index(relevant) + 1)
    count = len(rows)
    return {
        "case_count": count,
        "k": k,
        "hits_at_k": hits,
        "recall_at_k": hits / count if count else 0.0,
        "mrr_at_k": reciprocal_rank_sum / count if count else 0.0,
    }


def build_cases(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    selected_by_source: dict[str, int] = {}
    for doc in docs:
        meta = doc.get("metadata", {})
        source_type = doc["source_type"]
        limit = BENCHMARK_SOURCE_LIMITS.get(source_type)
        if limit is None or selected_by_source.get(source_type, 0) >= limit:
            continue

        if source_type == "snapshot":
            query = "What official snapshot and sample boundary support this environment analysis?"
            parsed = {}
        elif source_type in {"card", "card_profile"}:
            query = f"What are the usage rate, win rate, and observed profile for {meta['card_name']}?"
            parsed = {"card_name": meta["card_name"]}
        elif source_type in {"deck", "deck_profile"}:
            query = f"What is the composition and sampled performance of {meta['deck_name']}?"
            parsed = {"deck_name": meta["deck_name"]}
        elif source_type == "archetype":
            query = f"What evidence describes the {meta['archetype']} archetype in this snapshot?"
            parsed = {}
        elif source_type == "card_pair":
            first, second = meta["cards"]
            query = f"How often do {first} and {second} appear together, and what is their observed result?"
            parsed = {"card_names": [first, second]}
        elif source_type == "counter":
            query = f"How does {meta['card_name']} perform against {meta['opponent_card_name']}?"
            parsed = {"card_name": meta["card_name"], "opponent_card_name": meta["opponent_card_name"]}
        elif source_type == "matchup":
            query = f"What is the observed matchup between {meta['deck_name']} and {meta['opponent_deck_name']}?"
            parsed = {"deck_name": meta["deck_name"], "opponent_deck_name": meta["opponent_deck_name"]}
        else:
            raise ValueError(f"unsupported source type: {source_type}")

        selected_by_source[source_type] = selected_by_source.get(source_type, 0) + 1
        cases.append(
            {
                "case_id": f"retrieval_{len(cases) + 1:03d}",
                "query": query,
                "parsed": parsed,
                "relevant_doc_id": doc["doc_id"],
                "source_type": source_type,
            }
        )
    if not cases:
        raise ValueError("no supported official snapshot RAG documents were available")
    return cases


def normalize_bm25(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not results:
        return []
    scores = [float(item["score"]) for item in results]
    lower, upper = min(scores), max(scores)
    normalized = []
    for item in results:
        score = float(item["score"])
        final_score = 1.0 if lower == upper else (score - lower) / (upper - lower)
        normalized.append({"doc": item["doc"], "final_score": final_score})
    return normalized


def evaluate_cases(retriever: HybridRetriever, cases: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    if not retriever.dense_available:
        raise RuntimeError("Dense retrieval is unavailable; refusing to label BM25 fallback as Hybrid.")
    bm25_rows = []
    hybrid_rows = []
    rerank_rows = []
    for case in cases:
        bm25 = retriever.bm25_search(case["query"], top_k=10, source_type=case["source_type"])
        hybrid = retriever.hybrid_search(
            case["query"], top_k_bm25=10, top_k_dense=10, final_top_k=8, alpha=0.5, source_type=case["source_type"]
        )
        reranked = rerank_results(case["query"], case["parsed"], hybrid, top_n=8)
        base = {"case_id": case["case_id"], "relevant_doc_id": case["relevant_doc_id"]}
        bm25_rows.append({**base, "retrieved_doc_ids": [item["doc"]["doc_id"] for item in bm25[:k]]})
        hybrid_rows.append({**base, "retrieved_doc_ids": [item["doc"]["doc_id"] for item in hybrid[:k]]})
        rerank_rows.append({**base, "retrieved_doc_ids": [item["doc"]["doc_id"] for item in reranked[:k]]})
    return {
        "benchmark": "Official snapshot RAG retrieval benchmark",
        "case_count": len(cases),
        "dense_available": retriever.dense_available,
        "methods": {
            "bm25": {"metrics": score_ranking(bm25_rows, k), "results": bm25_rows},
            "hybrid": {"metrics": score_ranking(hybrid_rows, k), "results": hybrid_rows},
            "hybrid_rerank": {"metrics": score_ranking(rerank_rows, k), "results": rerank_rows},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="evaluation/retrieval_benchmark_report.json")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    if args.k <= 0:
        raise SystemExit("--k must be positive")
    docs = load_docs()
    cases = build_cases(docs)
    report = evaluate_cases(HybridRetriever(docs), cases, args.k)
    report["case_source"] = "active official snapshot evidence with template-generated silver labels"
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: item["metrics"] for name, item in report["methods"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
