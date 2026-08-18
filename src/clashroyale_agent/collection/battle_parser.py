from __future__ import annotations

import hashlib

from clashroyale_agent.collection.loadout_normalization import LOADOUT_SCHEMA_VERSION, normalize_side_loadout


PATH_OF_LEGEND_BATTLE_TYPE = "pathOfLegend"


def normalize_player_tag(value: object) -> str:
    return value.strip().upper() if isinstance(value, str) and value.strip() else ""


def is_path_of_legend_battle(battle: object) -> bool:
    """Return whether an official battle-log item belongs to Path of Legend."""
    if not isinstance(battle, dict):
        return False
    battle_type = battle.get("type")
    return isinstance(battle_type, str) and battle_type.strip().casefold() == PATH_OF_LEGEND_BATTLE_TYPE.casefold()


def team_member(value: object) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def team_cards(battle: dict) -> list[dict]:
    return side_cards(team_member(battle.get("team")))


def opponent_cards(battle: dict) -> list[dict]:
    return side_cards(team_member(battle.get("opponent")))


def side_cards(member: dict | None) -> list[dict]:
    team = member
    if team is None:
        return []

    cards = team.get("cards")
    if not isinstance(cards, list):
        cards = team.get("deck")
    if not isinstance(cards, list):
        return []

    normalized = []
    for card in cards:
        if isinstance(card, dict) and isinstance(card.get("name"), str):
            normalized.append(card)
        elif isinstance(card, str) and card.strip():
            normalized.append({"name": card.strip()})
    return normalized


def deck_signature(cards: list[dict]) -> tuple[str, ...]:
    return tuple(sorted(str(card["name"]).strip() for card in cards if str(card.get("name", "")).strip()))


def side_tag(member: dict | None) -> str | None:
    value = member.get("tag") if isinstance(member, dict) else None
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def crowns(member: dict | None) -> int:
    try:
        return int((member or {}).get("crowns", 0) or 0)
    except (TypeError, ValueError):
        return 0


def normalize_battle_record(battle: dict, observer_tag: str | None = None) -> dict | None:
    """Create a source-preserving, order-independent record for one battle."""
    if not isinstance(battle, dict):
        return None
    team = team_member(battle.get("team"))
    opponent = team_member(battle.get("opponent"))
    cards = team_cards(battle)
    if not cards:
        return None
    opposing_cards = opponent_cards(battle)
    team_deck = deck_signature(cards)
    opponent_deck = deck_signature(opposing_cards)
    team_tag = side_tag(team) or (observer_tag.strip().upper() if isinstance(observer_tag, str) and observer_tag.strip() else None)
    opponent_tag = side_tag(opponent)
    timestamp = str(battle.get("battleTime") or battle.get("battle_time") or "")
    team_loadout = normalize_side_loadout(team)
    opponent_loadout = normalize_side_loadout(opponent)

    # A player can appear in the global ranking alongside their opponent. The
    # same battle then appears twice with sides reversed, so the fingerprint is
    # deliberately independent of the observer's side.
    sides = sorted(
        (
            (team_tag or "", team_deck, crowns(team)),
            (opponent_tag or "", opponent_deck, crowns(opponent)),
        ),
        key=repr,
    )
    # battleTime is required for cross-player deduplication. If it is absent,
    # the record remains usable but is deliberately not globally deduplicated:
    # identical decks and crowns alone do not prove it is the same battle.
    battle_id = None
    if timestamp:
        fingerprint = repr((timestamp, sides)).encode("utf-8")
        battle_id = hashlib.sha256(fingerprint).hexdigest()[:24]
    return {
        "battle_id": battle_id,
        "battle_type": battle.get("type"),
        "battle_time": timestamp or None,
        "team_tag": team_tag,
        "opponent_tag": opponent_tag,
        "team_deck": list(team_deck),
        "opponent_deck": list(opponent_deck),
        "loadout_schema_version": LOADOUT_SCHEMA_VERSION,
        "team_loadout": team_loadout,
        "opponent_loadout": opponent_loadout,
        "team_crowns": crowns(team),
        "opponent_crowns": crowns(opponent),
        "won": crowns(team) > crowns(opponent),
    }


def opponent_tags_from_battles(battles: list[dict], *, observer_tag: str | None = None) -> list[str]:
    """Return unique opponent tags observed in selected battle-log records."""
    observer = normalize_player_tag(observer_tag)
    tags: list[str] = []
    seen: set[str] = set()
    for battle in battles:
        if not is_path_of_legend_battle(battle):
            continue
        record = normalize_battle_record(battle, observer)
        if not record:
            continue
        for tag in (record.get("opponent_tag"), record.get("team_tag")):
            normalized = normalize_player_tag(tag)
            if not normalized or normalized == observer or normalized in seen:
                continue
            seen.add(normalized)
            tags.append(normalized)
    return tags


def select_usable_battles(
    battles: list[dict],
    limit: int,
    *,
    seen_battle_ids: set[str] | None = None,
    observer_tag: str | None = None,
    selection_metrics: dict[str, int] | None = None,
    path_of_legend_only: bool = False,
    require_complete_decks_and_stable_id: bool = False,
) -> list[dict]:
    """Keep bounded, unique entries that satisfy the requested battle scope."""
    usable = []
    seen = seen_battle_ids if seen_battle_ids is not None else set()
    for battle in battles:
        if selection_metrics is not None:
            selection_metrics["inspected_battle_records"] = selection_metrics.get("inspected_battle_records", 0) + 1
        if path_of_legend_only and not is_path_of_legend_battle(battle):
            if selection_metrics is not None:
                selection_metrics["non_path_of_legend_records"] = (
                    selection_metrics.get("non_path_of_legend_records", 0) + 1
                )
            continue
        record = normalize_battle_record(battle, observer_tag)
        if record is None:
            if selection_metrics is not None:
                selection_metrics["deckless_or_invalid_records"] = selection_metrics.get("deckless_or_invalid_records", 0) + 1
            continue
        if require_complete_decks_and_stable_id and (
            not record.get("battle_id")
            or not record.get("battle_time")
            or len(record.get("team_deck") or ()) != 8
            or len(record.get("opponent_deck") or ()) != 8
        ):
            if selection_metrics is not None:
                selection_metrics["deckless_or_invalid_records"] = (
                    selection_metrics.get("deckless_or_invalid_records", 0) + 1
                )
            continue
        battle_id = record["battle_id"]
        if battle_id is not None and battle_id in seen:
            if selection_metrics is not None:
                selection_metrics["duplicates_skipped"] = selection_metrics.get("duplicates_skipped", 0) + 1
            continue
        if battle_id is not None:
            seen.add(battle_id)
        usable.append(battle)
        if len(usable) >= limit:
            break
    return usable


__all__ = [
    "PATH_OF_LEGEND_BATTLE_TYPE",
    "crowns",
    "deck_signature",
    "is_path_of_legend_battle",
    "normalize_battle_record",
    "normalize_player_tag",
    "opponent_cards",
    "opponent_tags_from_battles",
    "select_usable_battles",
    "side_cards",
    "side_tag",
    "team_cards",
    "team_member",
]
