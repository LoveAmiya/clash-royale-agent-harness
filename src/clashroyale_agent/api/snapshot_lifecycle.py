"""Behavior-preserving live snapshot refresh orchestration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SnapshotLifecycleDependencies:
    """Runtime dependencies for official snapshot restore and refresh."""

    data_dir: Path
    runtime_role: str
    live_data_enabled: bool
    external_api_required: bool
    api_token: str | None
    daily_target_battles: int
    daily_refresh_interval_seconds: float
    follower_poll_seconds: float
    client_factory: Callable[..., Any]
    client_timeout_seconds: float
    client_max_retries: int
    client_requests_per_second: float
    leaderboard_players: int
    seed_player_limit: int
    battles_per_player: int
    fetch_concurrency: int
    fallback_player_tags: tuple[str, ...]
    max_refresh_seconds: float
    progress_interval_seconds: float
    refresh_lock_factory: Callable[[], Any]
    is_path_of_legend_snapshot: Callable[[dict | None], bool]
    snapshot_refresh_due: Callable[[dict | None], bool]
    live_snapshot_refresh_gate: Callable[..., str]
    next_live_refresh_delay_seconds: Callable[..., float]
    refresh_cooldown_seconds: Callable[[int], int]
    is_complete_daily_snapshot: Callable[[dict], bool]
    publish_daily_snapshot: Callable[[dict, Path], dict]
    load_published_snapshot: Callable[[Path], dict | None]
    load_published_snapshot_summary: Callable[[Path], dict | None]
    snapshot_age_seconds: Callable[[dict | None], float | None]
    preheat_retriever: Callable[..., Any | None]
    preheat_retriever_in_background: Callable[..., Any]
    activate_snapshot_state: Callable[[Any, dict], None]
    active_snapshot_id: Callable[[Any], str | None]
    record_live_refresh_attempt: Callable[..., None]
    record_live_collection_progress: Callable[[Any, dict], Any]
    logger: Any
    now_monotonic: Callable[[], float] = time.monotonic
    now_utc: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )


def restore_published_snapshot(app: Any, *, dependencies: SnapshotLifecycleDependencies) -> dict | None:
    """Restore the last complete published snapshot before scheduling refresh."""
    snapshot = (
        dependencies.load_published_snapshot_summary(dependencies.data_dir)
        if dependencies.runtime_role == "collector"
        else dependencies.load_published_snapshot(dependencies.data_dir)
    )
    if snapshot is None:
        return None
    app.state.live_snapshot = snapshot
    app.state.rag_document_validation = snapshot.get("rag_document_validation")
    age_seconds = dependencies.snapshot_age_seconds(snapshot)
    app.state.live_snapshot_at = dependencies.now_monotonic() - (age_seconds or 0.0)
    app.state.live_snapshot_target_battles = dependencies.daily_target_battles
    app.state.cards_meta_data = list(snapshot.get("cards_meta", []))
    app.state.top_decks_data = list(snapshot.get("top_decks", []))
    app.state.card_deck_stats_data = dict(snapshot.get("card_deck_stats", {}))
    app.state.live_error = None
    app.state.live_refresh_status = (
        "ready" if not dependencies.snapshot_refresh_due(snapshot) else "stale"
    )
    dependencies.record_live_refresh_attempt(
        app,
        status="restored",
        snapshot=snapshot,
        finished_at=snapshot.get("published_at") or snapshot.get("fetched_at"),
    )
    dependencies.logger.info(
        "restored official weekly snapshot id=%s battles=%s age_seconds=%.1f",
        snapshot.get("snapshot_id"),
        snapshot.get("sample_battles"),
        age_seconds or 0.0,
    )
    return snapshot


def ensure_live_snapshot(app: Any, *, dependencies: SnapshotLifecycleDependencies) -> dict | None:
    """Refresh one complete official snapshot while preserving the last good cache."""
    if not dependencies.live_data_enabled or not dependencies.api_token:
        app.state.live_refresh_status = (
            "unavailable" if dependencies.external_api_required else "missing"
        )
        return None

    target_battles = dependencies.daily_target_battles
    cached = getattr(app.state, "live_snapshot", None)
    legacy_scope_refresh = (
        dependencies.runtime_role == "collector"
        and not dependencies.is_path_of_legend_snapshot(cached)
    )
    refresh_gate = dependencies.live_snapshot_refresh_gate(
        now_monotonic=dependencies.now_monotonic(),
        cooldown_until=getattr(app.state, "live_cooldown_until", 0.0),
        cached_present=cached is not None,
        legacy_scope_refresh=legacy_scope_refresh,
        refresh_due=dependencies.snapshot_refresh_due(cached) if cached is not None else True,
    )
    if refresh_gate == "cooldown":
        app.state.live_refresh_status = "cooldown"
        return getattr(app.state, "live_snapshot", None)
    if refresh_gate == "cached":
        return cached

    refresh_lock = getattr(app.state, "live_refresh_lock", None)
    if refresh_lock is None:
        refresh_lock = dependencies.refresh_lock_factory()
        app.state.live_refresh_lock = refresh_lock
    if not refresh_lock.acquire(blocking=False):
        if cached is not None:
            return cached
        with refresh_lock:
            return getattr(app.state, "live_snapshot", None)

    try:
        app.state.live_refresh_status = "refreshing"
        app.state.live_collection_progress = {
            "status": "starting",
            "target_battles": target_battles,
            "usable_battles": 0,
            "updated_at": dependencies.now_utc().isoformat(),
        }
        if cached is None:
            app.state.rag_status = "not_ready"
        client = dependencies.client_factory(
            dependencies.api_token,
            timeout_seconds=dependencies.client_timeout_seconds,
            max_retries=dependencies.client_max_retries,
            requests_per_second=dependencies.client_requests_per_second,
        )
        snapshot = client.fetch_snapshot(
            target_battles=target_battles,
            player_limit=dependencies.leaderboard_players,
            seed_player_limit=dependencies.seed_player_limit,
            battles_per_player=dependencies.battles_per_player,
            concurrency=dependencies.fetch_concurrency,
            fallback_player_tags=dependencies.fallback_player_tags,
            max_duration_seconds=dependencies.max_refresh_seconds,
            progress_callback=lambda progress: dependencies.record_live_collection_progress(
                app, progress
            ),
            progress_interval_seconds=dependencies.progress_interval_seconds,
            spool_dir=dependencies.data_dir / "snapshot_work",
        )
        if not dependencies.is_complete_daily_snapshot(snapshot):
            app.state.live_error = (
                "IncompleteOfficialSnapshot: "
                f"sample_battles={snapshot.get('sample_battles')} "
                f"target_battles={target_battles}"
            )
            source_exhausted = bool(snapshot.get("collection_metrics", {}).get("source_exhausted"))
            if source_exhausted:
                app.state.live_refresh_status = "source_exhausted"
                app.state.live_cooldown_until = (
                    dependencies.now_monotonic() + dependencies.daily_refresh_interval_seconds
                )
            else:
                failures = getattr(app.state, "live_refresh_failures", 0) + 1
                app.state.live_refresh_failures = failures
                app.state.live_cooldown_until = (
                    dependencies.now_monotonic()
                    + dependencies.refresh_cooldown_seconds(failures)
                )
                app.state.live_refresh_status = "cooldown"
            dependencies.record_live_refresh_attempt(
                app,
                status="source_exhausted" if source_exhausted else "incomplete",
                snapshot=snapshot,
                error=app.state.live_error,
            )
            dependencies.logger.warning(
                "discarded incomplete official weekly snapshot %s",
                app.state.live_error,
            )
            return cached

        snapshot = dependencies.publish_daily_snapshot(snapshot, dependencies.data_dir)
        if dependencies.runtime_role != "collector" and snapshot.get("raw_battles_storage"):
            snapshot = dependencies.load_published_snapshot(dependencies.data_dir)
            if snapshot is None:
                raise ValueError("streamed snapshot publication could not be reloaded")
        if dependencies.runtime_role == "collector":
            dependencies.activate_snapshot_state(app, snapshot)
        else:
            candidate = dependencies.preheat_retriever(
                app,
                candidate_snapshot=snapshot,
                activate_snapshot=True,
            )
            if candidate is None:
                app.state.live_error = "RAGActivationFailed"
                app.state.live_refresh_status = "stale" if cached is not None else "unavailable"
                dependencies.record_live_refresh_attempt(
                    app,
                    status="rag_activation_failed",
                    snapshot=snapshot,
                    error=app.state.live_error,
                )
                return cached
        app.state.live_error = None
        dependencies.record_live_refresh_attempt(app, status="success", snapshot=snapshot)
        if snapshot.get("collection_metrics", {}).get("rate_limited", 0):
            failures = getattr(app.state, "live_refresh_failures", 0) + 1
            app.state.live_refresh_failures = failures
            cooldown_seconds = dependencies.refresh_cooldown_seconds(failures)
            app.state.live_cooldown_until = dependencies.now_monotonic() + cooldown_seconds
            app.state.live_refresh_status = "cooldown"
        else:
            app.state.live_refresh_status = "ready"
            app.state.live_refresh_failures = 0
        dependencies.logger.info(
            "official live snapshot refreshed battles=%s target=%s players=%s failed=%s",
            snapshot.get("sample_battles"),
            snapshot.get("target_battles"),
            snapshot.get("sampled_players"),
            snapshot.get("failed_players"),
        )
        return snapshot
    except Exception as exc:
        if isinstance(exc, ValueError):
            app.state.live_error = f"ValueError: {str(exc)[:240]}"
        else:
            app.state.live_error = type(exc).__name__
        dependencies.logger.warning("official live snapshot refresh failed: %s", exc)
        failures = getattr(app.state, "live_refresh_failures", 0) + 1
        app.state.live_refresh_failures = failures
        cooldown_seconds = dependencies.refresh_cooldown_seconds(failures)
        app.state.live_cooldown_until = dependencies.now_monotonic() + cooldown_seconds
        app.state.live_refresh_status = "cooldown"
        dependencies.record_live_refresh_attempt(app, status="failed", error=app.state.live_error)
        return cached
    finally:
        refresh_lock.release()


async def refresh_live_snapshot_loop(
    app: Any, *, dependencies: SnapshotLifecycleDependencies
) -> None:
    """Load once, then refresh a complete official dataset every week."""
    while True:
        snapshot = await asyncio.to_thread(ensure_live_snapshot, app, dependencies=dependencies)
        snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
        snapshot_fingerprint = (
            snapshot.get("rag_docs_fingerprint") if isinstance(snapshot, dict) else None
        )
        if dependencies.runtime_role != "collector" and snapshot_id and (
            snapshot_id != getattr(app.state, "rag_snapshot_id", None)
            or snapshot_fingerprint != getattr(app.state, "rag_docs_fingerprint", None)
        ):
            await dependencies.preheat_retriever_in_background(app)
        delay = dependencies.next_live_refresh_delay_seconds(
            refresh_status=getattr(app.state, "live_refresh_status", None),
            cooldown_until=getattr(app.state, "live_cooldown_until", 0.0),
            now_monotonic=dependencies.now_monotonic(),
            snapshot_present=isinstance(snapshot, dict),
            snapshot_age_seconds=(
                dependencies.snapshot_age_seconds(snapshot)
                if isinstance(snapshot, dict)
                else None
            ),
            refresh_interval_seconds=dependencies.daily_refresh_interval_seconds,
        )
        await asyncio.sleep(delay)


async def follow_published_snapshot_loop(
    app: Any, *, dependencies: SnapshotLifecycleDependencies
) -> None:
    """Reload atomically published snapshots without contacting Supercell."""
    while True:
        published = await asyncio.to_thread(
            dependencies.load_published_snapshot, dependencies.data_dir
        )
        published_id = published.get("snapshot_id") if isinstance(published, dict) else None
        published_fingerprint = (
            published.get("rag_docs_fingerprint") if isinstance(published, dict) else None
        )
        if published_id and (
            published_id != dependencies.active_snapshot_id(app)
            or published_fingerprint != getattr(app.state, "rag_docs_fingerprint", None)
        ):
            await dependencies.preheat_retriever_in_background(
                app,
                candidate_snapshot=published,
                activate_snapshot=True,
            )
        await asyncio.sleep(dependencies.follower_poll_seconds)


async def refresh_live_snapshot_once(
    app: Any, *, dependencies: SnapshotLifecycleDependencies
) -> None:
    """Refresh after a settings change, retrying once if another refresh held the lock."""
    snapshot = await asyncio.to_thread(ensure_live_snapshot, app, dependencies=dependencies)
    if snapshot is None and getattr(app.state, "live_snapshot", None) is None:
        await asyncio.to_thread(ensure_live_snapshot, app, dependencies=dependencies)


__all__ = [
    "SnapshotLifecycleDependencies",
    "ensure_live_snapshot",
    "follow_published_snapshot_loop",
    "refresh_live_snapshot_loop",
    "refresh_live_snapshot_once",
    "restore_published_snapshot",
]
