"""Compatibility facade for official Clash Royale API leaderboard battle logs."""

import app_config  # noqa: F401 - initializes the src package path for root runs.
import requests
import time
from clashroyale_agent.collection.api_client import (
    OfficialAPIRequester,
    SUPERCELL_API_BASE_URL,
    SUPERCELL_SOURCE_URL,
)
from clashroyale_agent.collection.battle_parser import (
    PATH_OF_LEGEND_BATTLE_TYPE,
    is_path_of_legend_battle,
    normalize_battle_record,
    opponent_tags_from_battles,
    select_usable_battles,
    team_cards as _team_cards,
    team_member as _team_member,
)
from clashroyale_agent.collection.live_client import SupercellAPIClient as _PackagedSupercellAPIClient
from clashroyale_agent.collection.live_snapshot import (
    CARD_DECK_VARIANTS_PER_CARD,
    MAX_PUBLISHED_DECK_MATCHUPS,
    MAX_RANKING_SEED_LOCATIONS,
    MAX_RESUMABLE_WORKSPACE_AGE_SECONDS,
    MAX_SPECIAL_FIELD_PROBE_BATTLES,
    PATH_OF_LEGEND_COLLECTION_SCOPE,
    PATH_OF_LEGEND_SCOPE_CONTRACT,
    _is_win,
    _official_player_rank,
    _ranking_position,
    build_card_deck_stats,
    build_live_snapshot,
    probe_official_special_fields,
)
from clashroyale_agent.collection.snapshot_workspace import (
    DiskBackedSnapshotWorkspace,
    JsonlRecordSequence,
)


class SupercellAPIClient(_PackagedSupercellAPIClient):
    def __init__(self, *args, session_factory=None, **kwargs):
        kwargs.setdefault("sleeper", time.sleep)
        kwargs.setdefault("clock", time.monotonic)
        super().__init__(
            *args,
            session_factory=session_factory or requests.Session,
            **kwargs,
        )


__all__ = [
    "CARD_DECK_VARIANTS_PER_CARD",
    "DiskBackedSnapshotWorkspace",
    "JsonlRecordSequence",
    "MAX_PUBLISHED_DECK_MATCHUPS",
    "MAX_RANKING_SEED_LOCATIONS",
    "MAX_RESUMABLE_WORKSPACE_AGE_SECONDS",
    "MAX_SPECIAL_FIELD_PROBE_BATTLES",
    "OfficialAPIRequester",
    "PATH_OF_LEGEND_BATTLE_TYPE",
    "PATH_OF_LEGEND_COLLECTION_SCOPE",
    "PATH_OF_LEGEND_SCOPE_CONTRACT",
    "SUPERCELL_API_BASE_URL",
    "SUPERCELL_SOURCE_URL",
    "SupercellAPIClient",
    "_is_win",
    "_official_player_rank",
    "_ranking_position",
    "_team_cards",
    "_team_member",
    "build_card_deck_stats",
    "build_live_snapshot",
    "is_path_of_legend_battle",
    "normalize_battle_record",
    "opponent_tags_from_battles",
    "probe_official_special_fields",
    "select_usable_battles",
]
