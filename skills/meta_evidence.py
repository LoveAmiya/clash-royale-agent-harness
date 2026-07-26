"""将本地卡组、单卡和赛程快照整理成可供模型审阅的证据包。"""

from collections import Counter
from typing import Any


def _as_int(value: Any, default: int = 999_999) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _source_url(items: list[dict], fallback: str) -> str:
    for item in items:
        url = str(item.get("source_url", "")).strip()
        if url:
            return url
    return fallback


def _uses_supercell_live_sample(items: list[dict]) -> bool:
    return any(str(item.get("source", "")).strip() == "Supercell API live sample" for item in items)


def _live_snapshot_boundary(items: list[dict]) -> str | None:
    live_item = next(
        (
            item
            for item in items
            if str(item.get("source", "")).strip() == "Supercell API live sample"
            and item.get("sample_battles") is not None
        ),
        None,
    )
    if live_item is None:
        return None
    fields = [
        f"snapshot_id={live_item.get('snapshot_id', 'unknown')}",
        f"sample_battles={live_item.get('sample_battles')} 场",
        f"target_battles={live_item.get('target_battles', live_item.get('sample_battles'))} 场",
        f"fetched_at={live_item.get('fetched_at', 'unknown')}",
    ]
    return "Official snapshot boundary: " + " | ".join(fields)


def build_meta_evidence_pack(
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    *,
    deck_limit: int = 10,
    card_limit: int = 12,
    include_schedule: bool = True,
) -> tuple[str, str]:
    """返回模型可读的事实包和面向用户的来源清单。

    这里不产生策略结论，只做稳定的数据筛选、排序和聚合。策略判断由模型完成，
    并被提示词限制为不能杜撰本地快照中不存在的统计结论。
    """
    ranked_decks = sorted(top_decks_data, key=lambda item: _as_int(item.get("rank")))[:deck_limit]
    evidence_cards = [card for card in cards_meta_data if not card.get("_fallback_only")]
    if not evidence_cards:
        evidence_cards = cards_meta_data
    popular_cards = sorted(
        evidence_cards,
        key=lambda item: (-_as_float(item.get("usage_rate")), _as_int(item.get("rank"))),
    )[:card_limit]
    uses_live_deck_sample = _uses_supercell_live_sample(ranked_decks)
    uses_live_card_sample = _uses_supercell_live_sample(popular_cards)
    upcoming = sorted(
        [item for item in schedule_data if str(item.get("status", "")).lower() == "upcoming"],
        key=lambda item: (str(item.get("match_date", "")), _as_int(item.get("round"))),
    )

    card_frequency: Counter[str] = Counter()
    for deck in ranked_decks:
        card_frequency.update(str(card) for card in deck.get("cards", []) if str(card).strip())

    deck_lines = [
        (
            f"- rank={deck.get('rank')} | {deck.get('deck_name')} | "
            f"均费={deck.get('avg_elixir')} | 组件={', '.join(map(str, deck.get('cards', [])))}"
        )
        for deck in ranked_decks
    ]
    card_lines = [
        (
            f"- rank={card.get('rank')} | {card.get('card_name')} | "
            f"使用率={card.get('usage_rate')}% | 胜率={card.get('win_rate')}% | "
            f"净胜率={card.get('clean_win_rate')}%"
        )
        for card in popular_cards
    ]
    frequency_lines = [f"- {card}: 在 Top {len(ranked_decks)} 卡组样本中出现 {count} 次" for card, count in card_frequency.most_common(10)]

    schedule_lines: list[str] = []
    if upcoming:
        next_match = upcoming[0]
        schedule_lines.append(
            f"- 下一场：第 {next_match.get('round')} 轮，对手={next_match.get('opponent_team')}，"
            f"日期={next_match.get('match_date')}，赛制备注={next_match.get('note', '无')}"
        )

    snapshot_boundary = _live_snapshot_boundary([*ranked_decks, *popular_cards])
    evidence_sections = [
            (
                "Data freshness boundary: Supercell API live sample; deck and card evidence is a bounded battle-log sample "
                "collected for this request. It is not a global or full-season leaderboard."
                if uses_live_deck_sample or uses_live_card_sample
                else "数据时效边界：这是仓库内保存的静态快照（repository static snapshots），不是实时游戏数据。"
            ),
            *([snapshot_boundary] if snapshot_boundary else []),
            "热门卡组样本：",
            "\n".join(deck_lines) or "- 当前没有卡组样本。",
            "样本内高频组件：",
            "\n".join(frequency_lines) or "- 当前没有可统计的卡牌组件。",
            "高使用率单卡：",
            "\n".join(card_lines) or "- 当前没有单卡样本。",
        ]
    if include_schedule:
        evidence_sections.extend(
            [
                "赛程信息：",
                "\n".join(schedule_lines) or "- 当前没有 upcoming 赛程。",
            ]
        )
    evidence = "\n".join(evidence_sections)
    deck_source = (
        "[1] Supercell API live sample | bounded battle-log deck sample"
        if uses_live_deck_sample
        else "[1] top_decks.json | 静态快照 | " + _source_url(ranked_decks, "https://royaleapi.com/decks/leaderboard")
    )
    card_source = (
        "[2] Supercell API live sample | bounded battle-log card sample"
        if uses_live_card_sample
        else "[2] cards_meta.json | 静态快照 | " + _source_url(popular_cards, "https://royaleapi.com/cards/popular")
    )
    sources_list = [deck_source, card_source]
    if include_schedule and schedule_data:
        sources_list.append("[3] schedule.json | 本地赛程快照")
    sources = "\n".join(sources_list)
    return evidence, sources
