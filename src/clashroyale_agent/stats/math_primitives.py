"""Pure deterministic statistic primitives."""

from __future__ import annotations

import math


def result(crowns: int, opponent_crowns: int) -> tuple[int, int, int]:
    if crowns > opponent_crowns:
        return 1, 0, 0
    if crowns < opponent_crowns:
        return 0, 1, 0
    return 0, 0, 1


def wilson_lower_bound(wins: int, losses: int, z: float = 1.96) -> float:
    total = wins + losses
    if total <= 0:
        return 0.0
    proportion = wins / total
    denominator = 1 + z * z / total
    center = proportion + z * z / (2 * total)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
    return round(max(0.0, (center - margin) / denominator), 6)


def increment(counter: dict, key: object, outcome: tuple[int, int, int], games: int = 1) -> None:
    values = counter[key]
    values[0] += games
    values[1] += outcome[0]
    values[2] += outcome[1]
    values[3] += outcome[2]


__all__ = ["increment", "result", "wilson_lower_bound"]
