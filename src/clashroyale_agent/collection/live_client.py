from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Callable
from urllib.parse import quote

import requests

from clashroyale_agent.collection.api_client import OfficialAPIRequester, SUPERCELL_API_BASE_URL
from clashroyale_agent.collection.battle_parser import (
    normalize_player_tag as _normalize_player_tag,
    opponent_tags_from_battles,
    select_usable_battles,
)
from clashroyale_agent.collection.live_snapshot import (
    MAX_RANKING_SEED_LOCATIONS,
    PATH_OF_LEGEND_COLLECTION_SCOPE,
    PATH_OF_LEGEND_SCOPE_CONTRACT,
    _official_player_rank,
    _ranking_position,
    build_live_snapshot,
)
from clashroyale_agent.collection.snapshot_workspace import DiskBackedSnapshotWorkspace


logger = logging.getLogger(__name__)


def _append_unique_player(players: list[dict], seen_tags: set[str], player: dict, *, source: str) -> bool:
    tag = _normalize_player_tag(player.get("tag") if isinstance(player, dict) else None)
    if not tag or tag in seen_tags:
        return False
    record = dict(player)
    record["tag"] = tag
    record.setdefault("seed_source", source)
    seen_tags.add(tag)
    players.append(record)
    return True


class SupercellAPIClient(OfficialAPIRequester):
    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 5.0,
        session=None,
        session_factory=requests.Session,
        max_retries: int = 0,
        requests_per_second: float = 0.0,
        sleeper=time.sleep,
        clock=time.monotonic,
    ):
        super().__init__(
            token,
            base_url=SUPERCELL_API_BASE_URL,
            timeout_seconds=timeout_seconds,
            session=session,
            session_factory=session_factory,
            max_retries=max_retries,
            requests_per_second=requests_per_second,
            sleeper=sleeper,
            clock=clock,
        )

    def fetch_locations(self) -> list[dict]:
        payload = self._get_json("/locations")
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("official API locations response has no items list")
        return [item for item in items if isinstance(item, dict)]

    def fetch_global_rankings(
        self,
        limit: int,
        *,
        include_locations: bool = False,
        location_limit: int = MAX_RANKING_SEED_LOCATIONS,
    ) -> list[dict]:
        """Return unique Path of Legend player seeds from official leaderboard endpoints."""
        players: list[dict] = []
        seen_tags: set[str] = set()

        def add_from_path(path: str, source: str) -> None:
            if len(players) >= limit:
                return
            try:
                for player in self._fetch_rankings_path(path, limit - len(players)):
                    if _append_unique_player(players, seen_tags, player, source=source) and len(players) >= limit:
                        return
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) == 404:
                    return
                raise

        add_from_path("/locations/global/pathoflegend/players", "global_path_of_legend")
        if not include_locations or len(players) >= limit:
            return players

        try:
            locations = self.fetch_locations()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("official location seed expansion failed: %s", type(exc).__name__)
            return players

        scanned_locations = 0
        for location in locations:
            if len(players) >= limit or scanned_locations >= max(0, int(location_limit)):
                break
            location_id = location.get("id")
            if location_id in (None, "", "global"):
                continue
            location_key = str(location_id).strip()
            if not location_key:
                continue
            scanned_locations += 1
            add_from_path(f"/locations/{quote(location_key, safe='')}/pathoflegend/players", "location_path_of_legend")
        self.metrics["ranking_locations_scanned"] += scanned_locations
        return players

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
                ranked_item = dict(item)
                ranked_item.setdefault("rank", len(players) + 1)
                players.append(ranked_item)
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
        seed_player_limit: int = 1000,
        battles_per_player: int = 25,
        concurrency: int = 8,
        fallback_player_tags: tuple[str, ...] = (),
        battle_log_cache: dict[str, tuple[float, list[dict]]] | None = None,
        battle_log_cache_ttl_seconds: float = 0.0,
        max_duration_seconds: float | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        progress_interval_seconds: float = 60.0,
        spool_dir: Path | None = None,
        collection_mode: str = "weekly_expanded",
        expand_opponents: bool = True,
        strict_battle_contract: bool = False,
        ranked_tail_retry_rounds: int = 0,
        max_workspace_bytes: int | None = None,
        export_raw_battles: bool = True,
    ) -> dict:
        if target_battles < 1:
            raise ValueError("target_battles must be at least 1")
        if player_limit < 1:
            raise ValueError("player_limit must be at least 1")
        if seed_player_limit < 1:
            raise ValueError("seed_player_limit must be at least 1")
        if battles_per_player < 1:
            raise ValueError("battles_per_player must be at least 1")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if collection_mode not in {"daily_ranked", "weekly_expanded"}:
            raise ValueError("collection_mode must be daily_ranked or weekly_expanded")
        if collection_mode == "daily_ranked" and expand_opponents:
            raise ValueError("daily_ranked collection cannot expand opponent tags")

        started_at = time.monotonic()
        self.metrics.clear()
        workspace = (
            DiskBackedSnapshotWorkspace(
                spool_dir,
                target_battles=target_battles,
                player_limit=player_limit,
                battles_per_player=battles_per_player,
                seed_player_limit=seed_player_limit,
                collection_mode=collection_mode,
                max_workspace_bytes=max_workspace_bytes,
            )
            if spool_dir is not None
            else None
        )
        players = workspace.load_players() if workspace is not None else None
        if workspace is not None and players is None and workspace.processed_players:
            workspace.discard()
            workspace = DiskBackedSnapshotWorkspace(
                spool_dir,
                target_battles=target_battles,
                player_limit=player_limit,
                battles_per_player=battles_per_player,
                seed_player_limit=seed_player_limit,
                collection_mode=collection_mode,
                max_workspace_bytes=max_workspace_bytes,
            )
        if players is None:
            players = self.fetch_global_rankings(min(player_limit, seed_player_limit), include_locations=False)
            if not players:
                players = [{"tag": tag} for tag in fallback_player_tags]
            if workspace is not None:
                workspace.save_players(players)
        battle_logs = {} if workspace is None else None
        failed_players = workspace.metadata_int("failed_players") if workspace is not None else 0
        fetched_players = workspace.processed_players if workspace is not None else 0
        sampled_players = workspace.metadata_int("sampled_players") if workspace is not None else 0
        usable_battles = workspace.battle_count if workspace is not None else 0
        seen_battle_ids: set[str] | None = set() if workspace is None else None
        selection_metrics: defaultdict[str, int] = defaultdict(int)
        refresh_budget_exhausted = False
        expanded_players = sum(1 for player in players if player.get("seed_source") == "opponent_battlelog")
        initial_seed_players = max(0, len(players) - expanded_players)
        source_exhausted = False
        queued_player_tags = {
            _normalize_player_tag(player.get("tag"))
            for player in players
            if isinstance(player, dict) and _normalize_player_tag(player.get("tag"))
        }
        last_progress_at = started_at

        def report_progress(*, force: bool = False, final: bool = False) -> None:
            nonlocal last_progress_at
            if progress_callback is None:
                return
            progress_now = time.monotonic()
            if not force and progress_now - last_progress_at < max(0.0, progress_interval_seconds):
                return
            last_progress_at = progress_now
            if refresh_budget_exhausted:
                status = "budget_exhausted"
            elif usable_battles >= target_battles:
                status = "complete"
            elif source_exhausted:
                status = "source_exhausted"
            elif final:
                status = "incomplete"
            else:
                status = "collecting"
            try:
                progress_callback(
                    {
                        "status": status,
                        "target_battles": target_battles,
                        "usable_battles": usable_battles,
                        "fetched_players": fetched_players,
                        "sampled_players": sampled_players,
                        "candidate_players": len(players),
                        "queued_players": len(players),
                        "seed_players": initial_seed_players,
                        "expanded_players": expanded_players,
                        "failed_players": failed_players,
                        "request_count": int(self.metrics["request_count"]),
                        "rate_limited": int(self.metrics["rate_limited"]),
                        "source_exhausted": source_exhausted,
                        "elapsed_seconds": round(progress_now - started_at, 1),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                # Progress reporting must never interrupt official collection.
                logger.warning("snapshot progress callback failed", exc_info=True)

        start = workspace.processed_players if workspace is not None else 0
        while start < len(players):
            if max_duration_seconds is not None and time.monotonic() - started_at >= max_duration_seconds:
                refresh_budget_exhausted = True
                break
            batch = players[start : start + concurrency]
            if not batch:
                break
            batch_positions = {
                _normalize_player_tag(player.get("tag")): start + offset
                for offset, player in enumerate(batch)
                if isinstance(player, dict)
            }
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
                    if workspace is not None:
                        player_index = batch_positions.get(_normalize_player_tag(player.get("tag")), start)
                        is_expanded = player.get("seed_source") == "opponent_battlelog"
                        workspace.record_player(
                            player_index=player_index,
                            player_tag=str(player.get("tag") or ""),
                            battles=[],
                            failed=True,
                            target_battles=target_battles,
                            observer_rank=None if is_expanded else _official_player_rank(player),
                            observer_source="opponent_expansion" if is_expanded else "ranked_direct",
                            expansion_root_rank=player.get("expansion_root_rank"),
                        )
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
                    path_of_legend_only=True,
                    require_complete_decks_and_stable_id=strict_battle_contract,
                )
                if workspace is not None:
                    player_index = batch_positions.get(_normalize_player_tag(player.get("tag")), start)
                    is_expanded = player.get("seed_source") == "opponent_battlelog"
                    accepted = workspace.record_player(
                        player_index=player_index,
                        player_tag=str(player.get("tag") or ""),
                        battles=selected,
                        failed=error is not None,
                        target_battles=target_battles,
                        observer_rank=None if is_expanded else _official_player_rank(player),
                        observer_source="opponent_expansion" if is_expanded else "ranked_direct",
                        expansion_root_rank=player.get("expansion_root_rank"),
                    )
                    usable_battles += accepted
                    sampled_players += int(accepted > 0)
                elif selected:
                    if battle_log_cache is not None:
                        battle_log_cache[player["tag"]] = (time.monotonic(), battles)
                    sampled_players += 1
                    usable_battles += len(selected)
                    battle_logs[player["tag"]] = selected
                if (
                    expand_opponents
                    and selected
                    and player.get("seed_source") != "opponent_battlelog"
                    and len(players) < player_limit
                ):
                    added = 0
                    if player.get("seed_source") == "opponent_battlelog":
                        expansion_root_rank = player.get("expansion_root_rank")
                    else:
                        expansion_root_rank = _official_player_rank(player)
                    for opponent_tag in opponent_tags_from_battles(selected, observer_tag=player.get("tag")):
                        if len(players) >= player_limit:
                            break
                        if opponent_tag in queued_player_tags:
                            continue
                        queued_player_tags.add(opponent_tag)
                        players.append(
                            {
                                "tag": opponent_tag,
                                "seed_source": "opponent_battlelog",
                                "expansion_root_rank": expansion_root_rank,
                            }
                        )
                        added += 1
                    if added:
                        expanded_players += added
                        if workspace is not None:
                            workspace.save_players(players)

            report_progress()

            if self.metrics["rate_limited"]:
                if workspace is not None:
                    workspace.mark_rate_limited(int(self.metrics["rate_limited"]))
                break

            if usable_battles >= target_battles:
                break
            start += len(batch)

        if workspace is not None and not self.metrics["rate_limited"]:
            for _round in range(max(0, int(ranked_tail_retry_rounds))):
                failed_ranked = workspace.failed_ranked_players()
                if not failed_ranked:
                    break
                for player in failed_ranked:
                    if max_duration_seconds is not None and time.monotonic() - started_at >= max_duration_seconds:
                        refresh_budget_exhausted = True
                        break
                    try:
                        battles = self.fetch_battle_log(player["tag"])
                    except (requests.RequestException, ValueError):
                        if self.metrics["rate_limited"]:
                            workspace.mark_rate_limited(int(self.metrics["rate_limited"]))
                            break
                        continue
                    selection_metrics["raw_battle_records"] += len(battles)
                    selected = select_usable_battles(
                        battles,
                        battles_per_player,
                        observer_tag=player["tag"],
                        selection_metrics=selection_metrics,
                        path_of_legend_only=True,
                        require_complete_decks_and_stable_id=strict_battle_contract,
                    )
                    accepted = workspace.record_player(
                        player_index=max(0, int(player.get("rank") or 1) - 1),
                        player_tag=player["tag"],
                        battles=selected,
                        failed=False,
                        target_battles=target_battles,
                        observer_rank=player.get("rank"),
                        observer_source="ranked_direct",
                    )
                    usable_battles += accepted
                    sampled_players += int(accepted > 0)
                    fetched_players += 1
                    if self.metrics["rate_limited"]:
                        workspace.mark_rate_limited(int(self.metrics["rate_limited"]))
                        break
                if refresh_budget_exhausted or self.metrics["rate_limited"]:
                    break
            failed_players = workspace.failed_player_count
            report_progress(force=True)

        source_exhausted = (
            usable_battles < target_battles
            and not refresh_budget_exhausted
            and not self.metrics["rate_limited"]
            and start >= len(players)
        )

        report_progress(force=True, final=True)

        collection_metadata = {
                "ranked_players": initial_seed_players,
                "fetched_players": fetched_players,
                "sampled_players": sampled_players,
                "failed_players": failed_players,
                "usable_battles": usable_battles,
                "collection_mode": collection_mode,
                "expand_opponents": bool(expand_opponents),
                "collection_scope": PATH_OF_LEGEND_COLLECTION_SCOPE,
                "scope_contract": PATH_OF_LEGEND_SCOPE_CONTRACT,
                "scope_verified": bool(strict_battle_contract),
                "seed_player_limit": min(player_limit, seed_player_limit),
                "seed_players": initial_seed_players,
                "queued_players": len(players),
                "expanded_players": expanded_players,
                "source_exhausted": source_exhausted,
                "leaderboard_candidate_limit": min(player_limit, seed_player_limit),
                "player_queue_capacity": player_limit,
                "leaderboard_start_rank": _ranking_position(players, 0),
                "leaderboard_last_scanned_rank": _ranking_position(
                    players, min(fetched_players, initial_seed_players) - 1
                ),
                "raw_battle_records": int(selection_metrics["raw_battle_records"]),
                "inspected_battle_records": int(selection_metrics["inspected_battle_records"]),
                "duplicates_skipped": int(selection_metrics["duplicates_skipped"]),
                "deckless_or_invalid_records": int(selection_metrics["deckless_or_invalid_records"]),
                "non_path_of_legend_records": int(selection_metrics["non_path_of_legend_records"]),
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
            }
        if workspace is not None:
            try:
                return workspace.build_snapshot(
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    target_battles=target_battles,
                    collection_metadata=collection_metadata,
                    export_raw_battles=export_raw_battles,
                )
            finally:
                workspace.close()
        return build_live_snapshot(
            players,
            battle_logs or {},
            target_battles=target_battles,
            collection_metadata=collection_metadata,
        )


__all__ = ["SupercellAPIClient"]
