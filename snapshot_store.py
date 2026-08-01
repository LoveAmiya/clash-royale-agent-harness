"""Persist and derive the weekly official Supercell snapshot.

The canonical snapshot is the only production source for card, deck, matchup,
and RAG evidence. Derived JSON files are written for compatibility, but are
never independently trusted at runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from deck_archetypes import CLASSIFIER_VERSION, archetype_family, classify_deck
from supercell_live import (
    JsonlRecordSequence,
    PATH_OF_LEGEND_BATTLE_TYPE,
    PATH_OF_LEGEND_COLLECTION_SCOPE,
    PATH_OF_LEGEND_SCOPE_CONTRACT,
    build_card_deck_stats,
)


# The DAILY names remain as compatibility aliases for existing imports. The
# production contract is now one complete 200,000-battle snapshot per week.
DAILY_TARGET_BATTLES = 200_000
DAILY_REFRESH_INTERVAL = timedelta(days=7)
SNAPSHOT_FILE_NAME = "official_daily_snapshot.json"
SNAPSHOT_POINTER_FILE_NAME = "official_snapshot_pointer.json"
SNAPSHOT_ARCHIVE_DIR_NAME = "snapshot_archives"
SNAPSHOT_RETENTION_DAYS = 14
SNAPSHOT_RETENTION_MAX_COMPLETE = 2
MIN_MATCHUP_RAG_EVIDENCE_GAMES = 5
MAX_CARD_PROFILE_RAG_DOCUMENTS = 180
MAX_DECK_PROFILE_RAG_DOCUMENTS = 150
MAX_ARCHETYPE_RAG_DOCUMENTS = 80
MAX_CARD_PAIR_RAG_DOCUMENTS = 250
MAX_COUNTER_RAG_DOCUMENTS = 300
MIN_AGGREGATE_EVIDENCE_GAMES = 20


RAG_REQUIRED_COMMON_METADATA = ("snapshot_id", "fetched_at", "sample_battles", "source")


def compute_rag_docs_fingerprint(documents: list[dict]) -> str:
    """Stable identity for the exact evidence corpus, independent of index state."""
    return hashlib.sha256(
        json.dumps(documents, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _rate_valid(value: object) -> bool:
    return _is_number(value) and 0 <= float(value) <= 100


def _same_number(left: object, right: object) -> bool:
    return _is_number(left) and _is_number(right) and round(float(left), 6) == round(float(right), 6)


def _doc_text_has_missing_value(doc: dict) -> bool:
    text = str(doc.get("text", ""))
    lowered = text.lower()
    return any(token in lowered for token in ("none%", "null%", "nan%", "undefined%"))


def _deck_key(deck_name: object, cards: object) -> tuple[str, tuple[str, ...]]:
    return (
        str(deck_name or "").strip(),
        tuple(str(card).strip() for card in cards if isinstance(card, str) and card.strip())
        if isinstance(cards, list)
        else (),
    )


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
    if "collection_scope" in snapshot and (
        snapshot.get("collection_scope") != PATH_OF_LEGEND_COLLECTION_SCOPE
        or snapshot.get("scope_contract") != PATH_OF_LEGEND_SCOPE_CONTRACT
    ):
        return False
    raw_storage = snapshot.get("raw_battles_storage")
    if isinstance(raw_storage, dict):
        try:
            declared_count = int(raw_storage.get("record_count"))
        except (TypeError, ValueError):
            return False
        if raw_storage.get("loaded") is False:
            if declared_count != int(snapshot.get("sample_battles") or 0):
                return False
        else:
            raw_battles = snapshot.get("raw_battles")
            try:
                raw_count = len(raw_battles)
            except TypeError:
                return False
            if raw_count != declared_count or raw_count != int(snapshot.get("sample_battles") or 0):
                return False
    metrics = snapshot.get("collection_metrics", {})
    return not bool(metrics.get("refresh_budget_exhausted")) and not bool(metrics.get("rate_limited"))


def is_path_of_legend_snapshot(snapshot: object) -> bool:
    return bool(
        isinstance(snapshot, dict)
        and snapshot.get("collection_scope") == PATH_OF_LEGEND_COLLECTION_SCOPE
        and snapshot.get("scope_contract") == PATH_OF_LEGEND_SCOPE_CONTRACT
    )


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
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            if isinstance(payload, dict) and isinstance(payload.get("raw_battles"), JsonlRecordSequence):
                _write_streaming_snapshot_json(handle, payload)
            else:
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


def _write_streaming_snapshot_json(handle, payload: dict) -> None:
    public_items = [(key, value) for key, value in payload.items() if not str(key).startswith("_")]
    handle.write("{")
    for index, (key, value) in enumerate(public_items):
        if index:
            handle.write(",")
        handle.write("\n  ")
        json.dump(key, handle, ensure_ascii=False)
        handle.write(": ")
        if isinstance(value, JsonlRecordSequence):
            handle.write("[")
            actual_count = 0
            for record in value:
                if actual_count:
                    handle.write(",")
                handle.write("\n    ")
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
                actual_count += 1
            if actual_count:
                handle.write("\n  ")
            handle.write("]")
            if actual_count != len(value):
                raise ValueError(
                    f"streamed raw battle count mismatch: expected={len(value)} actual={actual_count}"
                )
        else:
            json.dump(value, handle, ensure_ascii=False, indent=2)
    handle.write("\n}\n")


def _atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp_name)
        with open(temp_name, "rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _archive_published_snapshot(data_dir: Path, snapshot: dict, documents: list[dict]) -> None:
    """Write a self-contained rollback package before the canonical pointer moves."""
    snapshot_id = str(snapshot["snapshot_id"])
    archive_dir = data_dir / SNAPSHOT_ARCHIVE_DIR_NAME / snapshot_id
    _atomic_write_json(archive_dir / "snapshot.json", snapshot)
    _atomic_write_json(archive_dir / "rag_documents.json", documents)
    aggregate_source = snapshot.get("_aggregate_store_path")
    if isinstance(aggregate_source, str) and Path(aggregate_source).is_file():
        _atomic_copy_file(Path(aggregate_source), archive_dir / "aggregates.sqlite")
    _atomic_write_json(archive_dir / "collector_snapshot.json", _collector_snapshot_summary(snapshot))
    _atomic_write_json(
        archive_dir / "manifest.json",
        {
            "schema_version": 1,
            "snapshot_id": snapshot_id,
            "published_at": snapshot.get("published_at"),
            "fetched_at": snapshot.get("fetched_at"),
            "sample_battles": snapshot.get("sample_battles"),
            "rag_docs_fingerprint": snapshot.get("rag_docs_fingerprint"),
            "complete": True,
        },
    )


def _collector_snapshot_summary(snapshot: dict) -> dict:
    """Keep collector restarts independent of the 200,000-record JSON array."""
    fields = (
        "snapshot_id",
        "published_at",
        "fetched_at",
        "sample_battles",
        "target_battles",
        "shortfall_battles",
        "ranked_players",
        "fetched_players",
        "sampled_players",
        "failed_players",
        "usable_battles",
        "leaderboard_candidate_limit",
        "leaderboard_start_rank",
        "leaderboard_last_scanned_rank",
        "collection_metrics",
        "special_fields_probe",
        "rag_docs_fingerprint",
        "rag_document_counts",
        "rag_document_validation",
        "aggregate_store",
    )
    summary = {key: snapshot.get(key) for key in fields if key in snapshot}
    raw_storage = snapshot.get("raw_battles_storage")
    if not isinstance(raw_storage, dict):
        raw_storage = {
            "format": "canonical_json_array",
            "canonical_file": SNAPSHOT_FILE_NAME,
            "record_count": int(snapshot.get("sample_battles") or 0),
        }
    summary["raw_battles"] = []
    summary["raw_battles_storage"] = {**raw_storage, "loaded": False}
    summary["cards_meta"] = []
    summary["top_decks"] = []
    summary["card_deck_stats"] = {}
    summary["deck_matchups"] = []
    return summary


def load_published_snapshot_summary(data_dir: Path) -> dict | None:
    """Load the active collector status without parsing canonical raw battles."""
    pointer_path = data_dir / SNAPSHOT_POINTER_FILE_NAME
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    snapshot_id = str(pointer.get("snapshot_id") or "") if isinstance(pointer, dict) else ""
    if not snapshot_id or any(character in snapshot_id for character in ("/", "\\", ":")):
        return None
    summary_path = data_dir / SNAPSHOT_ARCHIVE_DIR_NAME / snapshot_id / "collector_snapshot.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if summary.get("snapshot_id") != snapshot_id or not is_complete_daily_snapshot(summary):
        return None
    return summary


def _complete_archive_manifests(data_dir: Path) -> list[dict]:
    archive_root = data_dir / SNAPSHOT_ARCHIVE_DIR_NAME
    if not archive_root.exists():
        return []
    manifests: list[dict] = []
    for archive_dir in archive_root.iterdir():
        if not archive_dir.is_dir():
            continue
        try:
            manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot_id = str(manifest.get("snapshot_id") or "").strip()
        if snapshot_id != archive_dir.name or manifest.get("complete") is not True:
            continue
        published_at = _parse_timestamp(manifest.get("published_at") or manifest.get("fetched_at"))
        if published_at is None:
            continue
        manifests.append({**manifest, "snapshot_id": snapshot_id, "_published_at": published_at})
    return manifests


def cleanup_snapshot_retention(
    data_dir: Path,
    *,
    active_snapshot_id: str,
    now: datetime | None = None,
) -> dict:
    """Keep the active and newest previous complete snapshot package only.

    This must be called after the active snapshot's RAG index is ready. Unknown
    directories without a valid archive manifest are intentionally untouched.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    manifests = sorted(
        _complete_archive_manifests(data_dir),
        key=lambda item: item["_published_at"],
        reverse=True,
    )
    active = next((item for item in manifests if item["snapshot_id"] == active_snapshot_id), None)
    previous = next((item for item in manifests if item["snapshot_id"] != active_snapshot_id), None)
    retained = [item for item in (active, previous) if item is not None]
    retained_ids = {item["snapshot_id"] for item in retained}
    removed_ids: list[str] = []
    storage_roots = (
        SNAPSHOT_ARCHIVE_DIR_NAME,
        "daily_snapshot_qdrant",
        "audit_exports",
        "external_reviews",
        "structured_stats",
    )
    for manifest in manifests:
        snapshot_id = manifest["snapshot_id"]
        age = current - manifest["_published_at"]
        over_age = age >= timedelta(days=SNAPSHOT_RETENTION_DAYS)
        over_count = snapshot_id not in retained_ids
        if snapshot_id == active_snapshot_id or not (over_age or over_count):
            continue
        # The previous complete rollback is protected even if it is older than
        # 14 days; it is replaced only after a newer complete package exists.
        if previous is not None and snapshot_id == previous["snapshot_id"]:
            continue
        for root_name in storage_roots:
            target = data_dir / root_name / snapshot_id
            if target.is_dir():
                shutil.rmtree(target)
        removed_ids.append(snapshot_id)

    return {
        "retention_days": SNAPSHOT_RETENTION_DAYS,
        "max_complete_snapshots": SNAPSHOT_RETENTION_MAX_COMPLETE,
        "retained_snapshot_ids": [item["snapshot_id"] for item in retained],
        "removed_snapshot_ids": removed_ids,
    }


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
    """Compatibility wrapper around the shared feature-weighted classifier."""
    return classify_deck(deck).name


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
    raw_records = snapshot.get("raw_battles", [])
    streamed = isinstance(raw_records, JsonlRecordSequence) or isinstance(snapshot.get("aggregate_store"), dict)
    tracked_decks: set[tuple[str, ...]] = set()
    if streamed:
        for deck in snapshot.get("top_decks", []):
            if isinstance(deck, dict):
                tracked = _raw_deck(deck, "cards")
                if tracked:
                    tracked_decks.add(tracked)
        card_deck_stats = snapshot.get("card_deck_stats", {})
        if isinstance(card_deck_stats, dict):
            for variants in card_deck_stats.values():
                if not isinstance(variants, list):
                    continue
                for deck in variants:
                    tracked = _raw_deck(deck, "cards")
                    if tracked:
                        tracked_decks.add(tracked)
    observed_cards: set[str] = set()

    for record in raw_records:
        team_deck = _raw_deck(record, "team_deck")
        if not team_deck:
            continue
        opponent_deck = _raw_deck(record, "opponent_deck")
        observed_cards.update(team_deck)
        observed_cards.update(opponent_deck)
        if len(observed_cards) > 256:
            raise ValueError("official snapshot contains an unsafe number of distinct card names")
        team_won = bool(record.get("won")) if isinstance(record, dict) else False
        opponent_won = not team_won
        if isinstance(record, dict):
            team_crowns = record.get("team_crowns")
            opponent_crowns = record.get("opponent_crowns")
            if isinstance(team_crowns, int) and isinstance(opponent_crowns, int):
                team_won = team_crowns > opponent_crowns
                opponent_won = opponent_crowns > team_crowns

        perspectives = [(team_deck, opponent_deck, team_won)]
        if opponent_deck:
            perspectives.append((opponent_deck, team_deck, opponent_won))

        for deck, opposing_deck, won in perspectives:
            if not streamed or deck in tracked_decks:
                deck_games[deck] += 1
                deck_wins[deck] += int(won)
            if opposing_deck and not streamed:
                deck_opponents[deck][opposing_deck] += 1

            for card in deck:
                card_games[card] += 1
                card_wins[card] += int(won)
                card_teammates[card].update(other for other in deck if other != card)
                card_opponents[card].update(opposing_deck)
                for opposing_card in opposing_deck:
                    counter_games[(card, opposing_card)] += 1
                    counter_wins[(card, opposing_card)] += int(won)
            for pair in combinations(deck, 2):
                pair_games[pair] += 1
                pair_wins[pair] += int(won)

            archetype = _archetype_name(deck)
            opponent_archetype = (
                _archetype_name(opposing_deck) if opposing_deck else "Unknown opponent family"
            )
            archetype_games[archetype] += 1
            archetype_wins[archetype] += int(won)
            archetype_opponents[archetype][opponent_archetype] += 1
            archetype_matchup_games[(archetype, opponent_archetype)] += 1
            archetype_matchup_wins[(archetype, opponent_archetype)] += int(won)

    snapshot_id = common_metadata["snapshot_id"]
    deck_profile_opponents = snapshot.get("deck_profile_opponents", {})
    documents: list[dict] = []
    for card, games in card_games.most_common(MAX_CARD_PROFILE_RAG_DOCUMENTS):
        win_rate = _percent(card_wins[card], games)
        documents.append(
            {
                "doc_id": f"{snapshot_id}:card-profile:{card}",
                "source_type": "card_profile",
                "text": (
                    f"Card profile evidence. {card} appeared in {games} games with win rate "
                    f"{win_rate}%. Common teammate cards: "
                    f"{_counter_summary(card_teammates[card])}. Common opposing cards: "
                    f"{_counter_summary(card_opponents[card])}."
                ),
                "metadata": {**common_metadata, "card_name": card, "games": games, "win_rate": win_rate},
            }
        )

    for deck, games in deck_games.most_common(MAX_DECK_PROFILE_RAG_DOCUMENTS):
        if games < MIN_AGGREGATE_EVIDENCE_GAMES:
            continue
        if streamed and isinstance(deck_profile_opponents, dict):
            observed = deck_profile_opponents.get(" / ".join(deck), [])
            opponents = ", ".join(
                f"{item.get('opponent_deck_name')} ({item.get('games')})"
                for item in observed
                if isinstance(item, dict) and item.get("opponent_deck_name") and item.get("games")
            ) or "available in the exact aggregate store"
        else:
            opponents = _counter_summary(Counter({" / ".join(key): value for key, value in deck_opponents[deck].items()}))
        sample_win_rate = _percent(deck_wins[deck], games)
        documents.append(
            {
                "doc_id": f"{snapshot_id}:deck-profile:{'|'.join(deck)}",
                "source_type": "deck_profile",
                "text": (
                    f"Deck profile evidence. {' / '.join(deck)} appeared in {games} games with win rate "
                    f"{sample_win_rate}%. Most observed opposing decks: {opponents}."
                ),
                "metadata": {
                    **common_metadata,
                    "deck_name": " / ".join(deck),
                    "cards": list(deck),
                    "games": games,
                    "sample_win_rate": sample_win_rate,
                },
            }
        )

    for archetype, games in archetype_games.most_common(MAX_ARCHETYPE_RAG_DOCUMENTS):
        win_rate = _percent(archetype_wins[archetype], games)
        matchups = []
        for opponent, matchup_games in archetype_opponents[archetype].most_common(3):
            wins = archetype_matchup_wins[(archetype, opponent)]
            matchups.append(f"{opponent} ({matchup_games} games, {_percent(wins, matchup_games)}% win)")
        documents.append(
            {
                "doc_id": f"{snapshot_id}:archetype:{archetype}",
                "source_type": "archetype",
                "text": (
                    f"Feature-weighted archetype evidence. {archetype} ({archetype_family(archetype)}) "
                    f"appeared in {games} games with win rate "
                    f"{win_rate}%. Frequent opposing families: "
                    f"{'; '.join(matchups) if matchups else 'none observed'}."
                ),
                "metadata": {
                    **common_metadata,
                    "archetype": archetype,
                    "archetype_family": archetype_family(archetype),
                    "games": games,
                    "win_rate": win_rate,
                    "classification": CLASSIFIER_VERSION,
                },
            }
        )

    for pair, games in pair_games.most_common():
        if games < MIN_AGGREGATE_EVIDENCE_GAMES or len(documents) >= (
            MAX_CARD_PROFILE_RAG_DOCUMENTS + MAX_DECK_PROFILE_RAG_DOCUMENTS + MAX_ARCHETYPE_RAG_DOCUMENTS + MAX_CARD_PAIR_RAG_DOCUMENTS
        ):
            continue
        sample_win_rate = _percent(pair_wins[pair], games)
        documents.append(
            {
                "doc_id": f"{snapshot_id}:card-pair:{pair[0]}::{pair[1]}",
                "source_type": "card_pair",
                "text": (
                    f"Card-pair synergy evidence. {pair[0]} and {pair[1]} appeared together in {games} games; "
                    f"the observed deck win rate was {sample_win_rate}%."
                ),
                "metadata": {**common_metadata, "cards": list(pair), "games": games, "sample_win_rate": sample_win_rate},
            }
        )

    counter_documents = 0
    for (card, opponent_card), games in counter_games.most_common():
        if games < MIN_AGGREGATE_EVIDENCE_GAMES or counter_documents >= MAX_COUNTER_RAG_DOCUMENTS:
            continue
        win_rate = _percent(counter_wins[(card, opponent_card)], games)
        documents.append(
            {
                "doc_id": f"{snapshot_id}:counter:{card}::{opponent_card}",
                "source_type": "counter",
                "text": (
                    f"Observed counter evidence. Decks containing {card} faced {opponent_card} in {games} games; "
                    f"their observed win rate was {win_rate}%. "
                    "This is sampled matchup evidence, not a causal counter claim."
                ),
                "metadata": {
                    **common_metadata,
                    "card_name": card,
                    "opponent_card_name": opponent_card,
                    "games": games,
                    "win_rate": win_rate,
                },
            }
        )
        counter_documents += 1
    return documents


def build_snapshot_rag_documents(snapshot: dict) -> list[dict]:
    """Build compact evidence documents from one validated official snapshot."""
    if not is_complete_daily_snapshot(snapshot):
        raise ValueError("only a complete weekly snapshot can produce RAG documents")

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
                f"Official Clash Royale weekly snapshot {snapshot_id}. "
                f"It contains {snapshot['sample_battles']} battles collected at {snapshot.get('fetched_at')}. "
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
                    f"{card.get('appearance_count', 0)} appearances in a {snapshot['sample_battles']}-battle sample."
                ),
                "metadata": {
                    **common_metadata,
                    "card_name": card.get("card_name"),
                    "rank": card.get("rank"),
                    "usage_rate": card.get("usage_rate"),
                    "win_rate": card.get("win_rate"),
                    "clean_win_rate": card.get("clean_win_rate"),
                    "appearance_count": card.get("appearance_count", 0),
                },
            }
        )

    for deck in _with_snapshot_metadata(snapshot.get("top_decks"), snapshot):
        documents.append(
            {
                "doc_id": f"{snapshot_id}:deck:{deck.get('rank')}:{deck.get('deck_name')}",
                "source_type": "deck",
                "text": (
                    f"Deck evidence. {deck.get('deck_name')} ranks {deck.get('rank')}; "
                    f"observed in {deck.get('battles')} games; win rate {deck.get('sample_win_rate')}%; "
                    f"cards: {', '.join(deck.get('cards', []))}."
                ),
                "metadata": {
                    **common_metadata,
                    "deck_name": deck.get("deck_name"),
                    "rank": deck.get("rank"),
                    "cards": list(deck.get("cards", [])),
                    "battles": deck.get("battles"),
                    "sample_win_rate": deck.get("sample_win_rate"),
                },
            }
        )

    matchups = [item for item in snapshot.get("deck_matchups", []) if isinstance(item, dict)]
    matchups.sort(key=lambda item: (int(item.get("games", 0) or 0), float(item.get("win_rate", 0) or 0)), reverse=True)
    for matchup in matchups:
        if int(matchup.get("games", 0) or 0) < MIN_MATCHUP_RAG_EVIDENCE_GAMES:
            continue
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
                    "wins": matchup.get("wins"),
                    "win_rate": matchup.get("win_rate"),
                },
            }
        )
    documents.extend(_build_aggregate_evidence_documents(snapshot, common_metadata))
    return documents


def validate_snapshot_rag_documents(snapshot: dict, documents: list[dict]) -> dict:
    """Validate the complete derived corpus before it can become active evidence."""
    snapshot_id = str(snapshot.get("snapshot_id") or "") if isinstance(snapshot, dict) else ""
    failures: set[str] = set()
    invalid_doc_ids: set[str] = set()
    source_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    def invalidate(doc: object, failure: str) -> None:
        failures.add(failure)
        if isinstance(doc, dict):
            invalid_doc_ids.add(str(doc.get("doc_id") or "<missing-doc-id>"))

    if not is_complete_daily_snapshot(snapshot):
        failures.add("incomplete_snapshot")
    if not snapshot_id or not _non_empty_text(snapshot.get("fetched_at")):
        failures.add("invalid_snapshot_identity")
    if not isinstance(snapshot.get("cards_meta"), list) or not snapshot.get("cards_meta"):
        failures.add("cards_meta_missing")
    if not isinstance(snapshot.get("top_decks"), list) or not snapshot.get("top_decks"):
        failures.add("top_decks_missing")
    if not isinstance(documents, list) or not documents:
        failures.add("rag_documents_missing")
        documents = []

    for doc in documents:
        if not isinstance(doc, dict):
            invalidate(doc, "invalid_evidence_fields")
            continue
        doc_id = doc.get("doc_id")
        source_type = doc.get("source_type")
        metadata = doc.get("metadata")
        if not _non_empty_text(doc_id) or doc_id in seen_ids:
            invalidate(doc, "duplicate_or_missing_doc_id")
        else:
            seen_ids.add(doc_id)
        if not _non_empty_text(source_type) or not _non_empty_text(doc.get("text")):
            invalidate(doc, "invalid_evidence_fields")
        else:
            source_counts[source_type] += 1
        if not isinstance(metadata, dict):
            invalidate(doc, "invalid_evidence_fields")
            continue
        if any(not _non_empty_text(metadata.get(key)) for key in ("snapshot_id", "fetched_at", "source")):
            invalidate(doc, "invalid_evidence_fields")
        if metadata.get("snapshot_id") != snapshot_id:
            invalidate(doc, "snapshot_id_mismatch")
        if metadata.get("fetched_at") != snapshot.get("fetched_at"):
            invalidate(doc, "snapshot_metadata_mismatch")
        if metadata.get("sample_battles") != snapshot.get("sample_battles"):
            invalidate(doc, "snapshot_metadata_mismatch")
        if metadata.get("source") != "Supercell API live sample":
            invalidate(doc, "source_mismatch")
        if _doc_text_has_missing_value(doc):
            invalidate(doc, "invalid_evidence_fields")

    try:
        expected_documents = build_snapshot_rag_documents(snapshot)
    except (KeyError, TypeError, ValueError):
        expected_documents = []
        failures.add("rag_document_build_failed")
    expected_by_id = {
        doc.get("doc_id"): doc
        for doc in expected_documents
        if isinstance(doc, dict) and _non_empty_text(doc.get("doc_id"))
    }
    actual_by_id = {
        doc.get("doc_id"): doc
        for doc in documents
        if isinstance(doc, dict) and _non_empty_text(doc.get("doc_id"))
    }
    if set(expected_by_id) != set(actual_by_id):
        failures.add("rag_document_coverage_mismatch")
        invalid_doc_ids.update(str(doc_id) for doc_id in set(expected_by_id) ^ set(actual_by_id))
    for doc_id in set(expected_by_id) & set(actual_by_id):
        if expected_by_id[doc_id] != actual_by_id[doc_id]:
            source_type = expected_by_id[doc_id].get("source_type")
            if source_type == "card":
                failure = "card_document_mismatch"
            elif source_type == "deck":
                failure = "deck_document_mismatch"
            elif source_type == "matchup":
                failure = "matchup_document_mismatch"
            else:
                failure = "aggregate_document_mismatch"
            invalidate(actual_by_id[doc_id], failure)

    for doc in documents:
        if not isinstance(doc, dict) or not isinstance(doc.get("metadata"), dict):
            continue
        metadata = doc["metadata"]
        source_type = doc.get("source_type")
        if source_type == "card":
            valid = (
                _non_empty_text(metadata.get("card_name"))
                and isinstance(metadata.get("rank"), int)
                and metadata["rank"] > 0
                and _rate_valid(metadata.get("usage_rate"))
                and _rate_valid(metadata.get("win_rate"))
                and _rate_valid(metadata.get("clean_win_rate"))
                and isinstance(metadata.get("appearance_count"), int)
                and metadata["appearance_count"] >= 0
            )
            if not valid:
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "deck":
            cards = metadata.get("cards")
            valid = (
                _non_empty_text(metadata.get("deck_name"))
                and isinstance(metadata.get("rank"), int)
                and metadata["rank"] > 0
                and isinstance(cards, list)
                and len(cards) == 8
                and len(set(cards)) == 8
                and all(_non_empty_text(card) for card in cards)
                and isinstance(metadata.get("battles"), int)
                and metadata["battles"] > 0
                and _rate_valid(metadata.get("sample_win_rate"))
            )
            if not valid:
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "matchup":
            valid = (
                _non_empty_text(metadata.get("deck_name"))
                and _non_empty_text(metadata.get("opponent_deck_name"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_MATCHUP_RAG_EVIDENCE_GAMES
                and isinstance(metadata.get("wins"), int)
                and 0 <= metadata["wins"] <= metadata["games"]
                and _rate_valid(metadata.get("win_rate"))
            )
            if not valid:
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "card_profile":
            if not (
                _non_empty_text(metadata.get("card_name"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] > 0
                and _rate_valid(metadata.get("win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "deck_profile":
            cards = metadata.get("cards")
            if not (
                _non_empty_text(metadata.get("deck_name"))
                and isinstance(cards, list)
                and len(cards) == 8
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_AGGREGATE_EVIDENCE_GAMES
                and _rate_valid(metadata.get("sample_win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "archetype":
            if not (
                _non_empty_text(metadata.get("archetype"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] > 0
                and _rate_valid(metadata.get("win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "card_pair":
            cards = metadata.get("cards")
            if not (
                isinstance(cards, list)
                and len(cards) == 2
                and all(_non_empty_text(card) for card in cards)
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_AGGREGATE_EVIDENCE_GAMES
                and _rate_valid(metadata.get("sample_win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type == "counter":
            if not (
                _non_empty_text(metadata.get("card_name"))
                and _non_empty_text(metadata.get("opponent_card_name"))
                and isinstance(metadata.get("games"), int)
                and metadata["games"] >= MIN_AGGREGATE_EVIDENCE_GAMES
                and _rate_valid(metadata.get("win_rate"))
            ):
                invalidate(doc, "invalid_evidence_fields")
        elif source_type != "snapshot":
            invalidate(doc, "unknown_source_type")

    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id or None,
        "docs_fingerprint": compute_rag_docs_fingerprint(documents),
        "document_count": len(documents),
        "source_counts": dict(source_counts),
        "card_documents_checked": source_counts.get("card", 0),
        "deck_documents_checked": source_counts.get("deck", 0),
        "matchup_documents_checked": source_counts.get("matchup", 0),
        "passed": not failures,
        "failures": sorted(failures),
        "invalid_doc_ids": sorted(invalid_doc_ids),
    }


def publish_daily_snapshot(snapshot: dict, data_dir: Path) -> dict:
    """Atomically publish a complete official snapshot and its derived datasets."""
    if not is_complete_daily_snapshot(snapshot):
        raise ValueError("refusing to publish an incomplete official weekly snapshot")

    if snapshot.get("collection_scope") == PATH_OF_LEGEND_COLLECTION_SCOPE:
        if snapshot.get("scope_contract") != PATH_OF_LEGEND_SCOPE_CONTRACT:
            raise ValueError("refusing to publish a snapshot with an invalid Path of Legend scope contract")
        scope_record_count = 0
        invalid_scope_records = 0
        for record in snapshot.get("raw_battles", []):
            scope_record_count += 1
            if not isinstance(record, dict) or record.get("battle_type") != PATH_OF_LEGEND_BATTLE_TYPE:
                invalid_scope_records += 1
        if scope_record_count != int(snapshot.get("sample_battles") or 0):
            raise ValueError("refusing to publish a Path of Legend snapshot with an incomplete raw corpus")
        if invalid_scope_records:
            raise ValueError(
                "refusing to publish a Path of Legend snapshot containing "
                f"{invalid_scope_records} out-of-scope raw battles"
            )

    published = dict(snapshot)
    published["snapshot_id"] = _snapshot_id(published)
    published.setdefault("published_at", datetime.now(timezone.utc).isoformat())
    published["cards_meta"] = _with_snapshot_metadata(published.get("cards_meta"), published)
    published["top_decks"] = _with_snapshot_metadata(published.get("top_decks"), published)
    published["deck_matchups"] = _with_snapshot_metadata(published.get("deck_matchups"), published)
    raw_battles = published.get("raw_battles")
    streamed = isinstance(raw_battles, JsonlRecordSequence)
    if streamed and len(raw_battles) != int(published.get("sample_battles") or 0):
        raise ValueError("refusing to publish a streamed snapshot with an incomplete raw corpus")
    aggregate_source = published.get("_aggregate_store_path")
    if streamed:
        if not isinstance(aggregate_source, str) or not Path(aggregate_source).is_file():
            raise ValueError("refusing to publish a streamed snapshot without its exact aggregate store")
        published["aggregate_store"] = {
            "schema_version": 1,
            "canonical_file": f"{SNAPSHOT_ARCHIVE_DIR_NAME}/{published['snapshot_id']}/aggregates.sqlite",
            "archive_file": "aggregates.sqlite",
            "exact_matchups": int(published.get("collection_metrics", {}).get("exact_matchups_stored", 0)),
        }
        published["raw_battles_storage"] = {
            "format": "canonical_json_array",
            "canonical_file": SNAPSHOT_FILE_NAME,
            "record_count": len(raw_battles),
        }
    if not isinstance(published.get("card_deck_stats"), dict):
        published["card_deck_stats"] = build_card_deck_stats(
            list(published.get("raw_battles", [])),
            fetched_at=str(published.get("fetched_at") or ""),
            sample_battles=int(published.get("sample_battles") or 0),
            target_battles=int(published.get("target_battles") or 0),
        )
    documents = build_snapshot_rag_documents(published)
    published["rag_document_counts"] = dict(Counter(document["source_type"] for document in documents))
    validation = validate_snapshot_rag_documents(published, documents)
    if not validation["passed"]:
        raise ValueError(
            "refusing to publish invalid RAG evidence: " + ", ".join(validation["failures"])
        )
    published["rag_docs_fingerprint"] = validation["docs_fingerprint"]
    published["rag_document_validation"] = {
        key: validation[key]
        for key in (
            "schema_version",
            "snapshot_id",
            "docs_fingerprint",
            "document_count",
            "source_counts",
            "card_documents_checked",
            "deck_documents_checked",
            "matchup_documents_checked",
            "passed",
            "failures",
        )
    }

    # The canonical file is written last. Readers either use the previous full
    # snapshot or the new complete set; they never accept a partial collection.
    _atomic_write_json(data_dir / "cards_meta.json", published["cards_meta"])
    _atomic_write_json(data_dir / "top_decks.json", published["top_decks"])
    _atomic_write_json(data_dir / "rag_documents.json", documents)
    _archive_published_snapshot(data_dir, published, documents)
    _atomic_write_json(data_dir / SNAPSHOT_FILE_NAME, published)
    _atomic_write_json(
        data_dir / SNAPSHOT_POINTER_FILE_NAME,
        {
            "schema_version": 1,
            "snapshot_id": published["snapshot_id"],
            "published_at": published.get("published_at"),
        },
    )
    if not streamed:
        return published

    compact = {key: value for key, value in published.items() if not str(key).startswith("_")}
    compact["raw_battles"] = []
    compact["raw_battles_storage"] = {**compact["raw_battles_storage"], "loaded": False}
    work_dir = snapshot.get("_streaming_work_dir")
    if isinstance(work_dir, str):
        work_path = Path(work_dir).resolve()
        aggregate_path = Path(aggregate_source).resolve()
        expected_work_root = (data_dir / "snapshot_work").resolve()
        if (
            work_path.is_dir()
            and work_path.name.startswith("collection-")
            and work_path.parent == expected_work_root
            and aggregate_path.parent == work_path
        ):
            shutil.rmtree(work_path)
    return compact


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
    migrated = False
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
        migrated = True

    # Evidence schema and code can evolve without collecting another 200,000
    # battles. Re-derive every compatibility file from the canonical official
    # snapshot and publish it only after the full-corpus validator passes.
    documents = build_snapshot_rag_documents(snapshot)
    validation = validate_snapshot_rag_documents(snapshot, documents)
    if not validation["passed"]:
        return None
    expected_fingerprint = validation["docs_fingerprint"]
    documents_path = data_dir / "rag_documents.json"
    try:
        current_documents = json.loads(documents_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current_documents = None
    current_fingerprint = (
        compute_rag_docs_fingerprint(current_documents)
        if isinstance(current_documents, list)
        else None
    )
    try:
        derived_files_use_crlf = any(
            b"\r\n" in candidate.read_bytes()
            for candidate in (
                data_dir / "cards_meta.json",
                data_dir / "top_decks.json",
                documents_path,
            )
            if candidate.exists()
        )
    except OSError:
        derived_files_use_crlf = True
    if (
        snapshot.get("rag_docs_fingerprint") != expected_fingerprint
        or current_fingerprint != expected_fingerprint
        or derived_files_use_crlf
    ):
        snapshot["rag_document_counts"] = dict(Counter(document["source_type"] for document in documents))
        snapshot["rag_docs_fingerprint"] = expected_fingerprint
        snapshot["rag_document_validation"] = {
            key: validation[key]
            for key in (
                "schema_version",
                "snapshot_id",
                "docs_fingerprint",
                "document_count",
                "source_counts",
                "card_documents_checked",
                "deck_documents_checked",
                "matchup_documents_checked",
                "passed",
                "failures",
            )
        }
        _atomic_write_json(data_dir / "cards_meta.json", snapshot.get("cards_meta", []))
        _atomic_write_json(data_dir / "top_decks.json", snapshot.get("top_decks", []))
        _atomic_write_json(documents_path, documents)
        migrated = True
    if migrated:
        _atomic_write_json(path, snapshot)
    return snapshot
