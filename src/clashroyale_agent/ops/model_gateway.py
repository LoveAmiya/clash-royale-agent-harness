"""OpenAI-compatible model gateway used by parsing and evidence synthesis."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator

from app_config import (
    MODEL_CIRCUIT_FAILURE_THRESHOLD,
    MODEL_CIRCUIT_RECOVERY_SECONDS,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    OPENAI_WIRE_API,
)
from clashroyale_agent.ops.model_resilience import (
    ModelProviderGuard,
    ModelStreamingUnavailableError,
)


def _provider_id() -> str:
    identity = f"{OPENAI_BASE_URL}|{OPENAI_MODEL}|{OPENAI_WIRE_API}".encode("utf-8")
    return f"provider-{hashlib.sha256(identity).hexdigest()[:12]}"


_provider_guard = ModelProviderGuard(
    provider_id=_provider_id(),
    failure_threshold=MODEL_CIRCUIT_FAILURE_THRESHOLD,
    recovery_seconds=MODEL_CIRCUIT_RECOVERY_SECONDS,
)


def uses_responses_api() -> bool:
    return OPENAI_WIRE_API == "responses"


async def generate_model_text_stream(
    *,
    api_key: str,
    instructions: str,
    input_text: str,
    reasoning_effort: str | None = None,
) -> AsyncIterator[str]:
    """Yield only public answer-text deltas from the configured model API."""
    from openai import AsyncOpenAI

    operation = "stream"
    _provider_guard.before_call(operation)
    # The runtime owns retry/fallback policy. SDK-level retries can otherwise
    # outlive the parser timeout and turn a 45-second bound into a much longer
    # request with no useful status signal.
    client = AsyncOpenAI(api_key=api_key, base_url=OPENAI_BASE_URL, max_retries=0)
    effort = reasoning_effort or OPENAI_REASONING_EFFORT
    emitted = False
    try:
        if uses_responses_api():
            stream = await client.responses.create(
                model=OPENAI_MODEL,
                instructions=instructions,
                input=input_text,
                reasoning={"effort": effort},
                store=False,
                stream=True,
            )
            async for event in stream:
                if getattr(event, "type", None) == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if isinstance(delta, str) and delta:
                        emitted = True
                        yield delta
        elif OPENAI_WIRE_API == "chat_completions":
            stream = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": input_text},
                ],
                reasoning_effort=effort,
                stream=True,
            )
            async for chunk in stream:
                choices = getattr(chunk, "choices", [])
                if not choices:
                    continue
                delta = getattr(getattr(choices[0], "delta", None), "content", "")
                if isinstance(delta, str) and delta:
                    emitted = True
                    yield delta
        else:
            raise ValueError(f"Unsupported OPENAI_WIRE_API: {OPENAI_WIRE_API}")
    except (asyncio.CancelledError, GeneratorExit):
        _provider_guard.record_cancelled(operation)
        raise
    except Exception as exc:
        _provider_guard.record_failure(operation, exc)
        raise

    if not emitted:
        _provider_guard.record_success(operation)
        _provider_guard.record_stream_capability(supported=False, reason="no_public_text_delta")
        raise ModelStreamingUnavailableError("model response contained no public text deltas")
    _provider_guard.record_success(operation)
    _provider_guard.record_stream_capability(supported=True)


async def generate_model_text(
    *,
    api_key: str,
    instructions: str,
    input_text: str,
    reasoning_effort: str | None = None,
) -> str:
    """Generate text through the configured OpenAI-compatible wire protocol.

    The Responses branch intentionally uses the native client. AgentScope's ReAct
    wrapper targets tool-use conversations and does not preserve strict parser JSON
    reliably through this relay.
    """
    from openai import AsyncOpenAI

    operation = "generate"
    _provider_guard.before_call(operation)
    client = AsyncOpenAI(api_key=api_key, base_url=OPENAI_BASE_URL, max_retries=0)
    effort = reasoning_effort or OPENAI_REASONING_EFFORT
    try:
        if uses_responses_api():
            response = await client.responses.create(
                model=OPENAI_MODEL,
                instructions=instructions,
                input=input_text,
                reasoning={"effort": effort},
                store=False,
            )
            text = (response.output_text or "").strip()
        elif OPENAI_WIRE_API == "chat_completions":
            response = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": input_text},
                ],
                reasoning_effort=effort,
            )
            text = (response.choices[0].message.content or "").strip()
        else:
            raise ValueError(f"Unsupported OPENAI_WIRE_API: {OPENAI_WIRE_API}")
    except asyncio.CancelledError:
        _provider_guard.record_cancelled(operation)
        raise
    except Exception as exc:
        _provider_guard.record_failure(operation, exc)
        raise
    _provider_guard.record_success(operation)
    return text


def get_model_provider_status() -> dict:
    return _provider_guard.snapshot()


def render_model_provider_metrics() -> str:
    return _provider_guard.render_prometheus()


def record_model_stream_mode(mode: str | None) -> None:
    _provider_guard.record_stream_mode(str(mode or "unavailable"))


def replace_model_provider_guard_for_tests(guard: ModelProviderGuard) -> ModelProviderGuard:
    global _provider_guard
    previous = _provider_guard
    _provider_guard = guard
    return previous


__all__ = [
    "generate_model_text",
    "generate_model_text_stream",
    "get_model_provider_status",
    "record_model_stream_mode",
    "render_model_provider_metrics",
    "replace_model_provider_guard_for_tests",
    "uses_responses_api",
]
