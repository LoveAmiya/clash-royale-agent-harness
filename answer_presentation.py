"""Deterministic presentation rules for user-visible Clash Royale answers."""

from __future__ import annotations

import re
from functools import lru_cache

from query_parser import CARD_ALIAS_OVERRIDES, CARD_FORM_CATALOG


SECTION_LABELS = {
    "conclusion": "结论",
    "data evidence": "数据依据",
    "evidence": "数据依据",
    "data boundaries": "数据边界",
    "data boundary": "数据边界",
    "boundaries": "数据边界",
    "references": "参考来源",
    "sources": "参考来源",
}


def card_display_names() -> dict[str, str]:
    names = {
        canonical: aliases[0]
        for canonical, aliases in CARD_ALIAS_OVERRIDES.items()
        if aliases and str(aliases[0]).strip()
    }
    for form_name in CARD_FORM_CATALOG:
        if form_name.endswith(" Evolution"):
            base_name = form_name.removesuffix(" Evolution")
            if base_name in names:
                names[form_name] = f"进化{names[base_name]}"
        elif form_name.startswith("Hero "):
            base_name = form_name.removeprefix("Hero ")
            if base_name in names:
                names[form_name] = f"英雄{names[base_name]}"
    return names


def answer_name_replacements() -> dict[str, str]:
    """Map canonical English names and unambiguous Chinese aliases to display names."""
    display_names = card_display_names()
    replacements: dict[str, str] = {}
    for canonical, display_name in display_names.items():
        replacements[canonical] = display_name
        replacements[display_name] = display_name
    for canonical, aliases in CARD_ALIAS_OVERRIDES.items():
        display_name = display_names.get(canonical)
        if not display_name:
            continue
        for alias in aliases[1:]:
            normalized = str(alias or "").strip()
            if len(normalized) >= 2 and re.search(r"[\u3400-\u9fff]", normalized):
                replacements.setdefault(normalized, display_name)
    return replacements


@lru_cache(maxsize=1)
def _card_name_pattern() -> re.Pattern[str]:
    names = sorted(answer_name_replacements(), key=len, reverse=True)
    expression = "|".join(re.escape(name) for name in names)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{expression})(?![A-Za-z0-9])", re.IGNORECASE)


def localize_card_names(text: str) -> str:
    """Replace canonical English card names with the editable Chinese display names."""
    if not text:
        return text
    replacements = answer_name_replacements()
    casefold_names = {name.casefold(): display for name, display in replacements.items()}
    return _card_name_pattern().sub(
        lambda match: casefold_names.get(match.group(0).casefold(), match.group(0)),
        text,
    )


def _normalize_section_line(line: str) -> str:
    without_heading = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
    key = without_heading.rstrip(":：").strip().casefold()
    if key in SECTION_LABELS:
        return SECTION_LABELS[key]
    return without_heading if without_heading != line.strip() else line


def normalize_answer_text(text: str) -> str:
    """Return plain frontend-safe text with standard Chinese card names."""
    if not text:
        return text
    normalized_lines = [_normalize_section_line(line) for line in str(text).splitlines()]
    normalized = "\n".join(normalized_lines)
    normalized = re.sub(r"(?m)^\s*\*\s+", "- ", normalized)
    normalized = normalized.replace("*", "").replace("__", "")
    normalized = localize_card_names(normalized)
    return normalized.strip()
