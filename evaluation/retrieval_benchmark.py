"""Snapshot-aware retrieval benchmark for the official RAG evidence corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import time
from pathlib import Path
from typing import Any

from app_config import (
    EMBED_BATCH_SIZE,
    RETRIEVAL_ALPHA,
    RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_FUSION_MODE,
    RETRIEVAL_RRF_K,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
)
from hybrid_retriever import HybridRetriever, load_docs
from evaluation.scorecard import attach_scorecard
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

DEFAULT_BOOTSTRAP_ITERATIONS = 2000
DEFAULT_BOOTSTRAP_SEED = 20260726


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def summarize_latency(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "mean": statistics.fmean(ordered) if ordered else 0.0,
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1] if ordered else 0.0,
    }


def summarize_candidate_counts(values: list[int]) -> dict[str, float | int]:
    """Summarize candidates returned per query without changing result rows."""
    return summarize_latency([float(value) for value in values])


def bootstrap_mean_ci(
    values: list[float],
    *,
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Return a deterministic percentile bootstrap interval for a sample mean."""
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    sample = [float(value) for value in values]
    observed_mean = statistics.fmean(sample) if sample else 0.0
    if not sample:
        return {
            "mean": observed_mean,
            "lower": observed_mean,
            "upper": observed_mean,
            "confidence": 0.95,
            "iterations": iterations,
            "seed": seed,
        }
    generator = random.Random(seed)
    size = len(sample)
    bootstrap_means = sorted(
        statistics.fmean(sample[generator.randrange(size)] for _ in range(size))
        for _ in range(iterations)
    )
    return {
        "mean": observed_mean,
        "lower": _percentile(bootstrap_means, 0.025),
        "upper": _percentile(bootstrap_means, 0.975),
        "confidence": 0.95,
        "iterations": iterations,
        "seed": seed,
    }


def paired_cohens_d(baseline: list[float], treatment: list[float]) -> float | None:
    """Calculate paired Cohen's d_z for treatment minus baseline."""
    if len(baseline) != len(treatment):
        raise ValueError("paired samples must have the same length")
    differences = [
        float(after) - float(before)
        for before, after in zip(baseline, treatment, strict=True)
    ]
    if not differences:
        return None
    mean_difference = statistics.fmean(differences)
    if all(math.isclose(item, differences[0], abs_tol=1e-12) for item in differences):
        return 0.0 if math.isclose(mean_difference, 0.0, abs_tol=1e-12) else None
    standard_deviation = statistics.stdev(differences)
    if math.isclose(standard_deviation, 0.0, abs_tol=1e-12):
        return None
    return mean_difference / standard_deviation


def compare_paired_rankings(
    baseline: list[dict[str, Any]],
    treatment: list[dict[str, Any]],
    *,
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Compare two rankings by aligned case id, preserving paired observations."""
    baseline_by_id = {str(row["case_id"]): row for row in baseline}
    treatment_by_id = {str(row["case_id"]): row for row in treatment}
    if len(baseline_by_id) != len(baseline) or len(treatment_by_id) != len(treatment):
        raise ValueError("case ids must be unique")
    if baseline_by_id.keys() != treatment_by_id.keys():
        raise ValueError("paired rankings must contain the same case ids")
    case_ids = sorted(baseline_by_id)
    paired_case_scores = []
    for case_id in case_ids:
        before = baseline_by_id[case_id]
        after = treatment_by_id[case_id]
        paired_case_scores.append(
            {
                "case_id": case_id,
                "baseline_recall_at_k": float(before["recall_at_k"]),
                "treatment_recall_at_k": float(after["recall_at_k"]),
                "recall_at_k_delta": float(after["recall_at_k"]) - float(before["recall_at_k"]),
                "baseline_reciprocal_rank": float(before["reciprocal_rank"]),
                "treatment_reciprocal_rank": float(after["reciprocal_rank"]),
                "reciprocal_rank_delta": float(after["reciprocal_rank"])
                - float(before["reciprocal_rank"]),
            }
        )
    report: dict[str, Any] = {
        "case_count": len(case_ids),
        "case_scores": paired_case_scores,
    }
    for metric in ("recall_at_k", "reciprocal_rank"):
        before = [float(baseline_by_id[case_id][metric]) for case_id in case_ids]
        after = [float(treatment_by_id[case_id][metric]) for case_id in case_ids]
        differences = [right - left for left, right in zip(before, after, strict=True)]
        report[metric] = {
            "baseline_mean": statistics.fmean(before) if before else 0.0,
            "treatment_mean": statistics.fmean(after) if after else 0.0,
            "mean_delta": statistics.fmean(differences) if differences else 0.0,
            "ci95": bootstrap_mean_ci(differences, iterations=iterations, seed=seed),
            "paired_cohens_d": paired_cohens_d(before, after),
        }
    return report


def default_benchmark_index_path(docs: list[dict[str, Any]]) -> Path:
    """Keep offline evaluation isolated from the runtime's locked Qdrant path."""
    snapshot_ids = {
        str(doc.get("metadata", {}).get("snapshot_id", "")).strip()
        for doc in docs
        if isinstance(doc, dict)
    }
    snapshot_id = snapshot_ids.pop() if len(snapshot_ids) == 1 else "mixed"
    identity = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()[:16]
    return Path("data") / "retrieval_benchmark_qdrant" / identity


def score_ranking(
    rows: list[dict[str, Any]],
    k: int = 5,
    *,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    if k <= 0:
        raise ValueError("k must be positive")
    hits = 0
    reciprocal_rank_sum = 0.0
    case_scores = []
    for row in rows:
        retrieved = row.get("retrieved_doc_ids", [])[:k]
        relevant = row["relevant_doc_id"]
        recall = int(relevant in retrieved)
        reciprocal_rank = 1.0 / (retrieved.index(relevant) + 1) if recall else 0.0
        case_scores.append(
            {
                "case_id": str(row["case_id"]),
                "recall_at_k": recall,
                "reciprocal_rank": reciprocal_rank,
            }
        )
        if relevant in retrieved:
            hits += 1
            reciprocal_rank_sum += reciprocal_rank
    count = len(rows)
    recall_values = [float(row["recall_at_k"]) for row in case_scores]
    reciprocal_rank_values = [float(row["reciprocal_rank"]) for row in case_scores]
    return {
        "case_count": count,
        "k": k,
        "hits_at_k": hits,
        "recall_at_k": hits / count if count else 0.0,
        "mrr_at_k": reciprocal_rank_sum / count if count else 0.0,
        "recall_at_k_ci95": bootstrap_mean_ci(
            recall_values, iterations=bootstrap_iterations, seed=bootstrap_seed
        ),
        "mrr_at_k_ci95": bootstrap_mean_ci(
            reciprocal_rank_values, iterations=bootstrap_iterations, seed=bootstrap_seed
        ),
        "case_scores": case_scores,
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
                "dataset_scope": str(meta.get("dataset_scope") or "") or None,
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


def evaluate_cases(
    retriever: HybridRetriever,
    cases: list[dict[str, Any]],
    k: int = 5,
    *,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    if not retriever.dense_available:
        raise RuntimeError("Dense retrieval is unavailable; refusing to label BM25 fallback as Hybrid.")
    embedding_started = time.perf_counter()
    query_vectors: list[list[float] | None] = []
    embed_texts = getattr(retriever, "embed_texts", None)
    if callable(embed_texts):
        for start in range(0, len(cases), EMBED_BATCH_SIZE):
            batch = cases[start : start + EMBED_BATCH_SIZE]
            query_vectors.extend(embed_texts([case["query"] for case in batch]))
    else:
        query_vectors = [None] * len(cases)
    query_embedding_ms = (time.perf_counter() - embedding_started) * 1000
    bm25_rows = []
    hybrid_rows = []
    rerank_rows = []
    method_latencies = {"bm25": [], "hybrid": [], "hybrid_rerank": []}
    method_candidate_counts = {"bm25": [], "hybrid": [], "hybrid_rerank": []}
    for case, query_vector in zip(cases, query_vectors, strict=True):
        bm25_started = time.perf_counter()
        bm25 = retriever.bm25_search(
            case["query"],
            top_k=RETRIEVAL_TOP_K_BM25,
            source_type=case["source_type"],
            dataset_scope=case.get("dataset_scope"),
        )
        method_latencies["bm25"].append((time.perf_counter() - bm25_started) * 1000)
        hybrid_started = time.perf_counter()
        hybrid = retriever.hybrid_search(
            case["query"],
            top_k_bm25=RETRIEVAL_TOP_K_BM25,
            top_k_dense=RETRIEVAL_TOP_K_DENSE,
            final_top_k=RETRIEVAL_FINAL_TOP_K,
            alpha=RETRIEVAL_ALPHA,
            fusion_mode=RETRIEVAL_FUSION_MODE,
            rrf_k=RETRIEVAL_RRF_K,
            source_type=case["source_type"],
            dataset_scope=case.get("dataset_scope"),
            query_vector=query_vector,
        )
        hybrid_latency = (time.perf_counter() - hybrid_started) * 1000
        method_latencies["hybrid"].append(hybrid_latency)
        rerank_started = time.perf_counter()
        reranked = rerank_results(case["query"], case["parsed"], hybrid, top_n=8)
        rerank_latency = (time.perf_counter() - rerank_started) * 1000
        method_latencies["hybrid_rerank"].append(hybrid_latency + rerank_latency)
        method_candidate_counts["bm25"].append(len(bm25))
        method_candidate_counts["hybrid"].append(len(hybrid))
        method_candidate_counts["hybrid_rerank"].append(len(reranked))
        base = {"case_id": case["case_id"], "relevant_doc_id": case["relevant_doc_id"]}
        bm25_rows.append({**base, "retrieved_doc_ids": [item["doc"]["doc_id"] for item in bm25[:k]]})
        hybrid_rows.append({**base, "retrieved_doc_ids": [item["doc"]["doc_id"] for item in hybrid[:k]]})
        rerank_rows.append({**base, "retrieved_doc_ids": [item["doc"]["doc_id"] for item in reranked[:k]]})
    method_rows = {
        "bm25": bm25_rows,
        "hybrid": hybrid_rows,
        "hybrid_rerank": rerank_rows,
    }
    method_reports = {}
    for name, rows in method_rows.items():
        latency_ms = summarize_latency(method_latencies[name])
        metrics = score_ranking(
            rows,
            k,
            bootstrap_iterations=bootstrap_iterations,
            bootstrap_seed=bootstrap_seed,
        )
        method_reports[name] = {
            "variant": name,
            # Keep legacy fields while exposing common benchmark field names.
            "latency_ms": latency_ms,
            "retrieval_latency_ms": latency_ms,
            "candidate_count": summarize_candidate_counts(method_candidate_counts[name]),
            "hit_rate_at_k": metrics["recall_at_k"],
            "metrics": metrics,
            "results": rows,
        }
    comparisons = {}
    for name, baseline_name, treatment_name in (
        ("hybrid_vs_bm25", "bm25", "hybrid"),
        ("hybrid_rerank_vs_hybrid", "hybrid", "hybrid_rerank"),
        ("hybrid_rerank_vs_bm25", "bm25", "hybrid_rerank"),
    ):
        comparisons[name] = compare_paired_rankings(
            method_reports[baseline_name]["metrics"]["case_scores"],
            method_reports[treatment_name]["metrics"]["case_scores"],
            iterations=bootstrap_iterations,
            seed=bootstrap_seed,
        )
        comparisons[name]["baseline"] = baseline_name
        comparisons[name]["treatment"] = treatment_name
    return {
        "benchmark": "Snapshot-scoped RAG retrieval benchmark",
        "case_count": len(cases),
        "dense_available": retriever.dense_available,
        "runtime": {
            "query_embedding_ms": query_embedding_ms,
            "query_embedding_batches": math.ceil(len(cases) / EMBED_BATCH_SIZE),
        },
        "ablation": {
            "variants": ["bm25", "hybrid", "hybrid_rerank"],
            "hybrid_rerank_includes_metadata_bonus": True,
            "bootstrap_iterations": bootstrap_iterations,
            "bootstrap_seed": bootstrap_seed,
            "retrieval_config": {
                "fusion_mode": RETRIEVAL_FUSION_MODE,
                "rrf_k": RETRIEVAL_RRF_K if RETRIEVAL_FUSION_MODE == "rrf" else None,
                "bm25_top_k": RETRIEVAL_TOP_K_BM25,
                "dense_top_k": RETRIEVAL_TOP_K_DENSE,
                "candidate_top_k": RETRIEVAL_FINAL_TOP_K,
            },
        },
        "methods": method_reports,
        "paired_comparisons": comparisons,
    }


def attach_corpus_identity(report: dict[str, Any], retriever: HybridRetriever) -> dict[str, Any]:
    """Bind an offline quality report to the exact evidence corpus it evaluated."""
    report["snapshot_id"] = retriever.snapshot_id
    report["docs_fingerprint"] = retriever.docs_fingerprint
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="evaluation/retrieval_benchmark_report.json")
    parser.add_argument("--docs-path")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--index-path")
    parser.add_argument("--bootstrap-iterations", type=int, default=DEFAULT_BOOTSTRAP_ITERATIONS)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    args = parser.parse_args()
    if args.k <= 0:
        raise SystemExit("--k must be positive")
    if args.bootstrap_iterations <= 0:
        raise SystemExit("--bootstrap-iterations must be positive")
    load_started = time.perf_counter()
    docs = (
        json.loads(Path(args.docs_path).read_text(encoding="utf-8"))
        if args.docs_path
        else load_docs()
    )
    docs_load_ms = (time.perf_counter() - load_started) * 1000
    if not isinstance(docs, list):
        raise SystemExit("RAG documents must be a JSON list")
    cases = build_cases(docs)
    index_path = Path(args.index_path) if args.index_path else default_benchmark_index_path(docs)
    retriever_started = time.perf_counter()
    retriever = HybridRetriever(docs, index_path=index_path)
    retriever_init_ms = (time.perf_counter() - retriever_started) * 1000
    try:
        report = evaluate_cases(
            retriever,
            cases,
            args.k,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=args.bootstrap_seed,
        )
    finally:
        retriever.close()
    attach_corpus_identity(report, retriever)
    report["case_source"] = "selected evidence corpus with scope-aware template-generated silver labels"
    report["index_path"] = str(index_path)
    report["docs_path"] = str(args.docs_path) if args.docs_path else str(Path("data") / "rag_documents.json")
    report["runtime"] = {
        **report.get("runtime", {}),
        "docs_load_ms": docs_load_ms,
        "retriever_init_ms": retriever_init_ms,
        "reused_persisted_index": bool(getattr(retriever, "reused_persisted_index", False)),
    }
    attach_scorecard(report, source="retrieval")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "runtime": report["runtime"],
                "methods": {
                    name: {
                        "recall_at_k": item["metrics"]["recall_at_k"],
                        "mrr_at_k": item["metrics"]["mrr_at_k"],
                        "latency_ms": item["latency_ms"],
                    }
                    for name, item in report["methods"].items()
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
