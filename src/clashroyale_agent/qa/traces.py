"""Trace access helpers for QA answer execution."""

from __future__ import annotations

from typing import Any


def read_trace(trace_id: str | None, *, recorder: Any) -> list[dict[str, Any]]:
    """Read a trace from the injected recorder, preserving the empty-id contract."""
    if not trace_id:
        return []
    return recorder.read_trace(trace_id)


__all__ = ["read_trace"]
