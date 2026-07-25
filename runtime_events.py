"""Request-scoped events used to stream auditable runtime progress over SSE."""

from __future__ import annotations

import asyncio
import time
from typing import Any


class RuntimeEventEmitter:
    """Small request-local queue; it is not a cross-request event bus."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.content_count = 0

    async def next_event(self) -> dict[str, Any]:
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    async def progress(self, stage: str, label: str) -> None:
        await self._queue.put(
            {
                "object": "progress",
                "status": "in_progress",
                "stage": stage,
                "label": label,
            }
        )

    async def execution(
        self,
        *,
        step_id: str,
        phase: str,
        status: str,
        title: str,
        detail: str,
        subquery_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "object": "execution",
            "step_id": step_id,
            "phase": phase,
            "status": status,
            "title": title,
            "detail": detail,
            "timestamp": int(time.time() * 1000),
            "replace": True,
        }
        if subquery_id:
            payload["subquery_id"] = subquery_id
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        await self._queue.put(payload)

    async def content(self, text: str, *, delta: bool = True) -> None:
        if not text:
            return
        self.content_count += 1
        await self._queue.put(
            {
                "object": "content",
                "type": "text",
                "text": text,
                "delta": delta,
            }
        )
