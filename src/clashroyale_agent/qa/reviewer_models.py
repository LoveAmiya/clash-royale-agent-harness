"""Reviewer model construction helpers for QA/RAG synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ReviewerModelConfig:
    model_name: str
    client_kwargs: dict[str, Any]
    reasoning_effort: str
    wire_api: str


def build_reviewer_model(
    api_key: str,
    *,
    config: ReviewerModelConfig,
    chat_model_cls: Callable[..., Any],
    response_model_cls: Callable[..., Any],
) -> Any:
    """Build the configured non-streaming reviewer model."""
    common_kwargs = {
        "model_name": config.model_name,
        "api_key": api_key,
        "stream": False,
        "client_kwargs": config.client_kwargs,
    }
    if config.wire_api == "responses":
        return response_model_cls(
            **common_kwargs,
            reasoning_effort=config.reasoning_effort,
        )
    if config.wire_api == "chat_completions":
        return chat_model_cls(
            **common_kwargs,
            reasoning_effort=config.reasoning_effort,
        )
    raise ValueError(f"Unsupported OPENAI_WIRE_API: {config.wire_api}")


__all__ = ["ReviewerModelConfig", "build_reviewer_model"]
