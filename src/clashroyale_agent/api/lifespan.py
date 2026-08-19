"""FastAPI runtime lifecycle orchestration."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from typing import Any, Callable


@dataclass(frozen=True)
class RuntimeLifespanDependencies:
    initialize_runtime_services: Callable[..., Any]
    initialize_runtime_data_state: Callable[..., Any]
    record_api_startup_baseline: Callable[..., Any]
    shutdown_runtime_resources: Callable[..., Any]
    runtime_metrics_factory: Callable[..., Any]
    recent_answer_cache_factory: Callable[..., Any]
    feedback_store_factory: Callable[..., Any]
    process_quota_factory: Callable[..., Any]
    feedback_db_file: Any
    feedback_cache_max_items: int
    feedback_cache_ttl_seconds: int
    feedback_max_correction_chars: int
    process_quota_backend: str
    process_max_concurrent: int
    process_rate_limit_per_minute: int
    redis_url: str
    process_quota_lease_seconds: int
    process_quota_key_prefix: str
    process_quota_fail_mode: str
    load_card_catalog: Callable[[], list[dict]]
    live_sample_target_battles: int
    lock_factory: Callable[[], Any]
    runtime_role: str
    supercell_live_data_enabled: bool
    supercell_api_token: str | None
    external_api_required: bool
    snapshot_auto_follow_enabled: bool
    restore_published_snapshot: Callable[[Any], Any]
    preheat_retriever_in_background: Callable[[Any], Any]
    follow_published_snapshot_loop: Callable[[Any], Any]
    refresh_live_snapshot_loop: Callable[[Any], Any]
    logger: Any


def build_runtime_lifespan_dependencies(dependencies_cls: Any, runtime: dict[str, Any]) -> Any:
    """Bind runtime lifespan providers from the compatibility namespace."""
    return dependencies_cls(
        initialize_runtime_services=runtime["initialize_runtime_services"],
        initialize_runtime_data_state=runtime["initialize_runtime_data_state"],
        record_api_startup_baseline=runtime["record_api_startup_baseline"],
        shutdown_runtime_resources=runtime["shutdown_runtime_resources"],
        runtime_metrics_factory=runtime["RuntimeMetrics"],
        recent_answer_cache_factory=runtime["RecentAnswerCache"],
        feedback_store_factory=runtime["FeedbackStore"],
        process_quota_factory=runtime["create_process_quota"],
        feedback_db_file=runtime["FEEDBACK_DB_FILE"],
        feedback_cache_max_items=runtime["FEEDBACK_CACHE_MAX_ITEMS"],
        feedback_cache_ttl_seconds=runtime["FEEDBACK_CACHE_TTL_SECONDS"],
        feedback_max_correction_chars=runtime["FEEDBACK_MAX_CORRECTION_CHARS"],
        process_quota_backend=runtime["PROCESS_QUOTA_BACKEND"],
        process_max_concurrent=runtime["PROCESS_MAX_CONCURRENT"],
        process_rate_limit_per_minute=runtime["PROCESS_RATE_LIMIT_PER_MINUTE"],
        redis_url=runtime["REDIS_URL"],
        process_quota_lease_seconds=runtime["PROCESS_QUOTA_LEASE_SECONDS"],
        process_quota_key_prefix=runtime["PROCESS_QUOTA_KEY_PREFIX"],
        process_quota_fail_mode=runtime["PROCESS_QUOTA_FAIL_MODE"],
        load_card_catalog=runtime["load_card_catalog"],
        live_sample_target_battles=runtime["SUPERCELL_TARGET_BATTLES"],
        lock_factory=runtime["threading"].Lock,
        runtime_role=runtime["RUNTIME_ROLE"],
        supercell_live_data_enabled=runtime["SUPERCELL_LIVE_DATA_ENABLED"],
        supercell_api_token=runtime["SUPERCELL_API_TOKEN"],
        external_api_required=runtime["EXTERNAL_API_REQUIRED"],
        snapshot_auto_follow_enabled=runtime["SNAPSHOT_AUTO_FOLLOW_ENABLED"],
        restore_published_snapshot=runtime["restore_published_snapshot"],
        preheat_retriever_in_background=runtime["preheat_retriever_in_background"],
        follow_published_snapshot_loop=runtime["follow_published_snapshot_loop"],
        refresh_live_snapshot_loop=runtime["refresh_live_snapshot_loop"],
        logger=runtime["logger"],
    )


@asynccontextmanager
async def runtime_lifespan(app: Any, dependencies: RuntimeLifespanDependencies):
    startup_started_at = time.perf_counter()
    app.state.initialized = False
    await dependencies.initialize_runtime_services(
        app,
        runtime_metrics_factory=dependencies.runtime_metrics_factory,
        recent_answer_cache_factory=dependencies.recent_answer_cache_factory,
        feedback_store_factory=dependencies.feedback_store_factory,
        process_quota_factory=dependencies.process_quota_factory,
        feedback_db_file=dependencies.feedback_db_file,
        feedback_cache_max_items=dependencies.feedback_cache_max_items,
        feedback_cache_ttl_seconds=dependencies.feedback_cache_ttl_seconds,
        feedback_max_correction_chars=dependencies.feedback_max_correction_chars,
        process_quota_backend=dependencies.process_quota_backend,
        process_max_concurrent=dependencies.process_max_concurrent,
        process_rate_limit_per_minute=dependencies.process_rate_limit_per_minute,
        redis_url=dependencies.redis_url,
        process_quota_lease_seconds=dependencies.process_quota_lease_seconds,
        process_quota_key_prefix=dependencies.process_quota_key_prefix,
        process_quota_fail_mode=dependencies.process_quota_fail_mode,
    )
    dependencies.initialize_runtime_data_state(
        app,
        bootstrap_cards_meta_data=dependencies.load_card_catalog(),
        live_sample_target_battles=dependencies.live_sample_target_battles,
        lock_factory=dependencies.lock_factory,
    )

    dependencies.logger.info(
        "startup complete schedule=%s decks=%s cards=%s retriever=lazy",
        len(app.state.schedule_data),
        len(app.state.top_decks_data),
        len(app.state.cards_meta_data),
    )
    if dependencies.runtime_role == "api":
        app.state.rag_status = "not_ready"
        dependencies.restore_published_snapshot(app)
        if getattr(app.state, "live_snapshot", None) is not None:
            app.state.rag_preheat_task = asyncio.create_task(
                dependencies.preheat_retriever_in_background(app)
            )
        if dependencies.snapshot_auto_follow_enabled:
            app.state.live_refresh_task = asyncio.create_task(
                dependencies.follow_published_snapshot_loop(app)
            )
        else:
            dependencies.logger.info("snapshot auto-follow disabled; API remains pinned until restart")
    elif dependencies.supercell_live_data_enabled and dependencies.supercell_api_token:
        app.state.rag_status = "not_ready"
        dependencies.restore_published_snapshot(app)
        if dependencies.runtime_role != "collector" and getattr(app.state, "live_snapshot", None) is not None:
            app.state.rag_status = "not_ready"
            app.state.rag_preheat_task = asyncio.create_task(
                dependencies.preheat_retriever_in_background(app)
            )
        app.state.live_refresh_task = asyncio.create_task(
            dependencies.refresh_live_snapshot_loop(app)
        )
    elif dependencies.external_api_required:
        app.state.live_refresh_status = "unavailable"
    app.state.initialized = True
    dependencies.record_api_startup_baseline(
        app,
        started_at=startup_started_at,
        completed_at=time.perf_counter(),
    )
    try:
        yield
    finally:
        await dependencies.shutdown_runtime_resources(app)


__all__ = ["RuntimeLifespanDependencies", "build_runtime_lifespan_dependencies", "runtime_lifespan"]
