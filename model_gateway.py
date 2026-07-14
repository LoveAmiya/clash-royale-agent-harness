"""OpenAI-compatible model gateway used by parsing and evidence synthesis."""

from app_config import (
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    OPENAI_WIRE_API,
)


def uses_responses_api() -> bool:
    return OPENAI_WIRE_API == "responses"


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

    client = AsyncOpenAI(api_key=api_key, base_url=OPENAI_BASE_URL)
    effort = reasoning_effort or OPENAI_REASONING_EFFORT
    if uses_responses_api():
        response = await client.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=input_text,
            reasoning={"effort": effort},
            store=False,
        )
        return (response.output_text or "").strip()

    if OPENAI_WIRE_API == "chat_completions":
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_text},
            ],
            reasoning_effort=effort,
        )
        return (response.choices[0].message.content or "").strip()

    raise ValueError(f"Unsupported OPENAI_WIRE_API: {OPENAI_WIRE_API}")
