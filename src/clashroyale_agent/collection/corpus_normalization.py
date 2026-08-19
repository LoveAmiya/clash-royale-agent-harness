"""Pure normalization helpers for rolling battle facts."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from clashroyale_agent.collection.corpus_policy import CorpusError
from clashroyale_agent.collection.loadout_normalization import (
    LOADOUT_SCHEMA_VERSION,
    canonical_loadout,
)


PATH_OF_LEGEND_TYPE = "pathOfLegend"


def as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise CorpusError("timestamp must be an ISO string or datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | str) -> str:
    return as_utc(value).isoformat()


def normalized_tag(value: object) -> str:
    return str(value or "").strip().upper()


def canonical_battle(record: dict) -> dict:
    if not isinstance(record, dict):
        raise CorpusError("battle must be an object")
    battle_id = str(record.get("battle_id") or "").strip()
    battle_time = str(record.get("battle_time") or "").strip()
    battle_type = str(record.get("battle_type") or "").strip()
    team_deck = record.get("team_deck")
    opponent_deck = record.get("opponent_deck")
    if not battle_id or not battle_time:
        raise CorpusError("battle_id and battle_time are required")
    if battle_type.casefold() != PATH_OF_LEGEND_TYPE.casefold():
        raise CorpusError("only Path of Legend battles are accepted")
    if not isinstance(team_deck, list) or not isinstance(opponent_deck, list):
        raise CorpusError("both decks must be arrays")
    if len(team_deck) != 8 or len(opponent_deck) != 8:
        raise CorpusError("both decks must contain exactly eight cards")
    if any(not isinstance(card, str) or not card.strip() for card in team_deck + opponent_deck):
        raise CorpusError("deck cards must be non-empty strings")
    team_crowns = int(record.get("team_crowns", 0) or 0)
    opponent_crowns = int(record.get("opponent_crowns", 0) or 0)
    sides = sorted(
        (
            {
                "tag": normalized_tag(record.get("team_tag")) or None,
                "deck": [str(card).strip() for card in team_deck],
                "crowns": team_crowns,
                "loadout": canonical_loadout(record.get("team_loadout")),
            },
            {
                "tag": normalized_tag(record.get("opponent_tag")) or None,
                "deck": [str(card).strip() for card in opponent_deck],
                "crowns": opponent_crowns,
                "loadout": canonical_loadout(record.get("opponent_loadout")),
            },
        ),
        key=lambda side: (side["tag"] or "", tuple(side["deck"]), side["crowns"]),
    )
    canonical_team, canonical_opponent = sides
    canonical = {
        "battle_id": battle_id,
        "battle_type": PATH_OF_LEGEND_TYPE,
        "battle_time": battle_time,
        "team_tag": canonical_team["tag"],
        "opponent_tag": canonical_opponent["tag"],
        "team_deck": canonical_team["deck"],
        "opponent_deck": canonical_opponent["deck"],
        "team_crowns": canonical_team["crowns"],
        "opponent_crowns": canonical_opponent["crowns"],
        "won": canonical_team["crowns"] > canonical_opponent["crowns"],
    }
    if canonical_team["loadout"] is not None or canonical_opponent["loadout"] is not None:
        canonical.update(
            {
                "loadout_schema_version": LOADOUT_SCHEMA_VERSION,
                "team_loadout": canonical_team["loadout"],
                "opponent_loadout": canonical_opponent["loadout"],
            }
        )
    return canonical


def base_fact(record: dict) -> dict:
    return {
        key: value
        for key, value in record.items()
        if key not in {"loadout_schema_version", "team_loadout", "opponent_loadout"}
    }


def canonical_loadout_pair(record: dict) -> dict | None:
    team = record.get("team_loadout")
    opponent = record.get("opponent_loadout")
    if not isinstance(team, dict) and not isinstance(opponent, dict):
        return None
    return {
        "schema_version": LOADOUT_SCHEMA_VERSION,
        "team_loadout": team,
        "opponent_loadout": opponent,
        "complete": bool(
            isinstance(team, dict)
            and team.get("complete")
            and isinstance(opponent, dict)
            and opponent.get("complete")
        ),
    }


def fact_json(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "PATH_OF_LEGEND_TYPE",
    "as_utc",
    "base_fact",
    "canonical_battle",
    "canonical_loadout_pair",
    "fact_json",
    "iso",
    "normalized_tag",
]
