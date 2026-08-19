"""Model construction and committed card-catalog loading for runtime QA."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable


def load_json_file(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"娌℃湁鎵惧埌鏁版嵁鏂囦欢: {path.resolve()}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_card_catalog(path: Path, *, logger: logging.Logger) -> list[dict]:
    """Load the committed name catalog without treating it as snapshot metrics."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("card alias configuration is unavailable: %s", exc)
        return []
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(cards, dict):
        logger.warning("card alias configuration has an invalid cards object")
        return []
    return [
        {
            "card_name": name,
            "aliases": entry.get("aliases", []),
            "display_name": entry.get("display_name"),
        }
        for name, entry in cards.items()
        if isinstance(name, str) and name.strip() and isinstance(entry, dict)
    ]


def build_chat_model(
    api_key: str,
    *,
    model_name: str,
    client_kwargs: dict,
    wire_api: str,
    reasoning_effort: str,
    chat_model: type,
    response_model: type,
) -> Any:
    common_kwargs = {
        "model_name": model_name,
        "api_key": api_key,
        "stream": False,
        "client_kwargs": client_kwargs,
    }
    if wire_api == "responses":
        return response_model(**common_kwargs, reasoning_effort=reasoning_effort)
    if wire_api == "chat_completions":
        return chat_model(**common_kwargs, reasoning_effort=reasoning_effort)
    raise ValueError(f"Unsupported OPENAI_WIRE_API: {wire_api}")


def build_parser_agent(
    api_key: str,
    *,
    parser_system_prompt: str,
    build_model: Callable[[str], Any],
    agent_type: type,
    formatter_type: type,
    memory_type: type,
) -> Any:
    parser_agent = agent_type(
        name="Parser",
        sys_prompt=parser_system_prompt,
        model=build_model(api_key),
        formatter=formatter_type(),
        memory=memory_type(),
    )
    parser_agent.set_console_output_enabled(enabled=False)
    return parser_agent


__all__ = ["build_chat_model", "build_parser_agent", "load_card_catalog", "load_json_file"]
