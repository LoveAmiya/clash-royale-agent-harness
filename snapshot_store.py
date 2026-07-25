"""Persist and derive the daily official Supercell snapshot.

The canonical snapshot is the only production source for card, deck, matchup,
and RAG evidence. Derived JSON files are written for compatibility, but are
never independently trusted at runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from supercell_live import build_card_deck_stats


DAILY_TARGET_BATTLES = 20_000
DAILY_REFRESH_INTERVAL = timedelta(hours=24)
SNAPSHOT_FILE_NAME = "official_daily_snapshot.json"
MAX_MATCHUP_RAG_DOCUMENTS = 200
MAX_CARD_PROFILE_RAG_DOCUMENTS = 180
MAX_DECK_PROFILE_RAG_DOCUMENTS = 150
MAX_ARCHETYPE_RAG_DOCUMENTS = 80
MAX_CARD_PAIR_RAG_DOCUMENTS = 250
MAX_COUNTER_RAG_DOCUMENTS = 300
MIN_AGGREGATE_EVIDENCE_GAMES = 20


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_complete_daily_snapshot(snapshot: object) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("sample_battles") != DAILY_TARGET_BATTLES:
        return False
    if snapshot.get("target_battles") != DAILY_TARGET_BATTLES:
        return False
    if snapshot.get("shortfall_battles", 0) != 0:
        return False
    metrics = snapshot.get("collection_metrics", {})
    return not bool(metrics.get("refresh_budget_exhausted")) and not bool(metrics.get("rate_limited"))


def snapshot_refresh_due(snapshot: dict | None, *, now: datetime | None = None) -> bool:
    if not is_complete_daily_snapshot(snapshot):
        return True
    reference = _parse_timestamp(snapshot.get("published_at") or snapshot.get("fetched_at"))
    if reference is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - reference >= DAILY_REFRESH_INTERVAL


def snapshot_age_seconds(snapshot: dict | None, *, now: datetime | None = None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    reference = _parse_timestamp(snapshot.get("published_at") or snapshot.get("fetched_at"))
    if reference is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - reference).total_seconds())


def _snapshot_id(snapshot: dict) -> str:
    existing = snapshot.get("snapshot_id")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    stable_fields = {
        "fetched_at": snapshot.get("fetched_at"),
        "sample_battles": snapshot.get("sample_battles"),
        "target_battles": snapshot.get("target_battles"),
        "cards_meta": snapshot.get("cards_meta", []),
        "top_decks": snapshot.get("top_decks", []),
    }
    digest = hashlib.sha256(json.dumps(stable_fields, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"supercell-{digest}"


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _with_snapshot_metadata(items: object, snapshot: dict) -> list[dict]:
    if not isinstance(items, list):
        return []
    snapshot_id = snapshot["snapshot_id"]
    fetched_at = snapshot.get("fetched_at")
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append(
                {
                    **item,
                    "snapshot_id": snapshot_id,
                    "fetched_at": fetched_at,
                    "source": "Supercell API live sample",
                }
            )
    return result


def _raw_deck(record: object, key: str) -> tuple[str, ...]:
    if not isinstance(record, dict):
        return ()
    cards = record.get(key)
    if not isinstance(cards, list):
        return ()
    return tuple(sorted(str(card).strip() for card in cards if isinstance(card, str) and card.strip()))


def _archetype_name(deck: tuple[str, ...]) -> str:
    """Assign a conservative, auditable deck-family label from visible cards."""
    cards = set(deck)
    if "Electro Giant" in cards:
        return "E-Giant beatdown"
    if {"Hog Rider", "Earthquake"}.issubset(cards):
        return "Hog EQ"
    if "Hog Rider" in cards:
        return "Hog cycle"
    if "Lava Hound" in cards:
        return "Lava air beatdown"
    if "Golem" in cards:
        return "Golem beatdown"
    if "P.E.K.K.A" in cards and ({"Battle Ram", "Bandit"} & cards):
        return "PEKKA bridge spam"
    if "P.E.K.K.A" in cards:
        return "PEKKA control"
    if "Goblin Barrel" in cards:
        return "Log bait"
    if "X-Bow" in cards:
        return "X-Bow siege"
    if "Mortar" in cards:
        return "Mortar control"
    if "Royal Giant" in cards:
        return "Royal Giant"
    if "Goblin Giant" in cards:
        return "Goblin Giant beatdown"
    if "Graveyard" in cards:
        return "Graveyard control"
    if "Balloon" in cards:
        return "Balloon pressure"
    if "Goblin Drill" in cards:
        return "Goblin Drill control"
    if "Miner" in cards:
        return "Miner control"
    if "Giant" in cards:
        return "Giant beatdown"
    return "Unclassified deck family"


def _percent(wins: int, games: int) -> float:
    return round(wins / games * 100, 1) if games else 0.0


def _counter_summary(counter: Counter, *, limit: int = 3) -> str:
    values = [f"{name} ({count})" for name, count in counter.most_common(limit)]
    return ", ".join(values) if values else "none observed"


def _build_aggregate_evidence_documents(snapshot: dict, common_metadata: dict) -> list[dict]:
    """Derive high-information retrieval evidence from raw, deduplicated battles."""
    card_games: Counter[str] = Counter()
    card_wins: Counter[str] = Counter()
    card_teammates: dict[str, Counter[str]] = defaultdict(Counter)
    card_opponents: dict[str, Counter[str]] = defaultdict(Counter)
    deck_games: Counter[tuple[str, ...]] = Counter()
    deck_wins: Counter[tuple[str, ...]] = Counter()
    deck_opponents: dict[tuple[str, ...], Counter[tuple[str, ...]]] = defaultdict(Counter)
    pair_games: Counter[tuple[str, str]] = Counter()
    pair_wins: Counter[tuple[str, str]] = Counter()
    counter_games: Counter[tuple[str, str]] = Counter()
    counter_wins: Counter[tuple[str, str]] = Counter()
    archetype_games: Counter[str] = Counter()
    archetype_wins: Counter[str] = Counter()
    archetype_opponents: dict[str, Counter[str]] = defaultdict(Counter)
    archetype_matchup_games: Counter[tuple[str, str]] = Counter()
    archetype_matchup_wins: Counter[tuple[str, str]] = Counter()

    for record in snapshot.get("raw_battles", []):
        team_deck = _raw_deck(record, "team_deck")
        if not team_deck:
            continue
        opponent_deck = _raw_deck(record, "opponent_deck")
        won = bool(record.get("won")) if isinstance(record, dict) else False
        deck_games[team_deck] += 1
        deck_wins[team_deck] += int(won)
        if opponent_deck:
            deck_opponents[team_deck][opponent_deck] += 1

        for card in team_deck:
            card_games[card] += 1
            card_wins[card] += int(won)
            card_teammates[card].update(other for other in team_deck if other != card)
            card_opponents[card].update(opponent_deck)
            for opponent_card in opponent_deck:
                counter_games[(card, opponent_card)] += 1
                counter_wins[(card, opponent_card)] += int(won)
        for pair in combinations(team_deck, 2):
            pair_games[pair] += 1
            pair_wins[pair] += int(won)

        archetype = _archetype_name(team_deck)
        opponent_archetype = _archetype_name(opponent_deck) if opponent_deck else "Unknown opponent family"
        archetype_games[archetype] += 1
        archetype_wins[archetype] += int(won)
        archetype_opponents[archetype][opponent_archetype] += 1
        archetype_matchup_games[(archetype, opponent_archetype)] += 1
        archetype_matchup_wins[(archetype, opponent_archetype)] += int(won)

    snapshot_id = common_metadata["snapshot_id"]
    documents: list[dict] = []
    for card, games in card_games.most_common(MAX_CARD_PROFILE_RAG_DOCUMENTS):
        documents.append(
            {
                "doc_id": f"{snapshot_id}:card-profile:{card}",
                "source_type": "card_profile",
                "text": (
                    f"Card profile evidence. {card} appeared in {games} sampled battles with win rate "
                    f"{_percent(card_wins[card], games)}%. Common teammate cards: "
                    f"{_counter_summary(card_teammates[card])}. Common opposing cards: "
                    f"{_counter_summary(card_opponents[card])}."
                ),
                "metadata": {**common_metadata, "card_name": card, "games": games},
            }
        )

    for deck, games in deck_games.most_common(MAX_DECK_PROFILE_RAG_DOCUMENTS):
        if games < MIN_AGGREGATE_EVIDENCE_GAMES:
            continue
        opponents = _counter_summary(Counter({" / ".join(key): value for key, value in deck_opponents[deck].items()}))
        documents.append(
            {
                "doc_id": f"{snapshot_id}:deck-profile:{'|'.join(deck)}",
                "source_type": "deck_profile",
                "text": (
                    f"Deck profile evidence. {' / '.join(deck)} appeared in {games} sampled games with win rate "
                    f"{_percent(deck_wins[deck], games)}%. Most observed opposing decks: {opponents}."
                ),
                "metadata": {**common_metadata, "deck_name": " / ".join(deck), "games": games},
            }
        )

    for archetype, games in archetype_games.most_common(MAX_ARCHETYPE_RAG_DOCUMENTS):
        matchups = []
        for opponent, matchup_games in archetype_opponents[archetype].most_common(3):
            wins = archetype_matchup_wins[(archetype, opponent)]
            matchups.append(f"{opponent} ({matchup_games} games, {_percent(wins, matchup_games)}% win)")
        documents.append(
            {
                "doc_id": f"{snapshot_id}:archetype:{archetype}",
                "source_type": "archetype",
                "text": (
                    f"Heuristic archetype evidence. {archetype} appeared in {games} sampled battles with win rate "
                    f"{_percent(archetype_wins[archetype], games)}%. Frequent opposing families: "
                    f"{'; '.join(matchups) if matchups else 'none observed'}."
                ),
                "metadata": {**common_metadata, "archetype": archetype, "games": games, "classification": "card-rule heuristic"},
            }
        )

    for pair, games in pair_games.most_common():
        if games < MIN_AGGREGATE_EVIDENCE_GAMES or len(documents) >= (
            MAX_CARD_PROFILE_RAG_DOCUMENTS + MAX_DECK_PROFILE_RAG_DOCUMENTS + MAX_ARCHETYPE_RAG_DOCUMENTS + MAX_CARD_PAIR_RAG_DOCUMENTS
        ):
            continue
        documents.append(
            {
                "doc_id": f"{snapshot_id}:card-pair:{pair[0]}::{pair[1]}",
                "source_type": "card_pair",
                "text": (
                    f"Card-pair synergy evidence. {pair[0]} and {pair[1]} appeared together in {games} sampled battles; "
                    f"the observed deck win rate was {_percent(pair_wins[pair], games)}%."
                ),
                "metadata": {**common_metadata, "cards": list(pair), "games": games},
            }
        )

    counter_documents = 0
    for (card, opponent_card), games in counter_games.most_common():
        if games < MIN_AGGREGATE_EVIDENCE_GAMES or counter_documents >= MAX_COUNTER_RAG_DOCUMENTS:
            continue
        documents.append(
            {
                "doc_id": f"{snapshot_id}:counter:{card}::{opponent_card}",
                "source_type": "counter",
                "text": (
                    f"Observed counter evidence. Decks containing {card} faced {opponent_card} in {games} sampled battles; "
                    f"their observed win rate was {_percent(counter_wins[(card, opponent_card)], games)}%. "
                    "This is sampled matchup evidence, not a causal counter claim."
                ),
                "metadata": {**common_metadata, "card_name": card, "opponent_card_name": opponent_card, "games": games},
            }
        )
        counter_documents += 1
    return documents


def build_snapshot_rag_documents(snapshot: dict) -> list[dict]:
    """Build compact evidence documents from one validated official snapshot."""
    if not is_complete_daily_snapshot(snapshot):
        raise ValueError("only a complete daily snapshot can produce RAG documents")

    snapshot_id = snapshot["snapshot_id"]
    common_metadata = {
        "snapshot_id": snapshot_id,
        "fetched_at": snapshot.get("fetched_at"),
        "sample_battles": snapshot["sample_battles"],
        "source": "Supercell API live sample",
    }
    documents = [
        {
            "doc_id": f"{snapshot_id}:overview",
            "source_type": "snapshot",
            "text": (
                f"Official Clash Royale daily snapshot {snapshot_id}. "
                f"It contains {snapshot['sample_battles']} usable battles collected at {snapshot.get('fetched_at')}. "
                "Card, deck, and matchup metrics in this document set are limited to this sampled battle-log evidence."
            ),
            "metadata": common_metadata,
        }
    ]

    for card in _with_snapshot_metadata(snapshot.get("cards_meta"), snapshot):
        documents.append(
            {
                "doc_id": f"{snapshot_id}:card:{card.get('rank')}:{card.get('card_name')}",
                "source_type": "card",
                "text": (
                    f"Card evidence. {card.get('card_name')} ranks {card.get('rank')}; "
                    f"usage rate {card.get('usage_rate')}%; win rate {card.get('win_rate')}%; "
                    f"appearances {card.get('appearance_count', 0)} in {snapshot['sample_battles']} sampled battles."
                ),
                "metadata": {**common_metadata, "card_name": card.get("card_name"), "rank": card.get("rank")},
            }
        )

    for deck in _with_snapshot_metadata(snapshot.get("top_decks"), snapshot):
        documents.append(
            {
                "doc_id": f"{snapshot_id}:deck:{deck.get('rank')}:{deck.get('deck_name')}",
                "source_type": "deck",
                "text": (
                    f"Deck evidence. {deck.get('deck_name')} ranks {deck.get('rank')}; "
                    f"sampled games {deck.get('battles')}; win rate {deck.get('sample_win_rate')}%; "
                    f"cards: {', '.join(deck.get('cards', []))}."
                ),
                "metadata": {**common_metadata, "deck_name": deck.get("deck_name"), "rank": deck.get("rank")},
            }
        )

    matchups = [item for item in snapshot.get("deck_matchups", []) if isinstance(item, dict)]
    matchups.sort(key=lambda item: (int(item.get("games", 0) or 0), float(item.get("win_rate", 0) or 0)), reverse=True)
    for matchup in matchups[:MAX_MATCHUP_RAG_DOCUMENTS]:
        documents.append(
            {
                "doc_id": (
                    f"{snapshot_id}:matchup:{matchup.get('deck_name')}::"
                    f"{matchup.get('opponent_deck_name')}"
                ),
                "source_type": "matchup",
                "text": (
                    f"Deck matchup evidence. {matchup.get('deck_name')} versus "
                    f"{matchup.get('opponent_deck_name')}: {matchup.get('wins')} wins in "
                    f"{matchup.get('games')} games, win rate {matchup.get('win_rate')}%."
                ),
                "metadata": {
                    **common_metadata,
                    "deck_name": matchup.get("deck_name"),
                    "opponent_deck_name": matchup.get("opponent_deck_name"),
                    "games": matchup.get("games"),
                },
            }
        )
    documents.extend(_build_aggregate_evidence_documents(snapshot, common_metadata))
    return documents


def publish_daily_snapshot(snapshot: dict, data_dir: Path) -> dict:
    """Atomically publish a complete official snapshot and its derived datasets."""
    if not is_complete_daily_snapshot(snapshot):
        raise ValueError("refusing to publish an incomplete official daily snapshot")

    published = dict(snapshot)
    published["snapshot_id"] = _snapshot_id(published)
    published.setdefault("published_at", datetime.now(timezone.utc).isoformat())
    published["cards_meta"] = _with_snapshot_metadata(published.get("cards_meta"), published)
    published["top_decks"] = _with_snapshot_metadata(published.get("top_decks"), published)
    published["deck_matchups"] = _with_snapshot_metadata(published.get("deck_matchups"), published)
    if not isinstance(published.get("card_deck_stats"), dict):
        published["card_deck_stats"] = build_card_deck_stats(
            list(published.get("raw_battles", [])),
            fetched_at=str(published.get("fetched_at") or ""),
            sample_battles=int(published.get("sample_battles") or 0),
            target_battles=int(published.get("target_battles") or 0),
        )
    documents = build_snapshot_rag_documents(published)
    published["rag_document_counts"] = dict(Counter(document["source_type"] for document in documents))

    # The canonical file is written last. Readers either use the previous full
    # snapshot or the new complete set; they never accept a partial collection.
    _atomic_write_json(data_dir / "cards_meta.json", published["cards_meta"])
    _atomic_write_json(data_dir / "top_decks.json", published["top_decks"])
    _atomic_write_json(data_dir / "rag_documents.json", documents)
    _atomic_write_json(data_dir / SNAPSHOT_FILE_NAME, published)
    return published


def load_published_snapshot(data_dir: Path) -> dict | None:
    path = data_dir / SNAPSHOT_FILE_NAME
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not is_complete_daily_snapshot(snapshot):
        return None
    if not isinstance(snapshot.get("card_deck_stats"), dict):
        # One-time schema migration for snapshots produced before the
        # card-filtered exact-deck index existed. It only consumes the already
        # published official raw battles and never triggers a fresh API call.
        snapshot["card_deck_stats"] = build_card_deck_stats(
            list(snapshot.get("raw_battles", [])),
            fetched_at=str(snapshot.get("fetched_at") or ""),
            sample_battles=int(snapshot.get("sample_battles") or 0),
            target_battles=int(snapshot.get("target_battles") or 0),
        )
        _atomic_write_json(path, snapshot)
    return snapshot
