"""RAG document assembly for a validated official snapshot."""

from __future__ import annotations

from typing import Callable


def build_snapshot_rag_documents(
    snapshot: dict,
    *,
    is_complete: Callable[[dict], bool],
    with_snapshot_metadata: Callable[[object, dict], list[dict]],
    build_aggregate_documents: Callable[[dict, dict], list[dict]],
    min_matchup_games: int,
) -> list[dict]:
    """Build compact evidence documents from one complete official snapshot."""
    if not is_complete(snapshot):
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
    for card in with_snapshot_metadata(snapshot.get("cards_meta"), snapshot):
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
    for deck in with_snapshot_metadata(snapshot.get("top_decks"), snapshot):
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
    matchups.sort(
        key=lambda item: (int(item.get("games", 0) or 0), float(item.get("win_rate", 0) or 0)),
        reverse=True,
    )
    for matchup in matchups:
        if int(matchup.get("games", 0) or 0) < min_matchup_games:
            continue
        documents.append(
            {
                "doc_id": f"{snapshot_id}:matchup:{matchup.get('deck_name')}::{matchup.get('opponent_deck_name')}",
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
    documents.extend(build_aggregate_documents(snapshot, common_metadata))
    return documents
