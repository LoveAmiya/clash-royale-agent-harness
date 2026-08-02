"""Anonymous inputs for parser, routing, and deterministic contract checks."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_ALIAS_FILE = ROOT / "data" / "card_aliases.zh-CN.json"


@lru_cache(maxsize=1)
def _card_catalog() -> tuple[dict, ...]:
    payload = json.loads(CARD_ALIAS_FILE.read_text(encoding="utf-8"))
    cards = payload.get("cards", {})
    return tuple(
        {
            "rank": index,
            "card_name": name,
            "aliases": entry.get("aliases", []),
            "usage_rate": round(5.0 + index / 10, 2),
            "win_rate": round(50.0 + (index % 8), 2),
            "clean_win_rate": round(50.0 + (index % 8), 2),
            "rating": 1000 + index,
            "usage_delta": 0.0,
            "win_delta": 0.0,
            "mode": "contract-test",
            "source": "unit-test fixture",
        }
        for index, (name, entry) in enumerate(cards.items(), start=1)
        if isinstance(name, str) and isinstance(entry, dict)
    )


def sample_cards() -> list[dict]:
    return deepcopy(list(_card_catalog()))


def sample_decks() -> list[dict]:
    return [
        {
            "rank": 1,
            "deck_name": "Electro Giant Control",
            "cards": ["Electro Giant", "Lightning", "Tornado", "Fireball"],
            "usage_rate": 12.5,
            "win_rate": 56.0,
            "sample_win_rate": 56.0,
            "battles": 200,
            "avg_elixir": 3.8,
            "source": "unit-test fixture",
        },
        {
            "rank": 2,
            "deck_name": "Hog Rider Cycle",
            "cards": ["Hog Rider", "Fireball", "The Log", "Skeletons"],
            "usage_rate": 10.0,
            "win_rate": 54.0,
            "sample_win_rate": 54.0,
            "battles": 180,
            "avg_elixir": 2.9,
            "source": "unit-test fixture",
        },
        {
            "rank": 3,
            "deck_name": "Poison Control",
            "cards": ["Poison", "Miner", "Knight", "Zap"],
            "usage_rate": 8.0,
            "win_rate": 52.0,
            "sample_win_rate": 52.0,
            "battles": 160,
            "avg_elixir": 3.1,
            "source": "unit-test fixture",
        },
    ]


def sample_schedule() -> list[dict]:
    return [
        {
            "round": round_number,
            "match_date": f"2026-05-{17 + round_number:02d}",
            "team_name": "Test Team",
            "player_name": "TBD",
            "opponent_team": "Test Opponent",
            "opponent_player": "TBD",
            "status": "upcoming",
            "note": "unit-test fixture",
        }
        for round_number in range(1, 12)
    ]
