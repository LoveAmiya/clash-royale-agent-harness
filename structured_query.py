"""Read-only query contracts for snapshot-scoped structured statistics."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from battle_loadout import canonical_loadout, full_loadout_signature
from deck_archetypes import archetype_family
from query_parser import CARD_ALIAS_OVERRIDES
from rolling_corpus import DATASET_SCOPES


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
        connection = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if self.dataset_scope is not None:
                for table in (
                    "card_stats",
                    "card_teammates",
                    "card_opponents",
                    "deck_stats",
                    "matchup_stats",
                    "full_loadout_stats",
                    "full_loadout_matchup_stats",
                    "tower_stats",
                    "evolution_stats",
                    "elite_stats",
                    "loadout_card_catalog",
                    "loadout_entity_stats",
                    "archetype_stats",
                    "archetype_matchups",
                    "archetype_decks",
                ):
                    columns = [
                        str(row[1])
                        for row in connection.execute(f'PRAGMA main.table_info("{table}")')
                        if str(row[1]) != "dataset_scope"
                    ]
                    if not columns:
                        continue
                    selected = ",".join(f'"{column}"' for column in columns)
                    scope_literal = self.dataset_scope.replace("'", "''")
                    connection.execute(
                        f'CREATE TEMP VIEW "{table}" AS '
                        f'SELECT {selected} FROM main."{table}" WHERE dataset_scope=\'{scope_literal}\''
                    )
            yield connection
        finally:
            connection.close()

    def _provenance(self) -> dict:
        if self.dataset is not None:
            counts = self.dataset["structured_counts"]
            return {
                "snapshot_group_id": self.snapshot_group_id,
                "snapshot_id": self.snapshot_id,
                "dataset_scope": self.dataset_scope,
                "window_started_at": self.dataset.get("window_started_at"),
                "window_ended_at": self.dataset.get("window_ended_at"),
                "unique_battles": self.dataset.get("unique_battles"),
                "weekly_batch_count": self.dataset.get("weekly_batch_count"),
                "daily_batch_count": self.dataset.get("daily_batch_count"),
                "ranked_coverage": self.dataset.get("ranked_coverage"),
                "missing_collection_dates": self.dataset.get("missing_collection_dates", []),
                "source": "Supercell API rolling Path of Legend corpus",
                "total_sample_battles": counts["source_battles"],
                "included_battles": counts["included_battles"],
                "excluded_incomplete_decks": counts["excluded_incomplete_decks"],
                "side_records": counts["side_records"],
                "full_loadout_battles": counts.get("full_loadout_battles", 0),
                "full_loadout_side_records": counts.get("full_loadout_side_records", 0),
                "excluded_incomplete_loadouts": counts.get("excluded_incomplete_loadouts", 0),
                "structured_index_fingerprint": self.manifest.get("structured_stats_fingerprint"),
                "deck_contract": "exactly_8_unique_cards_on_both_sides",
            }
        counts = self.manifest["counts"]
        return {
            "snapshot_id": self.snapshot_id,
            "fetched_at": self.manifest.get("fetched_at"),
            "source": self.manifest.get("source"),
            "total_sample_battles": counts["source_battles"],
            "included_battles": counts["included_battles"],
            "excluded_incomplete_decks": counts["excluded_incomplete_decks"],
            "side_records": counts["side_records"],
            "full_loadout_battles": counts.get("full_loadout_battles", 0),
            "full_loadout_side_records": counts.get("full_loadout_side_records", 0),
            "excluded_incomplete_loadouts": counts.get("excluded_incomplete_loadouts", 0),
            "structured_index_fingerprint": self.manifest.get("stats_sqlite_sha256"),
            "deck_contract": self.manifest.get("filters", {}).get("deck_contract"),
        }

    @staticmethod
    def _warning(sample_count: int) -> dict | None:
        if sample_count >= LOW_SAMPLE_THRESHOLD:
            return None
        return {
            "code": "LOW_SAMPLE_WARNING",
            "message": f"Only {sample_count} matched observations are available.",
            "threshold": LOW_SAMPLE_THRESHOLD,
            "matched_sample_count": sample_count,
        }

    def _catalog_names(self) -> set[str]:
        if self._card_names is None:
            with self._connect() as connection:
                self._card_names = {str(row[0]) for row in connection.execute("SELECT card_name FROM card_stats")}
        return self._card_names

    def _validate_card(self, card_id: str) -> str:
        value = str(card_id or "").strip()
        if value not in self._catalog_names():
            raise StructuredQueryError(
                "INVALID_CARD_ID",
                "card_id must exactly match a card from the structured catalog.",
                details={"card_id": value},
            )
        return value

    def _validate_deck(self, cards: list[str]) -> tuple[tuple[str, ...], str]:
        if not isinstance(cards, list) or len(cards) != 8:
            raise StructuredQueryError(
                "INVALID_DECK",
                "A structured deck must contain exactly 8 card IDs.",
                details={"card_count": len(cards) if isinstance(cards, list) else None},
            )
        normalized = [self._validate_card(card) for card in cards]
        if len(set(normalized)) != 8:
            raise StructuredQueryError(
                "INVALID_DECK",
                "A structured deck cannot contain duplicate card IDs.",
                details={"duplicate_card_ids": sorted({card for card in normalized if normalized.count(card) > 1})},
            )
        deck = tuple(sorted(normalized))
        return deck, json.dumps(deck, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _validate_loadout(loadout: dict) -> tuple[dict, str]:
        normalized = canonical_loadout(loadout)
        official_ids_valid = bool(
            normalized
            and re.fullmatch(r"\d+", str(normalized["tower"]["id"]))
            and all(re.fullmatch(r"\d+", str(card.get("id") or "")) for card in normalized["cards"])
        )
        signature = full_loadout_signature(normalized)
        special_modes_valid = bool(
            normalized
            and all(
                (int(card.get("evolution_level") or 0), card.get("elite"))
                in {(0, False), (1, False), (2, True)}
                for card in normalized.get("cards", [])
            )
        )
        if not normalized or not signature or not special_modes_valid or not official_ids_valid:
            raise StructuredQueryError(
                "INVALID_FULL_LOADOUT",
                "A full loadout requires one official tower ID, 8 official card IDs, and official special modes 0=ordinary, 1=evolution, or 2=elite.",
                details={"deck_mode": "full_loadout"},
            )
        return normalized, signature

    @staticmethod
    def _card_row(row: sqlite3.Row) -> dict:
        keys = (
            "card_name",
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
        return {key: row[key] for key in keys}

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
        try:
            with self._connect() as connection:
                return connection.execute("SELECT * FROM loadout_entity_stats").fetchall()
        except sqlite3.OperationalError as exc:
            raise StructuredQueryError(
                "ENTITY_STATS_NOT_READY",
                "The selected snapshot does not contain loadout entity statistics yet.",
                status_code=503,
                details={"dataset_scope": self.dataset_scope},
            ) from exc

    def entity_catalog(self) -> dict:
        entities = [self._entity_row(row) for row in self._entity_rows()]
        entities.sort(key=lambda item: (item["entity_type"], item["display_name_zh"], item["entity_id"]))
        return {
            "entity_mode": "loadout_entity",
            "entities": entities,
            "entity_count": len(entities),
            "provenance": {**self._provenance(), "entity_mode": "loadout_entity"},
        }

    def entity_rankings(self, sort_by: str = "usage_rate") -> dict:
        metric = str(sort_by or "usage_rate").strip()
        if metric not in CARD_RANKING_METRICS:
            raise StructuredQueryError(
                "INVALID_CARD_RANKING_METRIC",
                "sort_by must be usage_rate, clean_win_rate, or rating.",
                details={"sort_by": metric, "allowed": list(CARD_RANKING_METRICS)},
            )
        entities = [self._entity_row(row) for row in self._entity_rows()]
        entities.sort(
            key=lambda item: (
                -float(item[metric]),
                -int(item["appearances"]),
                str(item["display_name_zh"]),
                str(item["entity_id"]),
            )
        )
        previous_value = None
        current_rank = 0
        for position, entity in enumerate(entities, start=1):
            value = float(entity[metric])
            if previous_value is None or value != previous_value:
                current_rank = position
                previous_value = value
            entity["rank"] = current_rank
        return {
            "entity_mode": "loadout_entity",
            "sort_by": metric,
            "sort_order": "desc",
            "entity_count": len(entities),
            "entities": entities,
            "low_sample_threshold": LOW_SAMPLE_THRESHOLD,
            "metric_definitions": dict(CARD_RANKING_METRIC_DEFINITIONS),
            "provenance": {**self._provenance(), "entity_mode": "loadout_entity"},
        }

    def entity_stats(self, entity_id: str) -> dict:
        value = str(entity_id or "").strip()
        if not re.fullmatch(r"(?:tower:\d+|card:\d+:(?:ordinary|evolution|elite))", value):
            raise StructuredQueryError(
                "INVALID_ENTITY_ID",
                "entity_id must identify an observed official tower or card form.",
                details={"entity_id": value},
            )
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM loadout_entity_stats WHERE entity_id=?", (value,)
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise StructuredQueryError(
                "ENTITY_STATS_NOT_READY",
                "The selected snapshot does not contain loadout entity statistics yet.",
                status_code=503,
                details={"dataset_scope": self.dataset_scope},
            ) from exc
        if row is None:
            raise StructuredQueryError(
                "ENTITY_NOT_FOUND",
                "No evidence is available for this entity in the selected dataset scope.",
                status_code=404,
                details={"entity_id": value},
            )
        entity = self._entity_row(row)
        return {
            "entity": entity,
            "matched_sample_count": entity["appearances"],
            "warning": self._warning(entity["appearances"]),
            "provenance": {**self._provenance(), "entity_mode": "loadout_entity"},
        }

    def compare_entities(self, entity_ids: list[str]) -> dict:
        if not isinstance(entity_ids, list) or len(entity_ids) != 2 or len(set(entity_ids)) != 2:
            raise StructuredQueryError(
                "INVALID_ENTITY_COMPARISON",
                "Entity comparison requires exactly 2 distinct entity IDs.",
            )
        results = [self.entity_stats(entity_id) for entity_id in entity_ids]
        entities = [result["entity"] for result in results]
        metrics = ("usage_rate", "clean_win_rate", "net_win_rate", "rating", "appearances")
        return {
            "entity_mode": "loadout_entity",
            "entities": entities,
            "differences": {
                metric: round(float(entities[0][metric]) - float(entities[1][metric]), 6)
                for metric in metrics
            },
            "matched_sample_count": [entity["appearances"] for entity in entities],
            "warnings": [result["warning"] for result in results if result["warning"]],
            "provenance": {**self._provenance(), "entity_mode": "loadout_entity"},
        }

    def card_catalog(self) -> dict:
        cards = []
        for card_id in sorted(self._catalog_names()):
            names = CARD_ALIAS_OVERRIDES.get(card_id, [])
            display_name = names[0] if names else card_id
            cards.append(
                {
                    "card_id": card_id,
                    "display_name_zh": display_name,
                    "translation_status": "canonical" if names else "fallback_english",
                }
            )
        return {"cards": cards, "card_count": len(cards), "provenance": self._provenance()}

    def card_rankings(self, sort_by: str = "usage_rate") -> dict:
        metric = str(sort_by or "usage_rate").strip()
        if metric not in CARD_RANKING_METRICS:
            raise StructuredQueryError(
                "INVALID_CARD_RANKING_METRIC",
                "sort_by must be usage_rate, clean_win_rate, or rating.",
                details={"sort_by": metric, "allowed": list(CARD_RANKING_METRICS)},
            )
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM card_stats").fetchall()
        cards = []
        for row in rows:
            card = self._card_row(row)
            names = CARD_ALIAS_OVERRIDES.get(card["card_name"], [])
            cards.append(
                {
                    **card,
                    "display_name_zh": names[0] if names else card["card_name"],
                    "translation_status": "canonical" if names else "fallback_english",
                    "is_low_sample": int(card["appearances"]) < LOW_SAMPLE_THRESHOLD,
                }
            )
        cards.sort(
            key=lambda card: (
                -float(card[metric]),
                -int(card["appearances"]),
                str(card["display_name_zh"]),
                str(card["card_name"]),
            )
        )
        previous_value = None
        current_rank = 0
        for position, card in enumerate(cards, start=1):
            value = float(card[metric])
            if previous_value is None or value != previous_value:
                current_rank = position
                previous_value = value
            card["rank"] = current_rank
        return {
            "sort_by": metric,
            "sort_order": "desc",
            "card_count": len(cards),
            "cards": cards,
            "low_sample_threshold": LOW_SAMPLE_THRESHOLD,
            "metric_definitions": dict(CARD_RANKING_METRIC_DEFINITIONS),
            "provenance": self._provenance(),
        }

    def loadout_catalog(self) -> dict:
        try:
            with self._connect() as connection:
                towers = []
                for row in connection.execute(
                    "SELECT * FROM tower_stats ORDER BY appearances DESC, tower_id"
                ):
                    tower = json.loads(row["tower_json"])
                    towers.append(
                        {
                            "tower_id": row["tower_id"],
                            "name": tower.get("name") or row["tower_id"],
                            "display_name_zh": TOWER_DISPLAY_NAMES_ZH.get(
                                tower.get("name"), tower.get("name") or row["tower_id"]
                            ),
                            "appearances": row["appearances"],
                            "usage_rate": row["usage_rate"],
                            "clean_win_rate": row["clean_win_rate"],
                        }
                    )
                cards = [
                    {
                        "card_id": row["card_id"],
                        "name": row["card_name"],
                        "display_name_zh": (
                            CARD_ALIAS_OVERRIDES.get(row["card_name"], [row["card_name"]])[0]
                        ),
                        "appearances": row["appearances"],
                        "can_evolve": row["evolution_appearances"] > 0,
                        "can_be_elite": row["elite_appearances"] > 0,
                        "evolution_appearances": row["evolution_appearances"],
                        "elite_appearances": row["elite_appearances"],
                    }
                    for row in connection.execute(
                        "SELECT * FROM loadout_card_catalog ORDER BY card_name, card_id"
                    )
                ]
        except sqlite3.OperationalError as exc:
            raise StructuredQueryError(
                "FULL_LOADOUT_NOT_READY",
                "The selected snapshot does not contain full-loadout evidence yet.",
                status_code=503,
                details={"dataset_scope": self.dataset_scope},
            ) from exc
        provenance = {**self._provenance(), "deck_mode": "full_loadout"}
        return {
            "deck_mode": "full_loadout",
            "towers": towers,
            "cards": cards,
            "tower_count": len(towers),
            "card_count": len(cards),
            "provenance": provenance,
        }

    def answer_payload(self) -> dict:
        """Return rolling-scope facts in the legacy Skill input shape."""
        provenance = self._provenance()
        with self._connect() as connection:
            card_rows = connection.execute(
                "SELECT * FROM card_stats ORDER BY usage_rate DESC, card_name"
            ).fetchall()
            deck_rows = connection.execute(
                "SELECT * FROM deck_stats ORDER BY games DESC, deck_signature LIMIT 150"
            ).fetchall()
        cards = []
        for rank, row in enumerate(card_rows, start=1):
            card = self._card_row(row)
            cards.append(
                {
                    "rank": rank,
                    "card_name": card["card_name"],
                    "rating": card["rating"],
                    "usage_rate": card["usage_rate"],
                    "usage_delta": 0.0,
                    "win_rate": card["clean_win_rate"],
                    "win_delta": 0.0,
                    "clean_win_rate": card["clean_win_rate"],
                    "appearance_count": card["appearances"],
                    "source": provenance["source"],
                    "sample_battles": provenance["unique_battles"],
                    "snapshot_id": self.snapshot_id,
                    "snapshot_group_id": self.snapshot_group_id,
                    "dataset_scope": self.dataset_scope,
                }
            )
        decks = []
        for rank, row in enumerate(deck_rows, start=1):
            deck_cards = json.loads(row["deck_json"])
            decks.append(
                {
                    "rank": rank,
                    "player_name": "Rolling Path of Legend sample",
                    "clan_name": "Official Supercell API",
                    "deck_name": " / ".join(deck_cards),
                    "avg_elixir": None,
                    "battles": row["games"],
                    "cards": deck_cards,
                    "sample_win_rate": row["clean_win_rate"],
                    "source": provenance["source"],
                    "sample_battles": provenance["unique_battles"],
                    "snapshot_id": self.snapshot_id,
                    "snapshot_group_id": self.snapshot_group_id,
                    "dataset_scope": self.dataset_scope,
                }
            )
        return {
            "cards_meta": cards,
            "top_decks": decks,
            "card_deck_stats": {},
            "provenance": provenance,
        }

    def card_stats(self, card_id: str) -> dict:
        card_id = self._validate_card(card_id)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM card_stats WHERE card_name = ?", (card_id,)).fetchone()
            teammates = [
                dict(item)
                for item in connection.execute(
                    "SELECT teammate_name AS card_id, games, wins, losses, draws "
                    "FROM card_teammates WHERE card_name = ? ORDER BY games DESC, teammate_name LIMIT 10",
                    (card_id,),
                )
            ]
            opponents = [
                dict(item)
                for item in connection.execute(
                    "SELECT opponent_name AS card_id, games, wins, losses, draws "
                    "FROM card_opponents WHERE card_name = ? ORDER BY games DESC, opponent_name LIMIT 10",
                    (card_id,),
                )
            ]
        if row is None:
            raise StructuredQueryError("NO_CARD_EVIDENCE", "No evidence is available for this card.", status_code=404)
        card = self._card_row(row)
        return {
            "card": card,
            "common_teammates": teammates,
            "common_opponents": opponents,
            "matched_sample_count": card["appearances"],
            "warning": self._warning(card["appearances"]),
            "provenance": self._provenance(),
        }

    def compare_cards(self, card_ids: list[str]) -> dict:
        if not isinstance(card_ids, list) or len(card_ids) != 2 or len(set(card_ids)) != 2:
            raise StructuredQueryError(
                "INVALID_CARD_COMPARISON",
                "Card comparison requires exactly 2 distinct card IDs.",
            )
        results = [self.card_stats(card_id) for card_id in card_ids]
        cards = [result["card"] for result in results]
        metrics = ("usage_rate", "clean_win_rate", "net_win_rate", "rating", "appearances")
        return {
            "cards": cards,
            "differences": {metric: round(float(cards[0][metric]) - float(cards[1][metric]), 6) for metric in metrics},
            "matched_sample_count": [card["appearances"] for card in cards],
            "warnings": [result["warning"] for result in results if result["warning"]],
            "provenance": self._provenance(),
        }

    def deck_profile(self, cards: list[str]) -> dict:
        deck, signature = self._validate_deck(cards)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM deck_stats WHERE deck_signature = ?", (signature,)).fetchone()
            matchup_rows = connection.execute(
                """
                SELECT *,
                    CASE WHEN deck_a_signature = ? THEN wins_a ELSE wins_b END AS perspective_wins,
                    CASE WHEN deck_a_signature = ? THEN wins_b ELSE wins_a END AS perspective_losses
                FROM matchup_stats
                WHERE deck_a_signature = ? OR deck_b_signature = ?
                ORDER BY
                    CASE
                        WHEN perspective_wins + perspective_losses > 0
                        THEN 1.0 * perspective_wins / (perspective_wins + perspective_losses)
                        ELSE 0.0
                    END DESC,
                    games DESC,
                    deck_a_signature,
                    deck_b_signature
                LIMIT 10
                """,
                (signature, signature, signature, signature),
            ).fetchall()
        if row is None:
            raise StructuredQueryError(
                "NO_DECK_EVIDENCE",
                "No exact evidence is available for this 8-card deck.",
                status_code=404,
                details={"cards": list(deck)},
            )
        common_opponents = []
        for matchup in matchup_rows:
            is_a = matchup["deck_a_signature"] == signature
            opponent_signature = matchup["deck_b_signature"] if is_a else matchup["deck_a_signature"]
            wins = matchup["wins_a"] if is_a else matchup["wins_b"]
            losses = matchup["wins_b"] if is_a else matchup["wins_a"]
            decisions = wins + losses
            common_opponents.append(
                {
                    "cards": json.loads(opponent_signature),
                    "games": matchup["games"],
                    "wins": wins,
                    "losses": losses,
                    "draws": matchup["draws"],
                    "clean_win_rate": round(wins / decisions * 100, 6) if decisions else 0.0,
                }
            )
        profile = dict(row)
        profile["cards"] = json.loads(profile.pop("deck_json"))
        profile["archetype_family"] = archetype_family(profile["archetype"])
        return {
            "deck": profile,
            "common_opponents": common_opponents,
            "matched_sample_count": profile["games"],
            "warning": self._warning(profile["games"]),
            "provenance": self._provenance(),
        }

    def deck_matchup(self, deck_a_cards: list[str], deck_b_cards: list[str]) -> dict:
        deck_a, signature_a = self._validate_deck(deck_a_cards)
        deck_b, signature_b = self._validate_deck(deck_b_cards)
        stored_a, stored_b = sorted((signature_a, signature_b))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM matchup_stats WHERE deck_a_signature = ? AND deck_b_signature = ?",
                (stored_a, stored_b),
            ).fetchone()
        if row is None:
            raise StructuredQueryError(
                "NO_MATCHUP_EVIDENCE",
                "No exact battles were found between these two 8-card decks.",
                status_code=404,
                details={"deck_a": list(deck_a), "deck_b": list(deck_b), "matched_sample_count": 0},
            )
        requested_a_is_stored_a = signature_a == row["deck_a_signature"]
        wins_a = row["wins_a"] if requested_a_is_stored_a else row["wins_b"]
        wins_b = row["wins_b"] if requested_a_is_stored_a else row["wins_a"]
        crowns_a = row["crowns_a"] if requested_a_is_stored_a else row["crowns_b"]
        crowns_b = row["crowns_b"] if requested_a_is_stored_a else row["crowns_a"]
        decisions = wins_a + wins_b
        rate_a = round(wins_a / decisions * 100, 6) if decisions else 0.0
        games = row["games"]
        return {
            "deck_a": {
                "cards": list(deck_a),
                "wins": wins_a,
                "clean_win_rate": rate_a,
                "average_crowns": round(crowns_a / games, 6),
            },
            "deck_b": {
                "cards": list(deck_b),
                "wins": wins_b,
                "clean_win_rate": round(100 - rate_a, 6) if decisions else 0.0,
                "average_crowns": round(crowns_b / games, 6),
            },
            "games": games,
            "draws": row["draws"],
            "latest_battle_time": row["latest_battle_time"],
            "matched_sample_count": games,
            "warning": self._warning(games),
            "provenance": self._provenance(),
        }

    def full_loadout_profile(self, loadout: dict) -> dict:
        _, signature = self._validate_loadout(loadout)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM full_loadout_stats WHERE loadout_signature=?",
                    (signature,),
                ).fetchone()
                matchup_rows = connection.execute(
                    """
                    SELECT *,
                        CASE WHEN loadout_a_signature=? THEN wins_a ELSE wins_b END AS perspective_wins,
                        CASE WHEN loadout_a_signature=? THEN wins_b ELSE wins_a END AS perspective_losses
                    FROM full_loadout_matchup_stats
                    WHERE loadout_a_signature=? OR loadout_b_signature=?
                    ORDER BY
                        CASE
                            WHEN perspective_wins+perspective_losses>0
                            THEN 1.0*perspective_wins/(perspective_wins+perspective_losses)
                            ELSE 0.0
                        END DESC,
                        games DESC,
                        loadout_a_signature,
                        loadout_b_signature
                    LIMIT 10
                    """,
                    (signature, signature, signature, signature),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise StructuredQueryError(
                "FULL_LOADOUT_NOT_READY",
                "The selected snapshot does not contain full-loadout evidence yet.",
                status_code=503,
                details={"dataset_scope": self.dataset_scope},
            ) from exc
        if row is None:
            raise StructuredQueryError(
                "NO_FULL_LOADOUT_EVIDENCE",
                "No exact evidence is available for this tower, evolution, and elite configuration.",
                status_code=404,
                details={"matched_sample_count": 0, "deck_mode": "full_loadout"},
            )
        profile = dict(row)
        profile["loadout"] = json.loads(profile.pop("loadout_json"))
        opponents = []
        for matchup in matchup_rows:
            is_a = matchup["loadout_a_signature"] == signature
            opponent_signature = (
                matchup["loadout_b_signature"] if is_a else matchup["loadout_a_signature"]
            )
            wins = matchup["wins_a"] if is_a else matchup["wins_b"]
            losses = matchup["wins_b"] if is_a else matchup["wins_a"]
            decisions = wins + losses
            with self._connect() as connection:
                opponent_row = connection.execute(
                    "SELECT loadout_json FROM full_loadout_stats WHERE loadout_signature=?",
                    (opponent_signature,),
                ).fetchone()
            opponents.append(
                {
                    "loadout": json.loads(opponent_row[0]) if opponent_row else None,
                    "games": matchup["games"],
                    "wins": wins,
                    "losses": losses,
                    "draws": matchup["draws"],
                    "clean_win_rate": round(wins * 100 / decisions, 6) if decisions else 0.0,
                }
            )
        provenance = {**self._provenance(), "deck_mode": "full_loadout"}
        return {
            "deck_mode": "full_loadout",
            "loadout": profile,
            "common_opponents": opponents,
            "matched_sample_count": profile["games"],
            "warning": self._warning(profile["games"]),
            "provenance": provenance,
        }

    def full_loadout_matchup(self, loadout_a: dict, loadout_b: dict) -> dict:
        normalized_a, signature_a = self._validate_loadout(loadout_a)
        normalized_b, signature_b = self._validate_loadout(loadout_b)
        stored_a, stored_b = sorted((signature_a, signature_b))
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM full_loadout_matchup_stats WHERE loadout_a_signature=? AND loadout_b_signature=?",
                    (stored_a, stored_b),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise StructuredQueryError(
                "FULL_LOADOUT_NOT_READY",
                "The selected snapshot does not contain full-loadout evidence yet.",
                status_code=503,
                details={"dataset_scope": self.dataset_scope},
            ) from exc
        if row is None:
            raise StructuredQueryError(
                "NO_FULL_LOADOUT_MATCHUP_EVIDENCE",
                "No exact battles were found between these two complete configurations.",
                status_code=404,
                details={"matched_sample_count": 0, "deck_mode": "full_loadout"},
            )
        requested_a_is_stored_a = signature_a == row["loadout_a_signature"]
        wins_a = row["wins_a"] if requested_a_is_stored_a else row["wins_b"]
        wins_b = row["wins_b"] if requested_a_is_stored_a else row["wins_a"]
        crowns_a = row["crowns_a"] if requested_a_is_stored_a else row["crowns_b"]
        crowns_b = row["crowns_b"] if requested_a_is_stored_a else row["crowns_a"]
        decisions = wins_a + wins_b
        games = row["games"]
        rate_a = round(wins_a * 100 / decisions, 6) if decisions else 0.0
        provenance = {**self._provenance(), "deck_mode": "full_loadout"}
        return {
            "deck_mode": "full_loadout",
            "loadout_a": {
                "loadout": normalized_a,
                "wins": wins_a,
                "clean_win_rate": rate_a,
                "average_crowns": round(crowns_a / games, 6),
            },
            "loadout_b": {
                "loadout": normalized_b,
                "wins": wins_b,
                "clean_win_rate": round(100 - rate_a, 6) if decisions else 0.0,
                "average_crowns": round(crowns_b / games, 6),
            },
            "games": games,
            "draws": row["draws"],
            "latest_battle_time": row["latest_battle_time"],
            "matched_sample_count": games,
            "warning": self._warning(games),
            "provenance": provenance,
        }

    def archetypes(self) -> dict:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM archetype_stats ORDER BY games DESC, archetype").fetchall()
            values = []
            for row in rows:
                item = dict(row)
                item["family"] = archetype_family(item["archetype"])
                representatives = connection.execute(
                    """
                    SELECT decks.deck_json, archetype_decks.games, archetype_decks.wins,
                           archetype_decks.losses, archetype_decks.draws
                    FROM archetype_decks
                    JOIN deck_stats AS decks USING(deck_signature)
                    WHERE archetype_decks.archetype = ?
                    ORDER BY archetype_decks.games DESC, archetype_decks.deck_signature LIMIT 3
                    """,
                    (row["archetype"],),
                ).fetchall()
                item["representative_decks"] = [
                    {"cards": json.loads(deck["deck_json"]), "games": deck["games"]}
                    for deck in representatives
                ]
                values.append(item)
        return {
            "archetypes": values,
            "matched_sample_count": (
                self.dataset["structured_counts"]["side_records"]
                if self.dataset is not None
                else self.manifest["counts"]["side_records"]
            ),
            "provenance": self._provenance(),
        }
