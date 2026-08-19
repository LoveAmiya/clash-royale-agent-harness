"""Deck profile and exact matchup projections."""

from __future__ import annotations

import json


def deck_profile(repository, cards: list[str], *, archetype_family, error_type: type[ValueError]) -> dict:
    deck, signature = repository._validate_deck(cards)
    with repository._connect() as connection:
        row = connection.execute("SELECT * FROM deck_stats WHERE deck_signature = ?", (signature,)).fetchone()
        matchup_rows = connection.execute(
            """SELECT *, CASE WHEN deck_a_signature = ? THEN wins_a ELSE wins_b END AS perspective_wins,
            CASE WHEN deck_a_signature = ? THEN wins_b ELSE wins_a END AS perspective_losses
            FROM matchup_stats WHERE deck_a_signature = ? OR deck_b_signature = ?
            ORDER BY CASE WHEN perspective_wins + perspective_losses > 0 THEN 1.0 * perspective_wins / (perspective_wins + perspective_losses) ELSE 0.0 END DESC,
            games DESC, deck_a_signature, deck_b_signature LIMIT 10""",
            (signature, signature, signature, signature),
        ).fetchall()
    if row is None:
        raise error_type("NO_DECK_EVIDENCE", "No exact evidence is available for this 8-card deck.", status_code=404, details={"cards": list(deck)})
    common_opponents = []
    for matchup in matchup_rows:
        is_a = matchup["deck_a_signature"] == signature
        opponent_signature = matchup["deck_b_signature"] if is_a else matchup["deck_a_signature"]
        wins = matchup["wins_a"] if is_a else matchup["wins_b"]
        losses = matchup["wins_b"] if is_a else matchup["wins_a"]
        decisions = wins + losses
        common_opponents.append({"cards": json.loads(opponent_signature), "games": matchup["games"], "wins": wins, "losses": losses, "draws": matchup["draws"], "clean_win_rate": round(wins / decisions * 100, 6) if decisions else 0.0})
    profile = dict(row)
    profile["cards"] = json.loads(profile.pop("deck_json"))
    profile["archetype_family"] = archetype_family(profile["archetype"])
    return {"deck": profile, "common_opponents": common_opponents, "matched_sample_count": profile["games"], "warning": repository._warning(profile["games"]), "provenance": repository._provenance()}


def deck_matchup(repository, deck_a_cards: list[str], deck_b_cards: list[str], *, error_type: type[ValueError]) -> dict:
    deck_a, signature_a = repository._validate_deck(deck_a_cards)
    deck_b, signature_b = repository._validate_deck(deck_b_cards)
    stored_a, stored_b = sorted((signature_a, signature_b))
    with repository._connect() as connection:
        row = connection.execute("SELECT * FROM matchup_stats WHERE deck_a_signature = ? AND deck_b_signature = ?", (stored_a, stored_b)).fetchone()
    if row is None:
        raise error_type("NO_MATCHUP_EVIDENCE", "No exact battles were found between these two 8-card decks.", status_code=404, details={"deck_a": list(deck_a), "deck_b": list(deck_b), "matched_sample_count": 0})
    requested_a_is_stored_a = signature_a == row["deck_a_signature"]
    wins_a = row["wins_a"] if requested_a_is_stored_a else row["wins_b"]
    wins_b = row["wins_b"] if requested_a_is_stored_a else row["wins_a"]
    crowns_a = row["crowns_a"] if requested_a_is_stored_a else row["crowns_b"]
    crowns_b = row["crowns_b"] if requested_a_is_stored_a else row["crowns_a"]
    decisions = wins_a + wins_b
    rate_a = round(wins_a / decisions * 100, 6) if decisions else 0.0
    games = row["games"]
    return {"deck_a": {"cards": list(deck_a), "wins": wins_a, "clean_win_rate": rate_a, "average_crowns": round(crowns_a / games, 6)}, "deck_b": {"cards": list(deck_b), "wins": wins_b, "clean_win_rate": round(100 - rate_a, 6) if decisions else 0.0, "average_crowns": round(crowns_b / games, 6)}, "games": games, "draws": row["draws"], "latest_battle_time": row["latest_battle_time"], "matched_sample_count": games, "warning": repository._warning(games), "provenance": repository._provenance()}
