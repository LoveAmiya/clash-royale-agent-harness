"""Loadout entity query projections for structured statistics."""

from __future__ import annotations

import re
import sqlite3


def entity_rows(repository, *, error_type: type[ValueError]) -> list[sqlite3.Row]:
    try:
        with repository._connect() as connection:
            return connection.execute("SELECT * FROM loadout_entity_stats").fetchall()
    except sqlite3.OperationalError as exc:
        raise error_type("ENTITY_STATS_NOT_READY", "The selected snapshot does not contain loadout entity statistics yet.", status_code=503, details={"dataset_scope": repository.dataset_scope}) from exc


def entity_catalog(repository) -> dict:
    entities = [repository._entity_row(row) for row in repository._entity_rows()]
    entities.sort(key=lambda item: (item["entity_type"], item["display_name_zh"], item["entity_id"]))
    return {"entity_mode": "loadout_entity", "entities": entities, "entity_count": len(entities), "provenance": {**repository._provenance(), "entity_mode": "loadout_entity"}}


def entity_rankings(repository, sort_by: str, *, metrics: tuple[str, ...], metric_definitions: dict, low_sample_threshold: int, error_type: type[ValueError]) -> dict:
    metric = str(sort_by or "usage_rate").strip()
    if metric not in metrics:
        raise error_type("INVALID_CARD_RANKING_METRIC", "sort_by must be usage_rate, clean_win_rate, or rating.", details={"sort_by": metric, "allowed": list(metrics)})
    entities = [repository._entity_row(row) for row in repository._entity_rows()]
    entities.sort(key=lambda item: (-float(item[metric]), -int(item["appearances"]), str(item["display_name_zh"]), str(item["entity_id"])))
    previous_value = None
    current_rank = 0
    for position, entity in enumerate(entities, start=1):
        value = float(entity[metric])
        if previous_value is None or value != previous_value:
            current_rank = position
            previous_value = value
        entity["rank"] = current_rank
    return {"entity_mode": "loadout_entity", "sort_by": metric, "sort_order": "desc", "entity_count": len(entities), "entities": entities, "low_sample_threshold": low_sample_threshold, "metric_definitions": dict(metric_definitions), "provenance": {**repository._provenance(), "entity_mode": "loadout_entity"}}


def entity_stats(repository, entity_id: str, *, error_type: type[ValueError]) -> dict:
    value = str(entity_id or "").strip()
    if not re.fullmatch(r"(?:tower:\d+|card:\d+:(?:ordinary|evolution|elite))", value):
        raise error_type("INVALID_ENTITY_ID", "entity_id must identify an observed official tower or card form.", details={"entity_id": value})
    try:
        with repository._connect() as connection:
            row = connection.execute("SELECT * FROM loadout_entity_stats WHERE entity_id=?", (value,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise error_type("ENTITY_STATS_NOT_READY", "The selected snapshot does not contain loadout entity statistics yet.", status_code=503, details={"dataset_scope": repository.dataset_scope}) from exc
    if row is None:
        raise error_type("ENTITY_NOT_FOUND", "No evidence is available for this entity in the selected dataset scope.", status_code=404, details={"entity_id": value})
    entity = repository._entity_row(row)
    return {"entity": entity, "matched_sample_count": entity["appearances"], "warning": repository._warning(entity["appearances"]), "provenance": {**repository._provenance(), "entity_mode": "loadout_entity"}}


def entity_stats_by_reference(repository, entity_type: str | None, entity_name: str | None, special_state: str | None, *, error_type: type[ValueError]) -> dict:
    name = str(entity_name or "").strip()
    state = str(special_state or "").strip()
    if entity_type == "tower" and state == "tower":
        for tower in repository.loadout_catalog()["towers"]:
            if name in {str(tower.get("name")), str(tower.get("display_name_zh"))}:
                return repository.entity_stats(f"tower:{tower['tower_id']}")
    elif entity_type == "card" and state in {"ordinary", "evolution", "elite"}:
        for card in repository.loadout_catalog()["cards"]:
            if name in {str(card.get("name")), str(card.get("display_name_zh"))}:
                return repository.entity_stats(f"card:{card['card_id']}:{state}")
    raise error_type("ENTITY_NOT_FOUND", "No evidence is available for this entity in the selected dataset scope.", status_code=404, details={"entity_type": entity_type, "entity_name": name, "special_state": state})


def compare_entities(repository, entity_ids: list[str], *, error_type: type[ValueError]) -> dict:
    if not isinstance(entity_ids, list) or len(entity_ids) != 2 or len(set(entity_ids)) != 2:
        raise error_type("INVALID_ENTITY_COMPARISON", "Entity comparison requires exactly 2 distinct entity IDs.")
    results = [repository.entity_stats(entity_id) for entity_id in entity_ids]
    entities = [result["entity"] for result in results]
    metrics = ("usage_rate", "clean_win_rate", "net_win_rate", "rating", "appearances")
    return {"entity_mode": "loadout_entity", "entities": entities, "differences": {metric: round(float(entities[0][metric]) - float(entities[1][metric]), 6) for metric in metrics}, "matched_sample_count": [entity["appearances"] for entity in entities], "warnings": [result["warning"] for result in results if result["warning"]], "provenance": {**repository._provenance(), "entity_mode": "loadout_entity"}}
