"""Small app-state helpers for the runtime snapshot lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def refresh_cooldown_seconds(failures: int) -> int:
    """Return bounded live-refresh backoff seconds for consecutive failures."""
    return (300, 900, 1800)[min(max(int(failures), 1) - 1, 2)]


def next_live_refresh_delay_seconds(
    *,
    refresh_status: str | None,
    cooldown_until: float,
    now_monotonic: float,
    snapshot_present: bool,
    snapshot_age_seconds: float | None,
    refresh_interval_seconds: float,
) -> float:
    """Return the next background refresh-loop delay without touching app state."""
    if refresh_status == "cooldown":
        return max(60.0, cooldown_until - now_monotonic)
    if refresh_status == "source_exhausted":
        return max(3600.0, cooldown_until - now_monotonic)
    if not snapshot_present:
        return 1800.0
    age_seconds = snapshot_age_seconds or 0.0
    return max(1.0, refresh_interval_seconds - age_seconds)


def live_snapshot_refresh_gate(
    *,
    now_monotonic: float,
    cooldown_until: float,
    cached_present: bool,
    legacy_scope_refresh: bool,
    refresh_due: bool,
) -> str:
    """Return the pre-lock live snapshot refresh decision."""
    if now_monotonic < cooldown_until:
        return "cooldown"
    if cached_present and not legacy_scope_refresh and not refresh_due:
        return "cached"
    return "refresh"


def rag_ready_status(retriever: Any) -> str:
    """Return the public RAG status for a validated retriever."""
    return "ready" if getattr(retriever, "dense_available", False) else "bm25_only"


def validate_rag_candidate_documents(
    validation: dict,
    *,
    snapshot_fingerprint: str | None,
) -> str:
    """Validate candidate documents and return their fingerprint for index building."""
    if not validation.get("passed"):
        raise ValueError("RAG documents failed full snapshot evidence validation")
    docs_fingerprint = validation.get("docs_fingerprint")
    if snapshot_fingerprint != docs_fingerprint:
        raise ValueError("RAG document fingerprint does not match the active official snapshot")
    if not isinstance(docs_fingerprint, str) or not docs_fingerprint:
        raise ValueError("RAG document validation did not provide a fingerprint")
    return docs_fingerprint


def validate_rag_candidate_index(
    *,
    candidate_snapshot_id: str | None,
    expected_snapshot_id: str,
    candidate_docs_fingerprint: str | None,
    expected_docs_fingerprint: str,
) -> None:
    """Validate an index identity before it can cross the evidence boundary."""
    if candidate_snapshot_id != expected_snapshot_id:
        raise ValueError("built retriever does not match the active official weekly snapshot")
    if candidate_docs_fingerprint != expected_docs_fingerprint:
        raise ValueError("built retriever docs fingerprint does not match validated RAG documents")


def run_rag_quality_gate(
    *,
    enabled: bool,
    external_api_required: bool,
    evaluate: Any,
    persist: Any,
    record: Any | None = None,
    quality_gate_error: type[Exception],
    snapshot_id: str,
    docs: list[dict],
    retriever: Any,
    report_dir: Any,
    min_documents: int,
    min_source_types: int,
    min_probe_recall: float,
    probes_per_source: int,
) -> dict | None:
    """Evaluate and persist a RAG quality report when the gate is active."""
    if not enabled or not external_api_required:
        return None
    report = evaluate(
        snapshot_id=snapshot_id,
        docs=docs,
        retriever=retriever,
        min_documents=min_documents,
        min_source_types=min_source_types,
        min_probe_recall=min_probe_recall,
        probes_per_source=probes_per_source,
    )
    persist(report=report, directory=report_dir)
    if record is not None:
        record(report)
    if not report["passed"]:
        raise quality_gate_error("RAG index did not meet the configured snapshot quality gate")
    return report


def should_discard_rag_candidate_index(
    *,
    activate_snapshot: bool,
    active_snapshot_id: str | None,
    candidate_snapshot_id: str,
) -> bool:
    """Return whether a completed candidate index no longer matches the active snapshot."""
    return not activate_snapshot and active_snapshot_id != candidate_snapshot_id


def publish_rag_candidate_index(
    app: Any,
    *,
    candidate: Any,
    snapshot_id: str,
    docs_fingerprint: str,
    validation: dict,
    previous_retriever: Any,
) -> None:
    """Publish a validated candidate index and close the replaced retriever."""
    app.state.retriever = candidate
    app.state.rag_snapshot_id = snapshot_id
    app.state.rag_docs_fingerprint = docs_fingerprint
    complete_rag_candidate_build(
        app,
        status=rag_ready_status(candidate),
        validation=validation,
        clear_candidate_error=True,
    )
    if previous_retriever is not None and previous_retriever is not candidate:
        close_previous = getattr(previous_retriever, "close", None)
        if callable(close_previous):
            close_previous()


def cleanup_rag_snapshot_retention(
    *,
    index_mode: str,
    cleanup: Any,
    data_dir: Any,
    active_snapshot_id: str,
) -> dict | None:
    """Run snapshot retention cleanup only for persistent RAG index mode."""
    if index_mode == "memory":
        return None
    return cleanup(data_dir, active_snapshot_id=str(active_snapshot_id))


def can_reuse_previous_rag_index_after_failure(
    *,
    previous_status: str,
    previous_retriever: Any,
    previous_snapshot_id: str | None,
    previous_fingerprint: str | None,
    active_snapshot: Any,
) -> bool:
    """Return whether a failed candidate build can keep serving the previous index."""
    return bool(
        previous_status in {"ready", "bm25_only"}
        and previous_retriever is not None
        and previous_snapshot_id
        and previous_fingerprint
        and isinstance(active_snapshot, dict)
        and active_snapshot.get("snapshot_id") == previous_snapshot_id
        and active_snapshot.get("rag_docs_fingerprint") == previous_fingerprint
        and getattr(previous_retriever, "docs_fingerprint", None) == previous_fingerprint
    )


def begin_rag_candidate_build(app: Any, *, has_active_retriever: bool) -> None:
    """Mark a RAG candidate build without hiding an already active retriever."""
    app.state.rag_candidate_status = "building"
    if not has_active_retriever:
        app.state.rag_status = "building"
    app.state.rag_error = None


def record_rag_candidate_validation(app: Any, validation: dict) -> None:
    """Store the latest candidate validation report for public redaction later."""
    app.state.rag_candidate_validation = validation


def complete_rag_candidate_build(
    app: Any,
    *,
    status: str,
    validation: dict,
    clear_candidate_error: bool = False,
) -> None:
    """Publish a validated RAG candidate status and active validation report."""
    app.state.rag_status = status
    app.state.rag_candidate_status = status
    app.state.rag_document_validation = validation
    if clear_candidate_error:
        app.state.rag_candidate_error = None


def fail_rag_candidate_build(app: Any, *, error_type: str) -> None:
    """Record only the RAG candidate failure type, never exception details."""
    app.state.rag_candidate_status = "failed"
    app.state.rag_candidate_error = error_type
    app.state.rag_error = error_type


def active_snapshot_id(app: Any) -> str | None:
    """Return the active live snapshot id when one is present and non-empty."""
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    return snapshot_id if isinstance(snapshot_id, str) and snapshot_id else None


def activate_snapshot_state(
    app: Any,
    snapshot: dict,
    *,
    now_monotonic: float,
    target_battles: int,
) -> None:
    """Switch every structured-data view to one already validated snapshot."""
    app.state.live_snapshot = snapshot
    app.state.live_snapshot_at = now_monotonic
    app.state.live_snapshot_target_battles = target_battles
    app.state.cards_meta_data = list(snapshot.get("cards_meta", []))
    app.state.top_decks_data = list(snapshot.get("top_decks", []))
    app.state.card_deck_stats_data = dict(snapshot.get("card_deck_stats", {}))


def record_live_refresh_attempt(
    app: Any,
    *,
    status: str,
    default_target_battles: int,
    snapshot: dict | None = None,
    error: str | None = None,
    finished_at: str | None = None,
) -> None:
    """Store the latest live-refresh attempt summary and publish collection metrics."""
    collection_metrics = snapshot.get("collection_metrics", {}) if isinstance(snapshot, dict) else {}
    sample_battles = int(snapshot.get("sample_battles", 0) or 0) if isinstance(snapshot, dict) else 0
    target_battles = (
        int(snapshot.get("target_battles", default_target_battles) or default_target_battles)
        if isinstance(snapshot, dict)
        else default_target_battles
    )
    shortfall_battles = (
        int(snapshot.get("shortfall_battles", max(0, target_battles - sample_battles)) or 0)
        if isinstance(snapshot, dict)
        else default_target_battles
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


def record_live_collection_progress(app: Any, progress: dict) -> dict:
    """Publish compact collector progress without invoking parser, RAG, or LLM code."""
    public_progress = dict(progress)
    app.state.live_collection_progress = public_progress
    return public_progress


__all__ = [
    "activate_snapshot_state",
    "active_snapshot_id",
    "begin_rag_candidate_build",
    "can_reuse_previous_rag_index_after_failure",
    "cleanup_rag_snapshot_retention",
    "complete_rag_candidate_build",
    "fail_rag_candidate_build",
    "live_snapshot_refresh_gate",
    "next_live_refresh_delay_seconds",
    "publish_rag_candidate_index",
    "rag_ready_status",
    "record_rag_candidate_validation",
    "record_live_collection_progress",
    "record_live_refresh_attempt",
    "refresh_cooldown_seconds",
    "run_rag_quality_gate",
    "should_discard_rag_candidate_index",
    "validate_rag_candidate_documents",
    "validate_rag_candidate_index",
]
