"""Behavior-preserving RAG preheat orchestration for the API runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Any

from clashroyale_agent.api.preheat import (
    acquire_rag_preheat_lock,
    find_reusable_rag_retriever,
    resolve_rag_preheat_target,
)
from clashroyale_agent.api.snapshot_state import (
    active_snapshot_id,
    begin_rag_candidate_build,
    can_reuse_previous_rag_index_after_failure,
    cleanup_rag_snapshot_retention,
    complete_rag_candidate_build,
    fail_rag_candidate_build,
    publish_rag_candidate_index,
    rag_ready_status,
    record_rag_candidate_validation,
    run_rag_quality_gate,
    should_discard_rag_candidate_index,
    validate_rag_candidate_documents,
    validate_rag_candidate_index,
)


@dataclass(frozen=True)
class RAGPreheatDependencies:
    """Runtime dependencies and configuration for one preheat attempt."""

    lock_factory: Callable[[], Any]
    load_docs: Callable[[], list[dict]]
    validate_snapshot_rag_documents: Callable[[dict, list[dict]], dict]
    retriever_factory: Callable[..., Any]
    evaluate_rag_quality: Callable[..., dict]
    persist_quality_report: Callable[..., Any]
    quality_gate_error: type[Exception]
    cleanup_snapshot_retention: Callable[..., Any]
    activate_snapshot_state: Callable[[Any, dict], None]
    logger: Any
    index_mode: str
    quality_gate_enabled: bool
    external_api_required: bool
    quality_report_dir: Any
    min_documents: int
    min_source_types: int
    min_probe_recall: float
    probes_per_source: int
    data_dir: Any


def build_rag_preheat_dependencies(dependencies_cls: Any, runtime: dict[str, Any]) -> Any:
    """Bind RAG preheat dependencies from the runtime compatibility namespace."""
    return dependencies_cls(
        lock_factory=runtime["threading"].Lock,
        load_docs=runtime["load_docs"],
        validate_snapshot_rag_documents=runtime["validate_snapshot_rag_documents"],
        retriever_factory=runtime["HybridRetriever"],
        evaluate_rag_quality=runtime["evaluate_rag_quality"],
        persist_quality_report=runtime["persist_quality_report"],
        quality_gate_error=runtime["RAGQualityGateError"],
        cleanup_snapshot_retention=runtime["cleanup_snapshot_retention"],
        activate_snapshot_state=runtime["_activate_snapshot_state"],
        logger=runtime["logger"],
        index_mode=runtime["RAG_INDEX_MODE"],
        quality_gate_enabled=runtime["RAG_QUALITY_GATE_ENABLED"],
        external_api_required=runtime["EXTERNAL_API_REQUIRED"],
        quality_report_dir=runtime["RAG_QUALITY_REPORT_DIR"],
        min_documents=runtime["RAG_MIN_DOCUMENTS"],
        min_source_types=runtime["RAG_MIN_SOURCE_TYPES"],
        min_probe_recall=runtime["RAG_MIN_PROBE_RECALL_PERCENT"] / 100.0,
        probes_per_source=runtime["RAG_PROBES_PER_SOURCE"],
        data_dir=runtime["DATA_DIR"],
    )


def record_rag_preheat_baseline(
    app: Any,
    *,
    started_at: float,
    completed_at: float,
    outcome: str,
    snapshot_id: str | None,
) -> None:
    """Store the latest preheat attempt metrics as a public-safe aggregate."""
    app.state.rag_preheat_baseline = {
        "elapsed_seconds": round(max(0.0, completed_at - started_at), 3),
        "outcome": outcome,
        "snapshot_id": snapshot_id,
    }


def preheat_rag_retriever(
    app: Any,
    *,
    dependencies: RAGPreheatDependencies,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> Any | None:
    """Validate, build, and atomically publish one snapshot-aligned RAG index."""
    started_at = time.perf_counter()
    target = resolve_rag_preheat_target(app, candidate_snapshot=candidate_snapshot)
    if target is None:
        record_rag_preheat_baseline(
            app,
            started_at=started_at,
            completed_at=time.perf_counter(),
            outcome="not_ready",
            snapshot_id=None,
        )
        return None
    target_snapshot = target.snapshot
    snapshot_id = target.snapshot_id

    lock = acquire_rag_preheat_lock(app, lock_factory=dependencies.lock_factory)
    if lock is None:
        record_rag_preheat_baseline(
            app,
            started_at=started_at,
            completed_at=time.perf_counter(),
            outcome="busy",
            snapshot_id=snapshot_id,
        )
        return None

    previous_status = getattr(app.state, "rag_status", "not_ready")
    previous_retriever = getattr(app.state, "retriever", None)
    previous_snapshot_id = getattr(app.state, "rag_snapshot_id", None)
    previous_fingerprint = getattr(app.state, "rag_docs_fingerprint", None)
    outcome = "failed"
    try:
        begin_rag_candidate_build(app, has_active_retriever=previous_retriever is not None)
        rag_docs = dependencies.load_docs()
        validation = dependencies.validate_snapshot_rag_documents(target_snapshot, rag_docs)
        record_rag_candidate_validation(app, validation)
        docs_fingerprint = validate_rag_candidate_documents(
            validation,
            snapshot_fingerprint=target_snapshot.get("rag_docs_fingerprint"),
        )

        existing = find_reusable_rag_retriever(
            app,
            snapshot_id=snapshot_id,
            docs_fingerprint=docs_fingerprint,
            activate_snapshot=activate_snapshot,
        )
        if existing is not None:
            complete_rag_candidate_build(
                app,
                status=rag_ready_status(existing),
                validation=validation,
            )
            outcome = "reused"
            return existing

        candidate = dependencies.retriever_factory(
            rag_docs,
            in_memory=dependencies.index_mode == "memory",
        )
        validate_rag_candidate_index(
            candidate_snapshot_id=getattr(candidate, "snapshot_id", None),
            expected_snapshot_id=snapshot_id,
            candidate_docs_fingerprint=getattr(candidate, "docs_fingerprint", None),
            expected_docs_fingerprint=docs_fingerprint,
        )
        run_rag_quality_gate(
            enabled=dependencies.quality_gate_enabled,
            external_api_required=dependencies.external_api_required,
            evaluate=dependencies.evaluate_rag_quality,
            persist=dependencies.persist_quality_report,
            record=lambda report: setattr(app.state, "rag_quality_report", report),
            quality_gate_error=dependencies.quality_gate_error,
            snapshot_id=snapshot_id,
            docs=rag_docs,
            retriever=candidate,
            report_dir=dependencies.quality_report_dir,
            min_documents=dependencies.min_documents,
            min_source_types=dependencies.min_source_types,
            min_probe_recall=dependencies.min_probe_recall,
            probes_per_source=dependencies.probes_per_source,
        )
        if should_discard_rag_candidate_index(
            activate_snapshot=activate_snapshot,
            active_snapshot_id=active_snapshot_id(app),
            candidate_snapshot_id=snapshot_id,
        ):
            app.state.rag_status = "not_ready"
            outcome = "discarded"
            return None

        if activate_snapshot:
            dependencies.activate_snapshot_state(app, target_snapshot)
        publish_rag_candidate_index(
            app,
            candidate=candidate,
            snapshot_id=snapshot_id,
            docs_fingerprint=docs_fingerprint,
            validation=validation,
            previous_retriever=previous_retriever,
        )
        dependencies.logger.info(
            "rag_preheat_complete snapshot_id=%s documents=%s docs_fingerprint=%s mode=%s",
            snapshot_id,
            len(rag_docs),
            docs_fingerprint[:12],
            app.state.rag_status,
        )
        retention = cleanup_rag_snapshot_retention(
            index_mode=dependencies.index_mode,
            cleanup=dependencies.cleanup_snapshot_retention,
            data_dir=dependencies.data_dir,
            active_snapshot_id=str(snapshot_id),
        )
        if retention is not None:
            dependencies.logger.info(
                "snapshot_retention_complete retained=%s removed=%s",
                retention["retained_snapshot_ids"],
                retention["removed_snapshot_ids"],
            )
        outcome = "ready"
        return candidate
    except Exception as exc:
        old_index_usable = can_reuse_previous_rag_index_after_failure(
            previous_status=previous_status,
            previous_retriever=previous_retriever,
            previous_snapshot_id=previous_snapshot_id,
            previous_fingerprint=previous_fingerprint,
            active_snapshot=getattr(app.state, "live_snapshot", None),
        )
        app.state.rag_status = previous_status if old_index_usable else "failed"
        fail_rag_candidate_build(app, error_type=type(exc).__name__)
        dependencies.logger.warning(
            "rag_preheat_failed snapshot_id=%s error_type=%s",
            snapshot_id,
            type(exc).__name__,
        )
        return None
    finally:
        record_rag_preheat_baseline(
            app,
            started_at=started_at,
            completed_at=time.perf_counter(),
            outcome=outcome,
            snapshot_id=snapshot_id,
        )
        lock.release()


__all__ = [
    "RAGPreheatDependencies",
    "build_rag_preheat_dependencies",
    "preheat_rag_retriever",
    "record_rag_preheat_baseline",
]
