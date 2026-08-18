"""Small SSE helpers for the runtime split."""

from __future__ import annotations

import json


def format_sse_data(payload: dict) -> str:
    """Serialize one server-sent event data frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_stream_chunks(text: str, chunk_size: int = 80):
    """Split fallback text into stable chunks for progressive display."""
    for start in range(0, len(text), chunk_size):
        yield text[start : start + chunk_size]


__all__ = ["format_sse_data", "split_stream_chunks"]
