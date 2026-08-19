from __future__ import annotations

import asyncio

from runtime_events import RuntimeEventEmitter


SEMANTIC_CONTENT_INTERVAL_SECONDS = 0.12


def split_answer_semantic_chunks(text: str):
    """Split deterministic answers at visible titles, lists, boundaries, and sources."""
    if not text:
        return
    chunks: list[str] = []
    current: list[str] = []
    current_kind = "paragraph"

    def flush() -> None:
        if current:
            chunks.append("".join(current))
            current.clear()

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            current.append(line)
            continue

        if stripped.startswith("## "):
            flush()
            current_kind = "heading"
        elif stripped.startswith("数据边界："):
            flush()
            current_kind = "boundary"
        elif stripped.startswith("参考来源："):
            flush()
            current_kind = "sources"
        elif stripped.startswith("**") and "请求指标" in stripped:
            flush()
            current_kind = "title"
        elif stripped.startswith(("- ", "* ", "+ ")):
            if current_kind != "list":
                flush()
                current_kind = "list"
        elif current_kind in {"heading", "title", "list"}:
            flush()
            current_kind = "paragraph"

        current.append(line)

    flush()
    yield from chunks


async def emit_semantic_content(event_sink: RuntimeEventEmitter, text: str) -> None:
    """Emit deterministic answer sections visibly, without simulating tokens."""
    chunks = list(split_answer_semantic_chunks(text))
    for index, chunk in enumerate(chunks):
        await event_sink.content(chunk, delta=True)
        if index < len(chunks) - 1:
            await asyncio.sleep(SEMANTIC_CONTENT_INTERVAL_SECONDS)


__all__ = [
    "SEMANTIC_CONTENT_INTERVAL_SECONDS",
    "emit_semantic_content",
    "split_answer_semantic_chunks",
]
