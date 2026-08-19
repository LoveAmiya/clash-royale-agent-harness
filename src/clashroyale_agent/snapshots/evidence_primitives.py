"""Pure formatting primitives used while deriving snapshot evidence."""

from __future__ import annotations

from collections import Counter
from typing import Callable


def with_snapshot_metadata(items: object, snapshot: dict) -> list[dict]:
    if not isinstance(items, list):
        return []
    snapshot_id = snapshot["snapshot_id"]
    fetched_at = snapshot.get("fetched_at")
    return [
        {
            **item,
            "snapshot_id": snapshot_id,
            "fetched_at": fetched_at,
            "source": "Supercell API live sample",
        }
        for item in items
        if isinstance(item, dict)
    ]


def raw_deck(record: object, key: str) -> tuple[str, ...]:
    if not isinstance(record, dict):
        return ()
    cards = record.get(key)
    if not isinstance(cards, list):
        return ()
    return tuple(sorted(str(card).strip() for card in cards if isinstance(card, str) and card.strip()))


def archetype_name(deck: tuple[str, ...], classifier: Callable[[tuple[str, ...]], object]) -> str:
    return str(classifier(deck).name)


def percent(wins: int, games: int) -> float:
    return round(wins / games * 100, 1) if games else 0.0


def counter_summary(counter: Counter, *, limit: int = 3) -> str:
    values = [f"{name} ({count})" for name, count in counter.most_common(limit)]
    return ", ".join(values) if values else "none observed"


__all__ = ["archetype_name", "counter_summary", "percent", "raw_deck", "with_snapshot_metadata"]
