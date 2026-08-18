"""Streaming helpers for evidence-grounded model synthesis."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from runtime_events import RuntimeEventEmitter


class ModelFirstTokenTimeout(asyncio.TimeoutError):
    """The model produced no public answer text within the silent-wait budget."""


class ModelStreamStartupError(RuntimeError):
    """The provider failed before publishing any public answer text."""


async def stream_with_first_token_watchdog(
    stream: Any,
    *,
    event_sink: RuntimeEventEmitter,
    step_id: str,
    subquery_id: str,
    first_token_timeout_seconds: float,
    progress_interval_seconds: float,
):
    """Yield model deltas while making silent reasoning time observable."""
    iterator = stream.__aiter__()
    first_delta_task = asyncio.create_task(anext(iterator))
    started_at = time.perf_counter()
    waiting_execution_emitted = False
    try:
        while not first_delta_task.done():
            elapsed = time.perf_counter() - started_at
            remaining = first_token_timeout_seconds - elapsed
            if remaining <= 0:
                raise ModelFirstTokenTimeout("model first public token timed out")
            done, _ = await asyncio.wait(
                {first_delta_task},
                timeout=min(progress_interval_seconds, remaining),
            )
            if done:
                break
            elapsed_seconds = max(1, int(time.perf_counter() - started_at))
            if not waiting_execution_emitted:
                await event_sink.execution(
                    step_id=step_id,
                    phase="generate",
                    status="running",
                    subquery_id=subquery_id,
                    title="模型正在组织回答",
                    detail="模型连接正常，正在等待首段公开文本；检索证据已经就绪。",
                    operation="synthesize.await_first_text",
                    parameters={
                        "first_token_timeout_seconds": first_token_timeout_seconds,
                    },
                    boundaries=["不会向页面发送尚未通过证据校验的模型草稿。"],
                )
                waiting_execution_emitted = True
            else:
                await event_sink.progress("generate", f"模型仍在组织回答，已等待 {elapsed_seconds} 秒")
        first_delta = first_delta_task.result()
    except BaseException:
        if not first_delta_task.done():
            first_delta_task.cancel()
            await asyncio.gather(first_delta_task, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
        raise

    await event_sink.execution(
        step_id=step_id,
        phase="generate",
        status="running",
        subquery_id=subquery_id,
        title="已收到模型文本，正在逐句校验证据",
        detail="首段公开文本已经到达；通过数值与引用边界校验后会立即显示。",
        operation="synthesize.validate_stream",
        parameters={"elapsed_seconds": max(0, int(time.perf_counter() - started_at))},
    )
    yield first_delta
    async for delta in iterator:
        yield delta


__all__ = [
    "ModelFirstTokenTimeout",
    "ModelStreamStartupError",
    "stream_with_first_token_watchdog",
]
