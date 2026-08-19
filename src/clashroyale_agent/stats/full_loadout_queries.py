"""Full tower/evolution/elite loadout query projections."""

from __future__ import annotations

import json
import sqlite3


def full_loadout_profile(repository, loadout: dict, *, error_type: type[ValueError]) -> dict:
    _, signature = repository._validate_loadout(loadout)
    try:
        with repository._connect() as connection:
            row = connection.execute("SELECT * FROM full_loadout_stats WHERE loadout_signature=?", (signature,)).fetchone()
            matchup_rows = connection.execute("""SELECT *, CASE WHEN loadout_a_signature=? THEN wins_a ELSE wins_b END AS perspective_wins,
                CASE WHEN loadout_a_signature=? THEN wins_b ELSE wins_a END AS perspective_losses
                FROM full_loadout_matchup_stats WHERE loadout_a_signature=? OR loadout_b_signature=?
                ORDER BY CASE WHEN perspective_wins+perspective_losses>0 THEN 1.0*perspective_wins/(perspective_wins+perspective_losses) ELSE 0.0 END DESC,
                games DESC, loadout_a_signature, loadout_b_signature LIMIT 10""", (signature, signature, signature, signature)).fetchall()
    except sqlite3.OperationalError as exc:
        raise error_type("FULL_LOADOUT_NOT_READY", "The selected snapshot does not contain full-loadout evidence yet.", status_code=503, details={"dataset_scope": repository.dataset_scope}) from exc
    if row is None:
        raise error_type("NO_FULL_LOADOUT_EVIDENCE", "No exact evidence is available for this tower, evolution, and elite configuration.", status_code=404, details={"matched_sample_count": 0, "deck_mode": "full_loadout"})
    profile = dict(row)
    profile["loadout"] = repository._display_loadout(json.loads(profile.pop("loadout_json")))
    opponents = []
    for matchup in matchup_rows:
        is_a = matchup["loadout_a_signature"] == signature
        opponent_signature = matchup["loadout_b_signature"] if is_a else matchup["loadout_a_signature"]
        wins = matchup["wins_a"] if is_a else matchup["wins_b"]
        losses = matchup["wins_b"] if is_a else matchup["wins_a"]
        decisions = wins + losses
        with repository._connect() as connection:
            opponent_row = connection.execute("SELECT loadout_json FROM full_loadout_stats WHERE loadout_signature=?", (opponent_signature,)).fetchone()
        opponents.append({"loadout": repository._display_loadout(json.loads(opponent_row[0])) if opponent_row else None, "games": matchup["games"], "wins": wins, "losses": losses, "draws": matchup["draws"], "clean_win_rate": round(wins * 100 / decisions, 6) if decisions else 0.0})
    return {"deck_mode": "full_loadout", "loadout": profile, "common_opponents": opponents, "matched_sample_count": profile["games"], "warning": repository._warning(profile["games"]), "provenance": {**repository._provenance(), "deck_mode": "full_loadout"}}


def full_loadout_matchup(repository, loadout_a: dict, loadout_b: dict, *, error_type: type[ValueError]) -> dict:
    normalized_a, signature_a = repository._validate_loadout(loadout_a)
    normalized_b, signature_b = repository._validate_loadout(loadout_b)
    stored_a, stored_b = sorted((signature_a, signature_b))
    try:
        with repository._connect() as connection:
            row = connection.execute("SELECT * FROM full_loadout_matchup_stats WHERE loadout_a_signature=? AND loadout_b_signature=?", (stored_a, stored_b)).fetchone()
    except sqlite3.OperationalError as exc:
        raise error_type("FULL_LOADOUT_NOT_READY", "The selected snapshot does not contain full-loadout evidence yet.", status_code=503, details={"dataset_scope": repository.dataset_scope}) from exc
    if row is None:
        raise error_type("NO_FULL_LOADOUT_MATCHUP_EVIDENCE", "No exact battles were found between these two complete configurations.", status_code=404, details={"matched_sample_count": 0, "deck_mode": "full_loadout"})
    requested_a_is_stored_a = signature_a == row["loadout_a_signature"]
    wins_a = row["wins_a"] if requested_a_is_stored_a else row["wins_b"]
    wins_b = row["wins_b"] if requested_a_is_stored_a else row["wins_a"]
    crowns_a = row["crowns_a"] if requested_a_is_stored_a else row["crowns_b"]
    crowns_b = row["crowns_b"] if requested_a_is_stored_a else row["crowns_a"]
    decisions = wins_a + wins_b
    games = row["games"]
    rate_a = round(wins_a * 100 / decisions, 6) if decisions else 0.0
    provenance = {**repository._provenance(), "deck_mode": "full_loadout"}
    return {"deck_mode": "full_loadout", "loadout_a": {"loadout": repository._display_loadout(normalized_a), "wins": wins_a, "clean_win_rate": rate_a, "average_crowns": round(crowns_a / games, 6)}, "loadout_b": {"loadout": repository._display_loadout(normalized_b), "wins": wins_b, "clean_win_rate": round(100 - rate_a, 6) if decisions else 0.0, "average_crowns": round(crowns_b / games, 6)}, "games": games, "draws": row["draws"], "latest_battle_time": row["latest_battle_time"], "matched_sample_count": games, "warning": repository._warning(games), "provenance": provenance}
