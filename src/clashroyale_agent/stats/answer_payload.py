"""Legacy QA answer payload projection from structured statistics."""

from __future__ import annotations

import json


def build_answer_payload(repository) -> dict:
    provenance = repository._provenance()
    sample_battles = provenance.get("unique_battles", provenance.get("total_sample_battles", 0))
    with repository._connect() as connection:
        card_rows = connection.execute("SELECT * FROM card_stats ORDER BY usage_rate DESC, card_name").fetchall()
        deck_rows = connection.execute("SELECT * FROM deck_stats ORDER BY games DESC, deck_signature LIMIT 150").fetchall()
    cards = []
    for rank, row in enumerate(card_rows, start=1):
        card = repository._card_row(row)
        cards.append({"rank": rank, "card_name": card["card_name"], "rating": card["rating"], "usage_rate": card["usage_rate"], "usage_delta": 0.0, "win_rate": card["clean_win_rate"], "win_delta": 0.0, "clean_win_rate": card["clean_win_rate"], "appearance_count": card["appearances"], "source": provenance["source"], "sample_battles": sample_battles, "snapshot_id": repository.snapshot_id, "snapshot_group_id": repository.snapshot_group_id, "dataset_scope": repository.dataset_scope})
    decks = []
    for rank, row in enumerate(deck_rows, start=1):
        deck_cards = json.loads(row["deck_json"])
        decks.append({"rank": rank, "player_name": "Rolling Path of Legend sample", "clan_name": "Official Supercell API", "deck_name": " / ".join(deck_cards), "avg_elixir": None, "battles": row["games"], "usage_rate": row["usage_rate"], "cards": deck_cards, "sample_win_rate": row["clean_win_rate"], "wins": row["wins"], "losses": row["losses"], "draws": row["draws"], "source": provenance["source"], "sample_battles": sample_battles, "snapshot_id": repository.snapshot_id, "snapshot_group_id": repository.snapshot_group_id, "dataset_scope": repository.dataset_scope})
    card_deck_stats: dict[str, list[dict]] = {}
    for deck in decks:
        for card_name in deck["cards"]:
            card_deck_stats.setdefault(card_name, []).append(deck)
    for card_name, variants in card_deck_stats.items():
        card_deck_stats[card_name] = variants[:10]
    return {"cards_meta": cards, "top_decks": decks, "card_deck_stats": card_deck_stats, "provenance": provenance}
