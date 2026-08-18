"""RAG preheat execution helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RAGPreheatTarget:
    """Validated snapshot target for one RAG preheat attempt."""

    snapshot: dict
    snapshot_id: str


def acquire_rag_preheat_lock(
    app: Any,
    *,
    lock_factory: Callable[[], Any],
) -> Any | None:
    """Create and acquire the per-runtime RAG preheat lock without blocking."""
    lock = getattr(app.state, "rag_preheat_lock", None)
    if lock is None:
        lock = lock_factory()
        app.state.rag_preheat_lock = lock
    if not lock.acquire(blocking=False):
        return None
    return lock


def resolve_rag_preheat_target(
    app: Any,
    *,
    candidate_snapshot: dict | None = None,
) -> RAGPreheatTarget | None:
    """Resolve the candidate snapshot and preserve legacy not-ready behavior."""
    target_snapshot = (
        candidate_snapshot if isinstance(candidate_snapshot, dict) else getattr(app.state, "live_snapshot", None)
    )
    snapshot_id = target_snapshot.get("snapshot_id") if isinstance(target_snapshot, dict) else None
    if not snapshot_id:
        app.state.rag_status = "not_ready"
        return None
    return RAGPreheatTarget(snapshot=target_snapshot, snapshot_id=snapshot_id)


def find_reusable_rag_retriever(
    app: Any,
    *,
    snapshot_id: str,
    docs_fingerprint: str,
    activate_snapshot: bool,
) -> Any | None:
    """Return the active retriever only when it already matches the candidate boundary."""
    if activate_snapshot:
        return None
    existing = getattr(app.state, "retriever", None)
    if existing is None:
        return None
    if getattr(app.state, "rag_snapshot_id", None) != snapshot_id:
        return None
    if getattr(app.state, "rag_docs_fingerprint", None) != docs_fingerprint:
        return None
    if getattr(existing, "docs_fingerprint", None) != docs_fingerprint:
        return None
    return existing


def find_active_rag_retriever(
    app: Any,
    *,
    active_snapshot_id: str | None,
) -> Any | None:
    """Return the current retriever only when it matches the active evidence boundary."""
    retriever = getattr(app.state, "retriever", None)
    if retriever is None:
        return None
    if getattr(app.state, "rag_snapshot_id", None) != active_snapshot_id:
        return None
    if getattr(app.state, "rag_status", None) not in {"ready", "bm25_only"}:
        return None
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_fingerprint = snapshot.get("rag_docs_fingerprint") if isinstance(snapshot, dict) else None
    active_fingerprint = getattr(app.state, "rag_docs_fingerprint", None)
    if not snapshot_fingerprint or snapshot_fingerprint != active_fingerprint:
        return None
    if getattr(retriever, "docs_fingerprint", None) != snapshot_fingerprint:
        return None
    return retriever


async def run_rag_preheat_in_thread(
    preheat: Callable[..., Any],
    app: Any,
    *,
    candidate_snapshot: dict | None = None,
    activate_snapshot: bool = False,
) -> None:
    """Run a blocking RAG preheat function without blocking the event loop."""
    await asyncio.to_thread(
        preheat,
        app,
        candidate_snapshot=candidate_snapshot,
        activate_snapshot=activate_snapshot,
    )


__all__ = [
    "RAGPreheatTarget",
    "acquire_rag_preheat_lock",
    "find_active_rag_retriever",
    "find_reusable_rag_retriever",
    "resolve_rag_preheat_target",
    "run_rag_preheat_in_thread",
]
