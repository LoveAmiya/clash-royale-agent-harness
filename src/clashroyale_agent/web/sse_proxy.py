"""SSE payload and forwarding helpers for the browser UI."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_backend_payload(
    *,
    message: str,
    session_id: str,
    user_id: str,
    intent_hint: str | None,
    dataset_scope: str,
    deck_mode: str,
    entity_mode: str,
) -> dict:
    return {
        "session_id": session_id,
        "user_id": user_id,
        "intent_hint": intent_hint,
        "dataset_scope": dataset_scope,
        "deck_mode": deck_mode,
        "entity_mode": entity_mode,
        "input": [
            {
                "role": "user",
                "content": [{"type": "text", "text": message}],
            }
        ],
    }


async def stream_backend_sse(
    *,
    backend_url: str,
    backend_payload: dict,
    trust_env: bool,
    httpx_module: Any,
) -> AsyncIterator[str]:
    try:
        async with httpx_module.AsyncClient(timeout=None, trust_env=trust_env) as client:
            async with client.stream("POST", backend_url, json=backend_payload) as response:
                if response.status_code >= 400:
                    yield sse_data(
                        {
                            "object": "error",
                            "status": "failed",
                            "message": f"后端返回异常状态码：{response.status_code}",
                        }
                    )
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        yield f"{line}\n\n"
    except httpx_module.ConnectError:
        yield sse_data(
            {
                "object": "error",
                "status": "failed",
                "message": f"无法连接到后端：{backend_url}，请先启动后端。",
            }
        )
    except httpx_module.HTTPError as exc:
        yield sse_data(
            {
                "object": "error",
                "status": "failed",
                "message": f"转发后端流失败：{exc}",
            }
        )


__all__ = ["build_backend_payload", "sse_data", "stream_backend_sse"]
