"""Pure parser payload and scalar normalization helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from clashroyale_agent.qa.ranking import extract_cn_number


def normalize_text(text: str) -> str:
    return text.strip().lower()


def extract_text_content(result: Any) -> str:
    if hasattr(result, "get_text_content"):
        return result.get_text_content()
    return str(result)


def extract_json_block(text: str) -> dict | None:
    """Extract one JSON object from otherwise unstructured model output."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def coerce_round_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return cn_number

    return extract_round_number(stripped)


def extract_round_number(question: str) -> int | None:
    patterns = [
        r"第\s*(\d+)\s*轮",
        r"round\s*(\d+)",
        r"\br\s*(\d+)\b",
    ]
    query = question.lower()
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return int(match.group(1))

    chinese_match = re.search(r"第\s*([一二两三四五六七八九十]+)\s*轮", question)
    if chinese_match:
        return extract_cn_number(chinese_match.group(1))
    return None


def extract_date(question: str) -> str | None:
    iso_match = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", question)
    if iso_match:
        year, month, day = iso_match.group(1).split("-")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    month_day_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", question)
    if month_day_match:
        month = int(month_day_match.group(1))
        day = int(month_day_match.group(2))
        return f"2026-{month:02d}-{day:02d}"
    return None


__all__ = [
    "coerce_round_value",
    "extract_date",
    "extract_json_block",
    "extract_round_number",
    "extract_text_content",
    "normalize_text",
]
