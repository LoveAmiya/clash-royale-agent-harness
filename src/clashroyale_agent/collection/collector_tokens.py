"""Environment token parsing for isolated rolling collector lanes."""

from __future__ import annotations

import json
import os
import re


def parse_api_tokens(raw: str, error_type: type[ValueError]) -> tuple[str, ...]:
    value = str(raw or "").strip()
    if not value:
        return ()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise error_type("SUPERCELL_API_TOKENS must be valid JSON or separated text") from exc
        if not isinstance(parsed, list):
            raise error_type("SUPERCELL_API_TOKENS JSON value must be an array")
        return tuple(str(item).strip() for item in parsed if str(item).strip())
    return tuple(item.strip() for item in re.split(r"[,;\r\n]+", value) if item.strip())


def resolve_api_token(mode: str, *, token_slot_by_mode: dict[str, int], error_type: type[ValueError]) -> str:
    index = token_slot_by_mode[mode]
    tokens = parse_api_tokens(os.getenv("SUPERCELL_API_TOKENS", ""), error_type)
    legacy = os.getenv("SUPERCELL_API_TOKEN", "").strip()
    if len(tokens) == 1 and legacy and tokens[0] != legacy:
        tokens = (legacy, tokens[0])
    if not tokens and mode == "daily_ranked" and legacy:
        return legacy
    if len(tokens) <= index:
        lane = "second token for weekly_expanded" if mode == "weekly_expanded" else "first token for daily_ranked"
        raise error_type(f"SUPERCELL_API_TOKENS requires a {lane}")
    return tokens[index]


__all__ = ["parse_api_tokens", "resolve_api_token"]
