"""Startup state helpers for the FastAPI runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


def initialize_runtime_data_state(
    app: Any,
    *,
    bootstrap_cards_meta_data: list[dict],
    live_sample_target_battles: int,
    lock_factory: Callable[[], Any],
) -> None:
    """Initialize in-memory snapshot, RAG, and live-refresh state."""
    app.state.schedule_data = []
    app.state.bootstrap_top_decks_data = []
    app.state.bootstrap_cards_meta_data = bootstrap_cards_meta_data
    app.state.top_decks_data = []
    app.state.cards_meta_data = list(app.state.bootstrap_cards_meta_data)
    app.state.card_deck_stats_data = {}
    app.state.retriever = None
    app.state.rolling_retriever = None
    app.state.rolling_retriever_group_id = None
    app.state.rolling_retriever_lock = lock_factory()
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
    app.state.rag_preheat_lock = lock_factory()
    app.state.rag_preheat_task = None
    app.state.api_startup_baseline = None
    app.state.rag_preheat_baseline = None
    app.state.live_snapshot = None
    app.state.live_snapshot_at = 0.0
    app.state.live_error = None
    app.state.live_sample_target_battles = live_sample_target_battles
    app.state.live_snapshot_target_battles = None
    app.state.live_refresh_lock = lock_factory()
    app.state.live_refresh_task = None
    app.state.live_refresh_status = "missing"
    app.state.live_battle_log_cache = {}
    app.state.live_cooldown_until = 0.0
    app.state.live_refresh_failures = 0
    app.state.live_last_refresh_attempt = None


def record_api_startup_baseline(
    app: Any,
    *,
    started_at: float,
    completed_at: float,
) -> None:
    """Record API lifecycle duration without changing startup control flow."""
    app.state.api_startup_baseline = {
        "elapsed_seconds": round(max(0.0, completed_at - started_at), 3),
    }


async def initialize_runtime_services(
    app: Any,
    *,
    runtime_metrics_factory: Callable[[], Any],
    recent_answer_cache_factory: Callable[..., Any],
    feedback_store_factory: Callable[..., Any],
    process_quota_factory: Callable[..., Any],
    feedback_db_file: Any,
    feedback_cache_max_items: int,
    feedback_cache_ttl_seconds: int,
    feedback_max_correction_chars: int,
    process_quota_backend: str,
    process_max_concurrent: int,
    process_rate_limit_per_minute: int,
    redis_url: str,
    process_quota_lease_seconds: int,
    process_quota_key_prefix: str,
    process_quota_fail_mode: str,
) -> None:
    """Initialize startup-owned service objects and validate quota backend health."""
    app.state.runtime_metrics = runtime_metrics_factory()
    app.state.recent_answers = recent_answer_cache_factory(
        max_items=feedback_cache_max_items,
        ttl_seconds=feedback_cache_ttl_seconds,
    )
    app.state.feedback_store = feedback_store_factory(
        feedback_db_file,
        max_correction_chars=feedback_max_correction_chars,
        answer_ttl_seconds=feedback_cache_ttl_seconds,
    )
    app.state.process_quota = process_quota_factory(
        backend=process_quota_backend,
        max_concurrent=process_max_concurrent,
        requests_per_minute=process_rate_limit_per_minute,
        redis_url=redis_url,
        lease_seconds=process_quota_lease_seconds,
        key_prefix=process_quota_key_prefix,
        fail_mode=process_quota_fail_mode,
    )
    await app.state.process_quota.probe()


async def _cancel_background_task(task: Any | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def shutdown_runtime_resources(app: Any) -> None:
    """Cancel background tasks and close resources owned by the runtime."""
    await _cancel_background_task(getattr(app.state, "live_refresh_task", None))
    await _cancel_background_task(getattr(app.state, "rag_preheat_task", None))

    rolling_retriever = getattr(app.state, "rolling_retriever", None)
    if rolling_retriever is not None:
        rolling_retriever.close()

    quota = getattr(app.state, "process_quota", None)
    if quota is not None:
        await quota.close()


__all__ = [
    "initialize_runtime_data_state",
    "initialize_runtime_services",
    "record_api_startup_baseline",
    "shutdown_runtime_resources",
]
