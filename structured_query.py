"""Read-only query contracts for snapshot-scoped structured statistics."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import app_config as _app_config  # noqa: F401 - bootstrap src package imports

from battle_loadout import canonical_loadout, full_loadout_signature
from deck_archetypes import archetype_family
from query_parser import CARD_ALIAS_OVERRIDES
from rolling_corpus import DATASET_SCOPES
try:
    from clashroyale_agent.stats.query_context import provenance as provenance_orchestrated, readonly_connection
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.query_context import provenance as provenance_orchestrated, readonly_connection
try:
    from clashroyale_agent.stats.card_queries import card_catalog as card_catalog_orchestrated, card_rankings as card_rankings_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.card_queries import card_catalog as card_catalog_orchestrated, card_rankings as card_rankings_orchestrated
try:
    from clashroyale_agent.stats.card_detail_queries import (
        card_pair_stats as card_pair_stats_orchestrated,
        card_stats as card_stats_orchestrated,
        card_teammate_rankings as card_teammate_rankings_orchestrated,
        compare_cards as compare_cards_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.card_detail_queries import (
        card_pair_stats as card_pair_stats_orchestrated,
        card_stats as card_stats_orchestrated,
        card_teammate_rankings as card_teammate_rankings_orchestrated,
        compare_cards as compare_cards_orchestrated,
    )
try:
    from clashroyale_agent.stats.entity_queries import (
        compare_entities as compare_entities_orchestrated,
        entity_catalog as entity_catalog_orchestrated,
        entity_rankings as entity_rankings_orchestrated,
        entity_rows as entity_rows_orchestrated,
        entity_stats as entity_stats_orchestrated,
        entity_stats_by_reference as entity_stats_by_reference_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.entity_queries import (
        compare_entities as compare_entities_orchestrated,
        entity_catalog as entity_catalog_orchestrated,
        entity_rankings as entity_rankings_orchestrated,
        entity_rows as entity_rows_orchestrated,
        entity_stats as entity_stats_orchestrated,
        entity_stats_by_reference as entity_stats_by_reference_orchestrated,
    )
try:
    from clashroyale_agent.stats.loadout_catalog import loadout_catalog as loadout_catalog_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.loadout_catalog import loadout_catalog as loadout_catalog_orchestrated
try:
    from clashroyale_agent.stats.answer_payload import build_answer_payload as build_answer_payload_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.answer_payload import build_answer_payload as build_answer_payload_orchestrated
try:
    from clashroyale_agent.stats.deck_queries import deck_matchup as deck_matchup_orchestrated, deck_profile as deck_profile_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.deck_queries import deck_matchup as deck_matchup_orchestrated, deck_profile as deck_profile_orchestrated
try:
    from clashroyale_agent.stats.full_loadout_queries import full_loadout_matchup as full_loadout_matchup_orchestrated, full_loadout_profile as full_loadout_profile_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.full_loadout_queries import full_loadout_matchup as full_loadout_matchup_orchestrated, full_loadout_profile as full_loadout_profile_orchestrated
try:
    from clashroyale_agent.stats.archetype_queries import archetypes as archetypes_orchestrated
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.archetype_queries import archetypes as archetypes_orchestrated
try:
    from clashroyale_agent.stats.query_contracts import (
        card_row as card_row_orchestrated,
        display_loadout as display_loadout_orchestrated,
        entity_display_name as entity_display_name_orchestrated,
        entity_row as entity_row_orchestrated,
        validate_card as validate_card_orchestrated,
        validate_deck as validate_deck_orchestrated,
        validate_loadout as validate_loadout_orchestrated,
        warning as warning_orchestrated,
    )
except ModuleNotFoundError:
    from src.clashroyale_agent.stats.query_contracts import (
        card_row as card_row_orchestrated,
        display_loadout as display_loadout_orchestrated,
        entity_display_name as entity_display_name_orchestrated,
        entity_row as entity_row_orchestrated,
        validate_card as validate_card_orchestrated,
        validate_deck as validate_deck_orchestrated,
        validate_loadout as validate_loadout_orchestrated,
        warning as warning_orchestrated,
    )


LOW_SAMPLE_THRESHOLD = 20
CARD_RANKING_METRICS = ("usage_rate", "clean_win_rate", "rating")
CARD_RANKING_METRIC_DEFINITIONS = {
    "usage_rate": "appearances / included side records",
    "clean_win_rate": "wins / (wins + losses); draws excluded",
    "rating_formula": "65% Wilson lower bound + 20% usage percentile + 15% sample confidence",
}
TOWER_DISPLAY_NAMES_ZH = {
    "Tower Princess": "公主塔",
    "Princess Tower": "公主塔",
    "Dagger Duchess": "飞刀塔",
    "Royal Chef": "厨师塔",
    "Cannoneer": "炮塔",
}


class StructuredQueryError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def response(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class StructuredStatsRepository:
    """A local SQLite repository; no parser, model, retriever, or network calls."""

    def __init__(self, data_dir: Path, snapshot_id: str):
        self.data_dir = Path(data_dir)
        self.snapshot_id = str(snapshot_id or "").strip()
        self.snapshot_group_id = None
        self.dataset_scope = None
        self.dataset = None
        self.index_dir = self.data_dir / "structured_stats" / self.snapshot_id
        self.database_path = self.index_dir / "stats.sqlite"
        self.manifest_path = self.index_dir / "manifest.json"
        try:
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StructuredQueryError(
                "STRUCTURED_INDEX_UNAVAILABLE",
                "Structured statistics are not ready for this snapshot.",
                status_code=503,
                details={"snapshot_id": self.snapshot_id},
            ) from exc
        if (
            not self.database_path.is_file()
            or not isinstance(self.manifest, dict)
            or self.manifest.get("snapshot_id") != self.snapshot_id
        ):
            raise StructuredQueryError(
                "STRUCTURED_INDEX_UNAVAILABLE",
                "Structured statistics are not aligned with the active snapshot.",
                status_code=503,
                details={"snapshot_id": self.snapshot_id},
            )
        self._card_names: set[str] | None = None

    @classmethod
    def for_snapshot_group(
        cls,
        data_dir: Path,
        snapshot_group_id: str,
        dataset_scope: str,
    ) -> "StructuredStatsRepository":
        if dataset_scope not in DATASET_SCOPES:
            raise StructuredQueryError(
                "INVALID_DATASET_SCOPE",
                "dataset_scope must be one of the published rolling dataset scopes.",
                details={"dataset_scope": dataset_scope, "allowed": list(DATASET_SCOPES)},
            )
        repository = cls.__new__(cls)
        repository.data_dir = Path(data_dir)
        repository.snapshot_group_id = str(snapshot_group_id or "").strip()
        repository.dataset_scope = dataset_scope
        repository.index_dir = repository.data_dir / "snapshot_groups" / repository.snapshot_group_id
        repository.database_path = repository.index_dir / "structured_stats.sqlite"
        repository.manifest_path = repository.index_dir / "manifest.json"
        try:
            repository.manifest = json.loads(repository.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The requested rolling snapshot group is not ready.",
                status_code=503,
                details={"snapshot_group_id": repository.snapshot_group_id, "dataset_scope": dataset_scope},
            ) from exc
        datasets = repository.manifest.get("datasets") if isinstance(repository.manifest, dict) else None
        dataset = datasets.get(dataset_scope) if isinstance(datasets, dict) else None
        dataset_ready = bool(
            isinstance(dataset, dict)
            and (
                dataset.get("ready") is True
                or ("ready" not in dataset and int(dataset.get("unique_battles") or 0) > 0)
            )
        )
        if (
            not repository.database_path.is_file()
            or repository.manifest.get("snapshot_group_id") != repository.snapshot_group_id
            or not isinstance(dataset, dict)
            or not dataset.get("snapshot_id")
            or not dataset_ready
        ):
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The requested rolling dataset scope is not ready.",
                status_code=503,
                details={"snapshot_group_id": repository.snapshot_group_id, "dataset_scope": dataset_scope},
            )
        repository.dataset = dataset
        repository.snapshot_id = str(dataset["snapshot_id"])
        repository._card_names = None
        return repository

    @contextmanager
    def _connect(self):
        with readonly_connection(self.database_path, self.dataset_scope) as connection:
            yield connection

    def _provenance(self) -> dict:
        return provenance_orchestrated(dataset=self.dataset, manifest=self.manifest, snapshot_group_id=self.snapshot_group_id, snapshot_id=self.snapshot_id, dataset_scope=self.dataset_scope)

    @staticmethod
    def _warning(sample_count: int) -> dict | None:
        return warning_orchestrated(sample_count, threshold=LOW_SAMPLE_THRESHOLD)

    def _catalog_names(self) -> set[str]:
        if self._card_names is None:
            with self._connect() as connection:
                self._card_names = {str(row[0]) for row in connection.execute("SELECT card_name FROM card_stats")}
        return self._card_names

    def _validate_card(self, card_id: str) -> str:
        return validate_card_orchestrated(card_id, self._catalog_names(), StructuredQueryError)

    def _validate_deck(self, cards: list[str]) -> tuple[tuple[str, ...], str]:
        return validate_deck_orchestrated(cards, self._catalog_names(), StructuredQueryError)

    @staticmethod
    def _validate_loadout(loadout: dict) -> tuple[dict, str]:
        return validate_loadout_orchestrated(
            loadout,
            canonical_loadout=canonical_loadout,
            full_loadout_signature=full_loadout_signature,
            error_type=StructuredQueryError,
        )
    @staticmethod
    def _display_loadout(loadout: dict | None) -> dict | None:
        return display_loadout_orchestrated(
            loadout,
            tower_display_names=TOWER_DISPLAY_NAMES_ZH,
            card_aliases=CARD_ALIAS_OVERRIDES,
        )

    @staticmethod
    def _card_row(row: sqlite3.Row) -> dict:
        return card_row_orchestrated(row)

    @staticmethod
    def _entity_display_name(row: sqlite3.Row) -> str:
        if row["entity_type"] == "tower":
            payload = json.loads(row["entity_json"])
            name = str(payload.get("name") or row["tower_id"])
            return TOWER_DISPLAY_NAMES_ZH.get(name, name)
        card_name = str(row["card_name"] or row["card_id"])
        base_name = CARD_ALIAS_OVERRIDES.get(card_name, [card_name])[0]
        if row["special_state"] == "evolution":
            return f"觉醒{base_name}"
        if row["special_state"] == "elite":
            return f"精英{base_name}"
        return base_name

    @classmethod
    def _entity_row(cls, row: sqlite3.Row) -> dict:
        keys = (
            "entity_id",
            "entity_type",
            "card_id",
            "card_name",
            "tower_id",
            "special_state",
            "appearances",
            "wins",
            "losses",
            "draws",
            "usage_rate",
            "clean_win_rate",
            "net_win_rate",
            "wilson_lower_bound",
            "usage_percentile",
            "sample_confidence",
            "rating",
        )
        return {
            **{key: row[key] for key in keys},
            "display_name_zh": cls._entity_display_name(row),
            "is_low_sample": int(row["appearances"]) < LOW_SAMPLE_THRESHOLD,
        }

    def _entity_rows(self) -> list[sqlite3.Row]:
        return entity_rows_orchestrated(self, error_type=StructuredQueryError)

    def entity_catalog(self) -> dict:
        return entity_catalog_orchestrated(self)

    def entity_rankings(self, sort_by: str = "usage_rate") -> dict:
        return entity_rankings_orchestrated(
            self,
            sort_by,
            metrics=CARD_RANKING_METRICS,
            metric_definitions=CARD_RANKING_METRIC_DEFINITIONS,
            low_sample_threshold=LOW_SAMPLE_THRESHOLD,
            error_type=StructuredQueryError,
        )

    def entity_stats(self, entity_id: str) -> dict:
        return entity_stats_orchestrated(self, entity_id, error_type=StructuredQueryError)

    def entity_stats_by_reference(
        self,
        entity_type: str | None,
        entity_name: str | None,
        special_state: str | None,
    ) -> dict:
        return entity_stats_by_reference_orchestrated(
            self,
            entity_type,
            entity_name,
            special_state,
            error_type=StructuredQueryError,
        )

    def compare_entities(self, entity_ids: list[str]) -> dict:
        return compare_entities_orchestrated(self, entity_ids, error_type=StructuredQueryError)

    def card_catalog(self) -> dict:
        return card_catalog_orchestrated(self, aliases=CARD_ALIAS_OVERRIDES)

    def card_rankings(self, sort_by: str = "usage_rate") -> dict:
        return card_rankings_orchestrated(self, sort_by, metrics=CARD_RANKING_METRICS, metric_definitions=CARD_RANKING_METRIC_DEFINITIONS, aliases=CARD_ALIAS_OVERRIDES, low_sample_threshold=LOW_SAMPLE_THRESHOLD, error_type=StructuredQueryError)

    def loadout_catalog(self) -> dict:
        return loadout_catalog_orchestrated(self, card_aliases=CARD_ALIAS_OVERRIDES, tower_names=TOWER_DISPLAY_NAMES_ZH, error_type=StructuredQueryError)

    def answer_payload(self) -> dict:
        """Return rolling-scope facts in the legacy Skill input shape."""
        return build_answer_payload_orchestrated(self)

    def card_stats(self, card_id: str) -> dict:
        return card_stats_orchestrated(self, card_id, error_type=StructuredQueryError)

    def compare_cards(self, card_ids: list[str]) -> dict:
        return compare_cards_orchestrated(self, card_ids, error_type=StructuredQueryError)

    def card_pair_stats(self, card_ids: list[str]) -> dict:
        return card_pair_stats_orchestrated(self, card_ids, error_type=StructuredQueryError)

    def card_teammate_rankings(self, card_id: str, top_n: int = 10) -> dict:
        return card_teammate_rankings_orchestrated(
            self,
            card_id,
            top_n=top_n,
            aliases=CARD_ALIAS_OVERRIDES,
            error_type=StructuredQueryError,
        )

    def deck_profile(self, cards: list[str]) -> dict:
        return deck_profile_orchestrated(self, cards, archetype_family=archetype_family, error_type=StructuredQueryError)

    def deck_matchup(self, deck_a_cards: list[str], deck_b_cards: list[str]) -> dict:
        return deck_matchup_orchestrated(self, deck_a_cards, deck_b_cards, error_type=StructuredQueryError)

    def full_loadout_profile(self, loadout: dict) -> dict:
        return full_loadout_profile_orchestrated(self, loadout, error_type=StructuredQueryError)

    def full_loadout_matchup(self, loadout_a: dict, loadout_b: dict) -> dict:
        return full_loadout_matchup_orchestrated(self, loadout_a, loadout_b, error_type=StructuredQueryError)

    def archetypes(self) -> dict:
        return archetypes_orchestrated(self, archetype_family=archetype_family)
