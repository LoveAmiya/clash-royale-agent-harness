"""Card detail projections for structured statistics queries."""

from __future__ import annotations


def card_stats(repository, card_id: str, *, error_type: type[ValueError]) -> dict:
    card_id = repository._validate_card(card_id)
    with repository._connect() as connection:
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
        raise error_type("NO_CARD_EVIDENCE", "No evidence is available for this card.", status_code=404)
    card = repository._card_row(row)
    return {
        "card": card,
        "common_teammates": teammates,
        "common_opponents": opponents,
        "matched_sample_count": card["appearances"],
        "warning": repository._warning(card["appearances"]),
        "provenance": repository._provenance(),
    }


def compare_cards(repository, card_ids: list[str], *, error_type: type[ValueError]) -> dict:
    if not isinstance(card_ids, list) or len(card_ids) != 2 or len(set(card_ids)) != 2:
        raise error_type("INVALID_CARD_COMPARISON", "Card comparison requires exactly 2 distinct card IDs.")
    results = [repository.card_stats(card_id) for card_id in card_ids]
    cards = [result["card"] for result in results]
    metrics = ("usage_rate", "clean_win_rate", "net_win_rate", "rating", "appearances")
    return {
        "cards": cards,
        "differences": {metric: round(float(cards[0][metric]) - float(cards[1][metric]), 6) for metric in metrics},
        "matched_sample_count": [card["appearances"] for card in cards],
        "warnings": [result["warning"] for result in results if result["warning"]],
        "provenance": repository._provenance(),
    }


def card_pair_stats(repository, card_ids: list[str], *, error_type: type[ValueError]) -> dict:
    if not isinstance(card_ids, list) or len(card_ids) != 2 or len(set(card_ids)) != 2:
        raise error_type("INVALID_CARD_PAIR", "Card pair statistics require exactly 2 distinct card IDs.")
    first, second = [repository._validate_card(card_id) for card_id in card_ids]
    with repository._connect() as connection:
        row = connection.execute(
            "SELECT games, wins, losses, draws FROM card_teammates WHERE card_name=? AND teammate_name=?",
            (first, second),
        ).fetchone()
        if row is None:
            row = connection.execute(
                "SELECT games, wins, losses, draws FROM card_teammates WHERE card_name=? AND teammate_name=?",
                (second, first),
            ).fetchone()
    if row is None:
        raise error_type(
            "NO_CARD_PAIR_EVIDENCE",
            "No same-deck observations were found for this card pair.",
            status_code=404,
            details={"card_ids": [first, second], "matched_sample_count": 0},
        )
    decisions = int(row["wins"]) + int(row["losses"])
    games = int(row["games"])
    return {
        "cards": [first, second],
        "games": games,
        "wins": int(row["wins"]),
        "losses": int(row["losses"]),
        "draws": int(row["draws"]),
        "clean_win_rate": round(int(row["wins"]) / decisions * 100, 6) if decisions else 0.0,
        "matched_sample_count": games,
        "warning": repository._warning(games),
        "provenance": repository._provenance(),
    }


def card_teammate_rankings(repository, card_id: str, top_n: int = 10, *, aliases: dict[str, list[str]], error_type: type[ValueError]) -> dict:
    card_id = repository._validate_card(card_id)
    if not isinstance(top_n, int) or not 1 <= top_n <= 30:
        raise error_type("INVALID_TOP_N", "top_n must be an integer from 1 to 30.", details={"top_n": top_n})
    with repository._connect() as connection:
        rows = connection.execute(
            "SELECT teammate_name AS card_id, games, wins, losses, draws "
            "FROM card_teammates WHERE card_name=? ORDER BY games DESC, teammate_name LIMIT ?",
            (card_id, top_n),
        ).fetchall()
    teammates = []
    for row in rows:
        decisions = int(row["wins"]) + int(row["losses"])
        teammate_name = str(row["card_id"])
        teammates.append(
            {
                "card_id": teammate_name,
                "display_name_zh": aliases.get(teammate_name, [teammate_name])[0],
                "games": int(row["games"]),
                "wins": int(row["wins"]),
                "losses": int(row["losses"]),
                "draws": int(row["draws"]),
                "clean_win_rate": round(int(row["wins"]) / decisions * 100, 6) if decisions else 0.0,
            }
        )
    return {
        "card_id": card_id,
        "display_name_zh": aliases.get(card_id, [card_id])[0],
        "top_n": top_n,
        "teammates": teammates,
        "matched_sample_count": sum(item["games"] for item in teammates),
        "provenance": repository._provenance(),
    }


__all__ = ["card_stats", "compare_cards", "card_pair_stats", "card_teammate_rankings"]
