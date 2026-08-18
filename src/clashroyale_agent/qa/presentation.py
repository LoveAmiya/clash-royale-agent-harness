"""Presentation helpers for streaming QA/RAG answer text."""

from __future__ import annotations

import asyncio

from runtime_events import RuntimeEventEmitter


FALLBACK_CONTENT_INTERVAL_SECONDS = 0.12
DEFAULT_CONTENT_CHUNK_SIZE = 80


def chunk_text(text: str, *, chunk_size: int = DEFAULT_CONTENT_CHUNK_SIZE) -> list[str]:
    """Split public answer text into stable frontend-sized chunks."""
    if not text:
        return []
    return [text[start : start + chunk_size] for start in range(0, len(text), chunk_size)]


async def emit_chunked_content(
    event_sink: RuntimeEventEmitter,
    text: str,
    *,
    chunk_size: int = DEFAULT_CONTENT_CHUNK_SIZE,
    interval_seconds: float = FALLBACK_CONTENT_INTERVAL_SECONDS,
) -> None:
    """Emit fallback answer text in the same chunked cadence used by runtime QA."""
    chunks = chunk_text(text, chunk_size=chunk_size)
    for index, chunk in enumerate(chunks):
        await event_sink.content(chunk, delta=True)
        if index < len(chunks) - 1:
            await asyncio.sleep(interval_seconds)


__all__ = [
    "DEFAULT_CONTENT_CHUNK_SIZE",
    "FALLBACK_CONTENT_INTERVAL_SECONDS",
    "chunk_text",
    "emit_chunked_content",
]
