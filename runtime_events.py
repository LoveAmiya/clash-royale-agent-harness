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
    _PARAMETER_KEYS = {
        "scope",
        "dataset_scope",
        "deck_mode",
        "entity_mode",
        "bm25_top_k",
        "dense_top_k",
        "final_top_k",
        "rerank_top_n",
        "evidence_count",
        "candidate_count",
        "fusion",
        "lanes",
        "mode",
        "effort",
        "stream",
        "timeout_seconds",
        "first_token_timeout_seconds",
        "elapsed_seconds",
        "model",
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
        self._event_sequence = 0
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
        operation: str | None = None,
        parameters: dict[str, Any] | None = None,
        rationale: str | None = None,
        evidence: list[str] | tuple[str, ...] | None = None,
        boundaries: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._event_sequence += 1
        payload: dict[str, Any] = {
            "object": "execution",
            "schema_version": 2,
            "event_id": f"{self.trace_id or 'execution'}:{self._event_sequence}",
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
        if operation:
            payload["operation"] = str(operation)[:120]
        if parameters:
            payload["parameters"] = {
                key: value
                for key, value in parameters.items()
                if key in self._PARAMETER_KEYS and value is not None
            }
        if rationale:
            payload["rationale"] = str(rationale)[:1000]
        if evidence:
            payload["evidence"] = [str(item)[:500] for item in evidence[:10]]
        if boundaries:
            payload["boundaries"] = [str(item)[:500] for item in boundaries[:10]]
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
