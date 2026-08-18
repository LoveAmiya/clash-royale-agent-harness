"""Rank and list-size parsing helpers for QA fallback parsing."""

from __future__ import annotations

import re
from typing import Any


CHINESE_NUM_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
    "三十": 30,
}


def extract_cn_number(text: str) -> int | None:
    return CHINESE_NUM_MAP.get(text)


def coerce_rank_value(value: Any, max_n: int = 30) -> int | None:
    """Convert model or user-provided ranking values into a safe 1..max_n integer."""
    if isinstance(value, int):
        return max(1, min(value, max_n))
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return max(1, min(int(stripped), max_n))

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return max(1, min(cn_number, max_n))

    return extract_rank_target(stripped, max_n=max_n)


def coerce_top_n_value(value: Any, max_n: int = 30) -> int | None:
    if isinstance(value, int):
        return max(1, min(value, max_n))
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return max(1, min(int(stripped), max_n))

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return max(1, min(cn_number, max_n))

    extracted = extract_top_n(stripped, default=max_n, max_n=max_n)
    if extracted == max_n and stripped not in {"前30", "三十"} and not any(ch.isdigit() for ch in stripped):
        return None
    return extracted


def extract_rank_target(question: str, max_n: int = 30) -> int | None:
    patterns = [
        r"第\s*(\d+)\s*名",
        r"排名\s*(\d+)",
        r"第\s*(\d+)\s*个",
        r"第\s*(\d+)(?!\s*轮)",
    ]
    for pattern in patterns:
        m = re.search(pattern, question)
        if m:
            return max(1, min(int(m.group(1)), max_n))

    cn_patterns = [
        r"第\s*([一二两三四五六七八九十]+)\s*名",
        r"排名\s*([一二两三四五六七八九十]+)",
        r"第\s*([一二两三四五六七八九十]+)\s*个",
        r"第\s*([一二两三四五六七八九十]+)(?!\s*轮)",
    ]
    for pattern in cn_patterns:
        m = re.search(pattern, question)
        if m:
            n = extract_cn_number(m.group(1))
            if n is not None:
                return max(1, min(n, max_n))

    return None


def extract_top_n(question: str, default: int | None = None, max_n: int = 30) -> int | None:
    ranked_count_patterns = [
        r"(?:最高|最多|最常见|最常用|排名靠前)(?:的)?\s*(\d+)\s*(?:张|个)?",
        r"(?:最高|最多|最常见|最常用|排名靠前)(?:的)?\s*([一二两三四五六七八九十]+)\s*(?:张|个)?",
    ]
    for pattern in ranked_count_patterns:
        match = re.search(pattern, question)
        if not match:
            continue
        value = int(match.group(1)) if match.group(1).isdigit() else extract_cn_number(match.group(1))
        if value is not None:
            return max(1, min(value, max_n))

    patterns = [
        r"前\s*(\d+)",
        r"给我看\s*(\d+)\s*个",
        r"来\s*(\d+)\s*个",
        r"\btop\s*(\d+)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, question)
        if m:
            return max(1, min(int(m.group(1)), max_n))

    cn_patterns = [
        r"前\s*([一二两三四五六七八九十]+)",
    ]
    for pattern in cn_patterns:
        m = re.search(pattern, question)
        if m:
            n = extract_cn_number(m.group(1))
            if n is not None:
                return max(1, min(n, max_n))

    if "几个" in question or "一些" in question:
        return min(5, max_n)

    if any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么"]):
        return min(5, max_n)

    return default


__all__ = [
    "CHINESE_NUM_MAP",
    "coerce_rank_value",
    "coerce_top_n_value",
    "extract_cn_number",
    "extract_rank_target",
    "extract_top_n",
]
