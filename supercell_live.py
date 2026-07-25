"""Bounded adapter for official Clash Royale API leaderboard battle logs."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import threading
import time
from urllib.parse import quote

import requests


SUPERCELL_API_BASE_URL = "https://api.clashroyale.com/v1"
SUPERCELL_SOURCE_URL = "https://developer.clashroyale.com/"
CARD_DECK_VARIANTS_PER_CARD = 20


class SupercellAPIClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 5.0,
        session=requests,
        max_retries: int = 0,
        requests_per_second: float = 0.0,
        sleeper=time.sleep,
    ):
        if not token:
            raise ValueError("SUPERCELL_API_TOKEN is required")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.session = session
        self.max_retries = max(0, max_retries)
        self.requests_per_second = max(0.0, requests_per_second)
        self.sleeper = sleeper
        self.metrics = defaultdict(float)
        self._pacer_lock = threading.Lock()
        self._next_request_at = 0.0
        self._cooldown_until = 0.0

    def _wait_for_request_slot(self) -> None:
        if self.requests_per_second <= 0:
            return
        with self._pacer_lock:
            now = time.monotonic()
            start_at = max(now, self._next_request_at, self._cooldown_until)
            self._next_request_at = start_at + 1.0 / self.requests_per_second
        wait_seconds = max(0.0, start_at - now)
        if wait_seconds:
            self.metrics["throttle_wait_seconds"] += wait_seconds
            self.sleeper(wait_seconds)

    def _apply_cooldown(self, seconds: float) -> None:
        with self._pacer_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + max(0.0, seconds))

    @staticmethod
    def _retry_after_seconds(response, attempt: int) -> float:
        headers = getattr(response, "headers", {}) or {}
        try:
            return max(0.0, float(headers.get("Retry-After", "")))
        except (TypeError, ValueError):
            return float(2**attempt)

    def _get_json(self, path: str, *, params: dict | None = None):
        for attempt in range(self.max_retries + 1):
            try:
                self._wait_for_request_slot()
                self.metrics["request_count"] += 1
                response = self.session.get(
                    f"{SUPERCELL_API_BASE_URL}{path}",
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                    params=params,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise ValueError("official API returned an unsupported JSON payload")
                self.metrics["successful_requests"] += 1
                return payload
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                if getattr(response, "status_code", None) == 429:
                    self.metrics["rate_limited"] += 1
                    delay = self._retry_after_seconds(response, attempt)
                    self._apply_cooldown(delay)
                else:
                    delay = float(2**attempt)
                if attempt >= self.max_retries:
                    self.metrics["failed_requests"] += 1
                    raise
            except requests.RequestException:
                delay = float(2**attempt)
                if attempt >= self.max_retries:
                    self.metrics["failed_requests"] += 1
                    raise
            self.metrics["retried_requests"] += 1
            self.metrics["retry_wait_seconds"] += delay
            self.sleeper(delay)

    def fetch_global_rankings(self, limit: int) -> list[dict]:
        """Return the highest-ranked players first, following API cursors as needed."""
        for path in (
            "/locations/global/rankings/players",
            "/locations/global/pathoflegend/players",
        ):
            try:
                players = self._fetch_rankings_path(path, limit)
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) == 404:
                    continue
                raise
            if players:
                return players
        return []

    def _fetch_rankings_path(self, path: str, limit: int) -> list[dict]:
        """Fetch ranking pages without reordering or requesting lower ranks prematurely."""
        players: list[dict] = []
        seen_tags: set[str] = set()
        after: str | None = None
        page_size = min(1000, limit)

        while len(players) < limit:
            params = {"limit": min(page_size, limit - len(players))}
            if after:
                params["after"] = after
            payload = self._get_json(path, params=params)
            self.metrics["ranking_pages"] += 1
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError("official API rankings response has no items list")

            for item in items:
                tag = item.get("tag") if isinstance(item, dict) else None
                normalized_tag = tag.strip().upper() if isinstance(tag, str) else ""
                if not normalized_tag or normalized_tag in seen_tags:
                    continue
                seen_tags.add(normalized_tag)
                players.append(item)
                if len(players) >= limit:
                    return players

            paging = payload.get("paging") if isinstance(payload, dict) else None
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            next_after = cursors.get("after") if isinstance(cursors, dict) else None
            if not isinstance(next_after, str) or not next_after.strip() or next_after == after:
                break
            after = next_after
        return players

    def fetch_battle_log(self, player_tag: str) -> list[dict]:
        payload = self._get_json(f"/players/{quote(player_tag, safe='')}/battlelog")
        if not isinstance(payload, list):
            raise ValueError("official API battle log response is not a list")
        return [item for item in payload if isinstance(item, dict)]

    def fetch_snapshot(
        self,
        *,
        target_battles: int = 400,
        player_limit: int = 1000,
        battles_per_player: int = 25,
        concurrency: int = 8,
        fallback_player_tags: tuple[str, ...] = (),
        battle_log_cache: dict[str, tuple[float, list[dict]]] | None = None,
        battle_log_cache_ttl_seconds: float = 0.0,
        max_duration_seconds: float | None = None,
    ) -> dict:
        if target_battles < 1:
            raise ValueError("target_battles must be at least 1")
        if player_limit < 1:
            raise ValueError("player_limit must be at least 1")
        if battles_per_player < 1:
            raise ValueError("battles_per_player must be at least 1")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")

        started_at = time.monotonic()
        self.metrics.clear()
        players = self.fetch_global_rankings(player_limit)
        if not players:
            players = [{"tag": tag} for tag in fallback_player_tags]
        battle_logs = {}
        failed_players = 0
        fetched_players = 0
        sampled_players = 0
        usable_battles = 0
        seen_battle_ids: set[str] = set()
        selection_metrics: defaultdict[str, int] = defaultdict(int)
        refresh_budget_exhausted = False

        for start in range(0, len(players), concurrency):
            if max_duration_seconds is not None and time.monotonic() - started_at >= max_duration_seconds:
                refresh_budget_exhausted = True
                break
            batch = players[start : start + concurrency]
            cached = []
            to_fetch = []
            cache_now = time.monotonic()
            for player in batch:
                tag = player["tag"]
                cache_item = (battle_log_cache or {}).get(tag)
                if cache_item and cache_now - cache_item[0] < battle_log_cache_ttl_seconds:
                    cached.append((player, cache_item[1], None))
                    self.metrics["cache_hits"] += 1
                else:
                    to_fetch.append(player)

            if not to_fetch:
                fetched = cached
            elif concurrency == 1:
                fetched = list(cached)
                for player in to_fetch:
                    try:
                        fetched.append((player, self.fetch_battle_log(player["tag"]), None))
                    except (requests.RequestException, ValueError) as exc:
                        fetched.append((player, [], exc))
            else:
                with ThreadPoolExecutor(max_workers=min(concurrency, len(to_fetch))) as executor:
                    futures = [(player, executor.submit(self.fetch_battle_log, player["tag"])) for player in to_fetch]
                    fetched = list(cached)
                    for player, future in futures:
                        try:
                            fetched.append((player, future.result(), None))
                        except (requests.RequestException, ValueError) as exc:
                            fetched.append((player, [], exc))

            for player, battles, error in fetched:
                fetched_players += 1
                if error is not None:
                    failed_players += 1
                    continue
                selection_metrics["raw_battle_records"] += len(battles)
                # Recent battle-log entries can be deckless event records. Filter first so
                # a short prefix does not discard an otherwise usable player sample.
                selected = select_usable_battles(
                    battles,
                    battles_per_player,
                    seen_battle_ids=seen_battle_ids,
                    observer_tag=player.get("tag"),
                    selection_metrics=selection_metrics,
                )
                if selected:
                    if battle_log_cache is not None:
                        battle_log_cache[player["tag"]] = (time.monotonic(), battles)
                    sampled_players += 1
                    usable_battles += len(selected)
                    battle_logs[player["tag"]] = selected

            if usable_battles >= target_battles:
                break

        return build_live_snapshot(
            players,
            battle_logs,
            target_battles=target_battles,
            collection_metadata={
                "ranked_players": len(players),
                "fetched_players": fetched_players,
                "sampled_players": sampled_players,
                "failed_players": failed_players,
                "usable_battles": usable_battles,
                "leaderboard_candidate_limit": player_limit,
                "leaderboard_start_rank": _ranking_position(players, 0),
                "leaderboard_last_scanned_rank": _ranking_position(players, fetched_players - 1),
                "raw_battle_records": int(selection_metrics["raw_battle_records"]),
                "inspected_battle_records": int(selection_metrics["inspected_battle_records"]),
                "duplicates_skipped": int(selection_metrics["duplicates_skipped"]),
                "deckless_or_invalid_records": int(selection_metrics["deckless_or_invalid_records"]),
                "collection_duration_seconds": round(time.monotonic() - started_at, 3),
                "refresh_budget_exhausted": refresh_budget_exhausted,
                "request_count": int(self.metrics["request_count"]),
                "successful_requests": int(self.metrics["successful_requests"]),
                "failed_requests": int(self.metrics["failed_requests"]),
                "rate_limited": int(self.metrics["rate_limited"]),
                "retried_requests": int(self.metrics["retried_requests"]),
                "cache_hits": int(self.metrics["cache_hits"]),
                "throttle_wait_seconds": round(self.metrics["throttle_wait_seconds"], 3),
                "retry_wait_seconds": round(self.metrics["retry_wait_seconds"], 3),
                "ranking_pages": int(self.metrics["ranking_pages"]),
            },
        )


def _ranking_position(players: list[dict], index: int) -> int | None:
    if index < 0 or index >= len(players):
        return None
    rank = players[index].get("rank") if isinstance(players[index], dict) else None
    try:
        return int(rank)
    except (TypeError, ValueError):
        return index + 1


def _team_member(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _team_cards(battle: dict) -> list[dict]:
    return _side_cards(_team_member(battle.get("team")))


def _opponent_cards(battle: dict) -> list[dict]:
    return _side_cards(_team_member(battle.get("opponent")))


def _side_cards(member: dict | None) -> list[dict]:
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


def _deck_signature(cards: list[dict]) -> tuple[str, ...]:
    return tuple(sorted(str(card["name"]).strip() for card in cards if str(card.get("name", "")).strip()))


def _side_tag(member: dict | None) -> str | None:
    value = member.get("tag") if isinstance(member, dict) else None
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _crowns(member: dict | None) -> int:
    try:
        return int((member or {}).get("crowns", 0) or 0)
    except (TypeError, ValueError):
        return 0


def normalize_battle_record(battle: dict, observer_tag: str | None = None) -> dict | None:
    """Create a source-preserving, order-independent record for one battle."""
    if not isinstance(battle, dict):
        return None
    team = _team_member(battle.get("team"))
    opponent = _team_member(battle.get("opponent"))
    team_cards = _team_cards(battle)
    if not team_cards:
        return None
    opponent_cards = _opponent_cards(battle)
    team_deck = _deck_signature(team_cards)
    opponent_deck = _deck_signature(opponent_cards)
    team_tag = _side_tag(team) or (observer_tag.strip().upper() if isinstance(observer_tag, str) and observer_tag.strip() else None)
    opponent_tag = _side_tag(opponent)
    timestamp = str(battle.get("battleTime") or battle.get("battle_time") or "")

    # A player can appear in the global ranking alongside their opponent. The
    # same battle then appears twice with sides reversed, so the fingerprint is
    # deliberately independent of the observer's side.
    sides = sorted(
        (
            (team_tag or "", team_deck, _crowns(team)),
            (opponent_tag or "", opponent_deck, _crowns(opponent)),
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
        "battle_time": timestamp or None,
        "team_tag": team_tag,
        "opponent_tag": opponent_tag,
        "team_deck": list(team_deck),
        "opponent_deck": list(opponent_deck),
        "team_crowns": _crowns(team),
        "opponent_crowns": _crowns(opponent),
        "won": _crowns(team) > _crowns(opponent),
    }


def select_usable_battles(
    battles: list[dict],
    limit: int,
    *,
    seen_battle_ids: set[str] | None = None,
    observer_tag: str | None = None,
    selection_metrics: dict[str, int] | None = None,
) -> list[dict]:
    """Keep bounded, unique entries that contain a team deck."""
    usable = []
    seen = seen_battle_ids if seen_battle_ids is not None else set()
    for battle in battles:
        if selection_metrics is not None:
            selection_metrics["inspected_battle_records"] = selection_metrics.get("inspected_battle_records", 0) + 1
        record = normalize_battle_record(battle, observer_tag)
        if record is None:
            if selection_metrics is not None:
                selection_metrics["deckless_or_invalid_records"] = selection_metrics.get("deckless_or_invalid_records", 0) + 1
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


def _is_win(battle: dict) -> bool:
    team = _team_member(battle.get("team"))
    opponent = _team_member(battle.get("opponent"))
    if team is None or opponent is None:
        return False
    try:
        return int(team.get("crowns", 0) or 0) > int(opponent.get("crowns", 0) or 0)
    except (TypeError, ValueError):
        return False


def build_live_snapshot(
    players: list[dict],
    battle_logs: dict[str, list[dict]],
    *,
    fetched_at: str | None = None,
    target_battles: int | None = None,
    collection_metadata: dict | None = None,
) -> dict:
    """Derive labelled sample metrics from public leaderboard battle logs."""
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    card_usage: Counter[str] = Counter()
    card_wins: Counter[str] = Counter()
    deck_usage: Counter[tuple[str, ...]] = Counter()
    deck_wins: Counter[tuple[str, ...]] = Counter()
    deck_elixir: dict[tuple[str, ...], list[float]] = defaultdict(list)
    matchup_games: Counter[tuple[tuple[str, ...], tuple[str, ...]]] = Counter()
    matchup_wins: Counter[tuple[tuple[str, ...], tuple[str, ...]]] = Counter()
    raw_battles: list[dict] = []
    seen_battle_ids: set[str] = set()
    total_battles = 0
    battle_records = 0
    deck_records = 0
    reached_target = False

    for player in players:
        tag = player.get("tag")
        for battle in battle_logs.get(tag, []):
            battle_records += 1
            record = normalize_battle_record(battle, tag)
            battle_id = record.get("battle_id") if record else None
            if record is None or (battle_id is not None and battle_id in seen_battle_ids):
                continue
            if battle_id is not None:
                seen_battle_ids.add(battle_id)
            cards = _team_cards(battle)
            deck_records += 1
            total_battles += 1
            raw_battles.append(record)
            names = tuple(record["team_deck"])
            won = bool(record["won"])
            deck_usage[names] += 1
            deck_wins[names] += int(won)
            costs = [float(card["elixirCost"]) for card in cards if isinstance(card.get("elixirCost"), (int, float))]
            if costs:
                deck_elixir[names].append(sum(costs) / len(costs))
            for card_name in names:
                card_usage[card_name] += 1
                card_wins[card_name] += int(won)
            opponent_deck = tuple(record["opponent_deck"])
            if opponent_deck:
                matchup_key = (names, opponent_deck)
                matchup_games[matchup_key] += 1
                matchup_wins[matchup_key] += int(won)
            if target_battles is not None and total_battles >= target_battles:
                reached_target = True
                break
        if reached_target:
            break

    cards_meta = []
    for rank, (card_name, usage) in enumerate(card_usage.most_common(), start=1):
        wins = card_wins[card_name]
        cards_meta.append(
            {
                "rank": rank,
                "card_name": card_name,
                "rating": 0,
                "usage_rate": round(usage / total_battles * 100, 1) if total_battles else 0.0,
                "usage_delta": 0.0,
                "win_rate": round(wins / usage * 100, 1) if usage else 0.0,
                "win_delta": 0.0,
                "clean_win_rate": round(wins / usage * 100, 1) if usage else 0.0,
                "mode": "Official leaderboard battle-log sample",
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": total_battles,
                "target_battles": target_battles or total_battles,
                "appearance_count": usage,
            }
        )

    top_decks = []
    for rank, (deck, battles) in enumerate(deck_usage.most_common(30), start=1):
        top_decks.append(
            {
                "rank": rank,
                "player_name": "Global leaderboard sample",
                "clan_name": "Official Supercell API",
                "deck_name": " / ".join(deck),
                "avg_elixir": round(sum(deck_elixir[deck]) / len(deck_elixir[deck]), 1) if deck_elixir[deck] else None,
                "battles": battles,
                "trophies": None,
                "last_ladder_battle": fetched_at,
                "cards": list(deck),
                "sample_win_rate": round(deck_wins[deck] / battles * 100, 1) if battles else 0.0,
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": total_battles,
                "target_battles": target_battles or total_battles,
            }
        )

    deck_matchups = []
    for (deck, opponent_deck), games in sorted(matchup_games.items(), key=lambda item: item[1], reverse=True):
        wins = matchup_wins[(deck, opponent_deck)]
        deck_matchups.append(
            {
                "deck_name": " / ".join(deck),
                "opponent_deck_name": " / ".join(opponent_deck),
                "games": games,
                "wins": wins,
                "win_rate": round(wins / games * 100, 1) if games else 0.0,
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": total_battles,
                "target_battles": target_battles or total_battles,
            }
        )

    if not total_battles:
        raise ValueError(
            "official API returned no usable battle-log decks "
            f"(players={len(players)}, battle_records={battle_records}, deck_records={deck_records})"
        )
    collection_metadata = collection_metadata or {}
    target = target_battles or total_battles
    card_deck_stats = build_card_deck_stats(
        raw_battles,
        fetched_at=fetched_at,
        sample_battles=total_battles,
        target_battles=target,
    )
    return {
        "cards_meta": cards_meta,
        "top_decks": top_decks,
        "card_deck_stats": card_deck_stats,
        "deck_matchups": deck_matchups,
        "raw_battles": raw_battles,
        "fetched_at": fetched_at,
        "sample_battles": total_battles,
        "target_battles": target,
        "shortfall_battles": max(target - total_battles, 0),
        "ranked_players": collection_metadata.get("ranked_players", len(players)),
        "fetched_players": collection_metadata.get("fetched_players", len(battle_logs)),
        "sampled_players": collection_metadata.get("sampled_players", len([items for items in battle_logs.values() if items])),
        "failed_players": collection_metadata.get("failed_players", 0),
        "usable_battles": collection_metadata.get("usable_battles", total_battles),
        "leaderboard_candidate_limit": collection_metadata.get("leaderboard_candidate_limit"),
        "leaderboard_start_rank": collection_metadata.get("leaderboard_start_rank"),
        "leaderboard_last_scanned_rank": collection_metadata.get("leaderboard_last_scanned_rank"),
        "collection_metrics": {
            key: value
            for key, value in collection_metadata.items()
            if key
            not in {
                "ranked_players",
                "fetched_players",
                "sampled_players",
                "failed_players",
                "usable_battles",
                "leaderboard_candidate_limit",
                "leaderboard_start_rank",
                "leaderboard_last_scanned_rank",
            }
        },
    }


def build_card_deck_stats(
    raw_battles: list[dict],
    *,
    fetched_at: str,
    sample_battles: int,
    target_battles: int,
    variants_per_card: int = CARD_DECK_VARIANTS_PER_CARD,
) -> dict[str, list[dict]]:
    """Aggregate the most observed exact decks containing each card.

    ``top_decks`` deliberately keeps only the global top 30 exact decks. That
    cannot answer a card-filtered question when a card has many viable build
    variants, so this index is derived from every normalized battle in the same
    official snapshot.
    """
    deck_usage: Counter[tuple[str, ...]] = Counter()
    deck_wins: Counter[tuple[str, ...]] = Counter()
    decks_by_card: defaultdict[str, set[tuple[str, ...]]] = defaultdict(set)

    for record in raw_battles:
        if not isinstance(record, dict):
            continue
        deck = tuple(str(card).strip() for card in record.get("team_deck", []) if isinstance(card, str) and card.strip())
        if not deck:
            continue
        deck_usage[deck] += 1
        deck_wins[deck] += int(bool(record.get("won")))
        for card_name in deck:
            decks_by_card[card_name].add(deck)

    result: dict[str, list[dict]] = {}
    for card_name, decks in decks_by_card.items():
        ranked = sorted(decks, key=lambda deck: (-deck_usage[deck], deck))[:variants_per_card]
        result[card_name] = [
            {
                "deck_name": " / ".join(deck),
                "cards": list(deck),
                "battles": deck_usage[deck],
                "sample_win_rate": round(deck_wins[deck] / deck_usage[deck] * 100, 1),
                "source": "Supercell API live sample",
                "source_url": SUPERCELL_SOURCE_URL,
                "fetched_at": fetched_at,
                "sample_battles": sample_battles,
                "target_battles": target_battles,
            }
            for deck in ranked
        ]
    return result
