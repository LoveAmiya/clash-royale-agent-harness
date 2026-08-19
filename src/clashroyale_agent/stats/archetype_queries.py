"""Archetype and representative-deck projections."""

from __future__ import annotations

import json


def archetypes(repository, *, archetype_family) -> dict:
    with repository._connect() as connection:
        rows = connection.execute("SELECT * FROM archetype_stats ORDER BY games DESC, archetype").fetchall()
        values = []
        for row in rows:
            item = dict(row)
            item["family"] = archetype_family(item["archetype"])
            representatives = connection.execute(
                """SELECT decks.deck_json, archetype_decks.games, archetype_decks.wins,
                archetype_decks.losses, archetype_decks.draws FROM archetype_decks
                JOIN deck_stats AS decks USING(deck_signature)
                WHERE archetype_decks.archetype = ?
                ORDER BY archetype_decks.games DESC, archetype_decks.deck_signature LIMIT 3""",
                (row["archetype"],),
            ).fetchall()
            item["representative_decks"] = [{"cards": json.loads(deck["deck_json"]), "games": deck["games"]} for deck in representatives]
            values.append(item)
    return {"archetypes": values, "matched_sample_count": repository.dataset["structured_counts"]["side_records"] if repository.dataset is not None else repository.manifest["counts"]["side_records"], "provenance": repository._provenance()}
