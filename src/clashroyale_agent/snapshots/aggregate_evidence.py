"""Derive aggregate RAG evidence from a validated snapshot's battle records."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, Callable


def build_aggregate_evidence_documents(
    snapshot: dict,
    common_metadata: dict,
    *,
    raw_record_type: type,
    raw_deck: Callable[[object, str], tuple[str, ...]],
    archetype_name: Callable[[tuple[str, ...]], str],
    archetype_family: Callable[[str], str],
    classifier_version: str,
    percent: Callable[[int, int], float],
    counter_summary: Callable[..., str],
    max_card_profile_documents: int,
    max_deck_profile_documents: int,
    max_archetype_documents: int,
    max_card_pair_documents: int,
    max_counter_documents: int,
    minimum_games: int,
) -> list[dict]:
    card_games: Counter[str] = Counter()
    card_wins: Counter[str] = Counter()
    card_teammates: dict[str, Counter[str]] = defaultdict(Counter)
    card_opponents: dict[str, Counter[str]] = defaultdict(Counter)
    deck_games: Counter[tuple[str, ...]] = Counter()
    deck_wins: Counter[tuple[str, ...]] = Counter()
    deck_opponents: dict[tuple[str, ...], Counter[tuple[str, ...]]] = defaultdict(Counter)
    pair_games: Counter[tuple[str, str]] = Counter()
    pair_wins: Counter[tuple[str, str]] = Counter()
    counter_games: Counter[tuple[str, str]] = Counter()
    counter_wins: Counter[tuple[str, str]] = Counter()
    archetype_games: Counter[str] = Counter()
    archetype_wins: Counter[str] = Counter()
    archetype_opponents: dict[str, Counter[str]] = defaultdict(Counter)
    archetype_matchup_wins: Counter[tuple[str, str]] = Counter()
    raw_records = snapshot.get("raw_battles", [])
    streamed = isinstance(raw_records, raw_record_type) or isinstance(snapshot.get("aggregate_store"), dict)
    tracked_decks: set[tuple[str, ...]] = set()
    if streamed:
        for deck in snapshot.get("top_decks", []):
            if isinstance(deck, dict):
                tracked = raw_deck(deck, "cards")
                if tracked:
                    tracked_decks.add(tracked)
        card_deck_stats = snapshot.get("card_deck_stats", {})
        if isinstance(card_deck_stats, dict):
            for variants in card_deck_stats.values():
                if isinstance(variants, list):
                    for deck in variants:
                        tracked = raw_deck(deck, "cards")
                        if tracked:
                            tracked_decks.add(tracked)
    observed_cards: set[str] = set()
    for record in raw_records:
        team_deck = raw_deck(record, "team_deck")
        if not team_deck:
            continue
        opponent_deck = raw_deck(record, "opponent_deck")
        observed_cards.update(team_deck)
        observed_cards.update(opponent_deck)
        if len(observed_cards) > 256:
            raise ValueError("official snapshot contains an unsafe number of distinct card names")
        team_won = bool(record.get("won")) if isinstance(record, dict) else False
        opponent_won = not team_won
        if isinstance(record, dict):
            team_crowns = record.get("team_crowns")
            opponent_crowns = record.get("opponent_crowns")
            if isinstance(team_crowns, int) and isinstance(opponent_crowns, int):
                team_won = team_crowns > opponent_crowns
                opponent_won = opponent_crowns > team_crowns
        perspectives = [(team_deck, opponent_deck, team_won)]
        if opponent_deck:
            perspectives.append((opponent_deck, team_deck, opponent_won))
        for deck, opposing_deck, won in perspectives:
            if not streamed or deck in tracked_decks:
                deck_games[deck] += 1
                deck_wins[deck] += int(won)
            if opposing_deck and not streamed:
                deck_opponents[deck][opposing_deck] += 1
            for card in deck:
                card_games[card] += 1
                card_wins[card] += int(won)
                card_teammates[card].update(other for other in deck if other != card)
                card_opponents[card].update(opposing_deck)
                for opposing_card in opposing_deck:
                    counter_games[(card, opposing_card)] += 1
                    counter_wins[(card, opposing_card)] += int(won)
            for pair in combinations(deck, 2):
                pair_games[pair] += 1
                pair_wins[pair] += int(won)
            archetype = archetype_name(deck)
            opponent_archetype = archetype_name(opposing_deck) if opposing_deck else "Unknown opponent family"
            archetype_games[archetype] += 1
            archetype_wins[archetype] += int(won)
            archetype_opponents[archetype][opponent_archetype] += 1
            archetype_matchup_wins[(archetype, opponent_archetype)] += int(won)

    snapshot_id = common_metadata["snapshot_id"]
    deck_profile_opponents = snapshot.get("deck_profile_opponents", {})
    documents: list[dict] = []
    for card, games in card_games.most_common(max_card_profile_documents):
        win_rate = percent(card_wins[card], games)
        documents.append({
            "doc_id": f"{snapshot_id}:card-profile:{card}", "source_type": "card_profile",
            "text": f"Card profile evidence. {card} appeared in {games} games with win rate {win_rate}%. Common teammate cards: {counter_summary(card_teammates[card])}. Common opposing cards: {counter_summary(card_opponents[card])}.",
            "metadata": {**common_metadata, "card_name": card, "games": games, "win_rate": win_rate},
        })
    for deck, games in deck_games.most_common(max_deck_profile_documents):
        if games < minimum_games:
            continue
        if streamed and isinstance(deck_profile_opponents, dict):
            observed = deck_profile_opponents.get(" / ".join(deck), [])
            opponents = ", ".join(f"{item.get('opponent_deck_name')} ({item.get('games')})" for item in observed if isinstance(item, dict) and item.get("opponent_deck_name") and item.get("games")) or "available in the exact aggregate store"
        else:
            opponents = counter_summary(Counter({" / ".join(key): value for key, value in deck_opponents[deck].items()}))
        sample_win_rate = percent(deck_wins[deck], games)
        documents.append({
            "doc_id": f"{snapshot_id}:deck-profile:{'|'.join(deck)}", "source_type": "deck_profile",
            "text": f"Deck profile evidence. {' / '.join(deck)} appeared in {games} games with win rate {sample_win_rate}%. Most observed opposing decks: {opponents}.",
            "metadata": {**common_metadata, "deck_name": " / ".join(deck), "cards": list(deck), "games": games, "sample_win_rate": sample_win_rate},
        })
    for archetype, games in archetype_games.most_common(max_archetype_documents):
        win_rate = percent(archetype_wins[archetype], games)
        matchups = []
        for opponent, matchup_games in archetype_opponents[archetype].most_common(3):
            wins = archetype_matchup_wins[(archetype, opponent)]
            matchups.append(f"{opponent} ({matchup_games} games, {percent(wins, matchup_games)}% win)")
        documents.append({
            "doc_id": f"{snapshot_id}:archetype:{archetype}", "source_type": "archetype",
            "text": f"Feature-weighted archetype evidence. {archetype} ({archetype_family(archetype)}) appeared in {games} games with win rate {win_rate}%. Frequent opposing families: {'; '.join(matchups) if matchups else 'none observed'}.",
            "metadata": {**common_metadata, "archetype": archetype, "archetype_family": archetype_family(archetype), "games": games, "win_rate": win_rate, "classification": classifier_version},
        })
    aggregate_limit = max_card_profile_documents + max_deck_profile_documents + max_archetype_documents + max_card_pair_documents
    for pair, games in pair_games.most_common():
        if games < minimum_games or len(documents) >= aggregate_limit:
            continue
        sample_win_rate = percent(pair_wins[pair], games)
        documents.append({"doc_id": f"{snapshot_id}:card-pair:{pair[0]}::{pair[1]}", "source_type": "card_pair", "text": f"Card-pair synergy evidence. {pair[0]} and {pair[1]} appeared together in {games} games; the observed deck win rate was {sample_win_rate}%.", "metadata": {**common_metadata, "cards": list(pair), "games": games, "sample_win_rate": sample_win_rate}})
    counter_documents = 0
    for (card, opponent_card), games in counter_games.most_common():
        if games < minimum_games or counter_documents >= max_counter_documents:
            continue
        win_rate = percent(counter_wins[(card, opponent_card)], games)
        documents.append({"doc_id": f"{snapshot_id}:counter:{card}::{opponent_card}", "source_type": "counter", "text": f"Observed counter evidence. Decks containing {card} faced {opponent_card} in {games} games; their observed win rate was {win_rate}%. This is sampled matchup evidence, not a causal counter claim.", "metadata": {**common_metadata, "card_name": card, "opponent_card_name": opponent_card, "games": games, "win_rate": win_rate}})
        counter_documents += 1
    return documents


__all__ = ["build_aggregate_evidence_documents"]
