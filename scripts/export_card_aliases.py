"""Export the effective free-question card alias table for human review."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from query_parser import CARD_ALIASES, CARD_ALIAS_OVERRIDES, CARD_COMMUNITY_ALIASES


OUTPUT_PATH = ROOT / "data" / "card_aliases.zh-CN.json"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def build_payload() -> dict:
    card_names = sorted(
        set(CARD_ALIASES) | set(CARD_ALIAS_OVERRIDES) | set(CARD_COMMUNITY_ALIASES)
    )
    cards = {}
    for card_name in card_names:
        standard_names = list(CARD_ALIAS_OVERRIDES.get(card_name, []))
        display_name = standard_names[0] if standard_names else card_name
        aliases = _unique(
            [
                *standard_names[1:],
                *CARD_ALIASES.get(card_name, []),
                *CARD_COMMUNITY_ALIASES.get(card_name, []),
            ]
        )
        aliases = [value for value in aliases if value.casefold() != display_name.casefold()]
        cards[card_name] = {
            "display_name": display_name,
            "aliases": aliases,
        }
    return {
        "schema_version": 1,
        "language": "zh-CN",
        "description": "自由问答卡牌标准中文名与可接受别名。display_name 是回答和前端展示名；aliases 仅用于自然语言解析。",
        "cards": cards,
    }


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(build_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"exported {len(build_payload()['cards'])} cards to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
