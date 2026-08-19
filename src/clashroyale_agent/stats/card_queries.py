"""Card catalog and ranking projections for structured statistics."""

from __future__ import annotations


def card_catalog(repository, *, aliases: dict[str, list[str]]) -> dict:
    cards = []
    for card_id in sorted(repository._catalog_names()):
        names = aliases.get(card_id, [])
        cards.append({"card_id": card_id, "display_name_zh": names[0] if names else card_id, "translation_status": "canonical" if names else "fallback_english"})
    return {"cards": cards, "card_count": len(cards), "provenance": repository._provenance()}


def card_rankings(repository, sort_by: str, *, metrics: tuple[str, ...], metric_definitions: dict, aliases: dict[str, list[str]], low_sample_threshold: int, error_type: type[ValueError]) -> dict:
    metric = str(sort_by or "usage_rate").strip()
    if metric not in metrics:
        raise error_type("INVALID_CARD_RANKING_METRIC", "sort_by must be usage_rate, clean_win_rate, or rating.", details={"sort_by": metric, "allowed": list(metrics)})
    with repository._connect() as connection:
        rows = connection.execute("SELECT * FROM card_stats").fetchall()
    cards = []
    for row in rows:
        card = repository._card_row(row)
        names = aliases.get(card["card_name"], [])
        cards.append({**card, "display_name_zh": names[0] if names else card["card_name"], "translation_status": "canonical" if names else "fallback_english", "is_low_sample": int(card["appearances"]) < low_sample_threshold})
    cards.sort(key=lambda card: (-float(card[metric]), -int(card["appearances"]), str(card["display_name_zh"]), str(card["card_name"])))
    previous_value = None; current_rank = 0
    for position, card in enumerate(cards, start=1):
        value = float(card[metric])
        if previous_value is None or value != previous_value:
            current_rank, previous_value = position, value
        card["rank"] = current_rank
    return {"sort_by": metric, "sort_order": "desc", "card_count": len(cards), "cards": cards, "low_sample_threshold": low_sample_threshold, "metric_definitions": dict(metric_definitions), "provenance": repository._provenance()}


__all__ = ["card_catalog", "card_rankings"]
