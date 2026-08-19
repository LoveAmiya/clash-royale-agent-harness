"""Full-loadout tower and card catalog projection."""

from __future__ import annotations

import json
import sqlite3


def loadout_catalog(repository, *, card_aliases: dict[str, list[str]], tower_names: dict[str, str], error_type: type[ValueError]) -> dict:
    try:
        with repository._connect() as connection:
            towers = []
            for row in connection.execute("SELECT * FROM tower_stats ORDER BY appearances DESC, tower_id"):
                tower = json.loads(row["tower_json"])
                towers.append({"tower_id": row["tower_id"], "name": tower.get("name") or row["tower_id"], "display_name_zh": tower_names.get(tower.get("name"), tower.get("name") or row["tower_id"]), "appearances": row["appearances"], "usage_rate": row["usage_rate"], "clean_win_rate": row["clean_win_rate"]})
            cards = [{"card_id": row["card_id"], "name": row["card_name"], "display_name_zh": card_aliases.get(row["card_name"], [row["card_name"]])[0], "appearances": row["appearances"], "can_evolve": row["evolution_appearances"] > 0, "can_be_elite": row["elite_appearances"] > 0, "evolution_appearances": row["evolution_appearances"], "elite_appearances": row["elite_appearances"]} for row in connection.execute("SELECT * FROM loadout_card_catalog ORDER BY card_name, card_id")]
    except sqlite3.OperationalError as exc:
        raise error_type("FULL_LOADOUT_NOT_READY", "The selected snapshot does not contain full-loadout evidence yet.", status_code=503, details={"dataset_scope": repository.dataset_scope}) from exc
    return {"deck_mode": "full_loadout", "towers": towers, "cards": cards, "tower_count": len(towers), "card_count": len(cards), "provenance": {**repository._provenance(), "deck_mode": "full_loadout"}}
