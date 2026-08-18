"""Message helpers for the process API contract."""

from __future__ import annotations

from clashroyale_agent.api.schemas import ProcessRequest


def get_user_text(request: ProcessRequest) -> str:
    """Return the first user text block from a process request."""
    for message in request.input:
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if block.get("type") == "text":
                return str(block.get("text", "")).strip()
    return ""


__all__ = ["get_user_text"]
