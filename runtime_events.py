"""Request-scoped events used to stream auditable runtime progress over SSE."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any


class RuntimeEventEmitter:
    """Small request-local queue; it is not a cross-request event bus."""

    _SPAN_NAMES = {
        "parse": "parser",
        "route": "parser",
        "retrieve": "retrieval",
        "retrieval": "retrieval",
        "rerank": "rerank",
        "synthesize": "synthesis",
        "generate": "synthesis",
        "review": "review",
        "validate": "validation",
        "validation": "validation",
    }
    _ATTRIBUTE_KEYS = {
        "snapshot_group_id",
        "snapshot_id",
        "dataset_scope",
        "deck_mode",
        "entity_mode",
        "model",
        "prompt_hash",
    }

    def __init__(
        self,
        request_id: str | None = None,
        *,
        question: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.content_count = 0
        self.request_id = request_id
        self.trace_id = request_id
        self.telemetry = {
            key: value
            for key, value in (attributes or {}).items()
            if key in self._ATTRIBUTE_KEYS and value is not None
        }
        if question is not None:
            normalized = str(question)
            self.telemetry["question_hash"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            self.telemetry["question_length"] = len(normalized)

    def _decorate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.request_id:
            payload["request_id"] = self.request_id
        return payload

    async def next_event(self) -> dict[str, Any]:
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    async def progress(self, stage: str, label: str) -> None:
        await self._queue.put(
            self._decorate({
                "object": "progress",
                "status": "in_progress",
                "stage": stage,
                "label": label,
            })
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
            "span_name": self._SPAN_NAMES.get(phase, phase),
        }
        if self.trace_id:
            payload["trace_id"] = self.trace_id
        if self.telemetry:
            payload["telemetry"] = dict(self.telemetry)
        if subquery_id:
            payload["subquery_id"] = subquery_id
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        await self._queue.put(self._decorate(payload))

    async def content(self, text: str, *, delta: bool = True) -> None:
        if not text:
            return
        self.content_count += 1
        await self._queue.put(
            self._decorate({
                "object": "content",
                "type": "text",
                "text": text,
                "delta": delta,
            })
        )
