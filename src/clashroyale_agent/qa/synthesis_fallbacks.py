"""Fallback answer builders for QA/RAG synthesis paths."""

from __future__ import annotations

from typing import Any


def build_snapshot_fallback_answer(
    top_decks_data: list[dict[str, Any]],
    cards_meta_data: list[dict[str, Any]],
) -> str:
    """Return a conservative local-snapshot fallback when model synthesis fails."""
    top_decks = sorted(top_decks_data, key=lambda item: item.get("rank", 10**9))[:5]
    top_cards = sorted(
        cards_meta_data,
        key=lambda item: float(item.get("usage_rate", 0) or 0),
        reverse=True,
    )[:5]

    deck_lines = [
        f"- 第 {item.get('rank')} 名：{item.get('deck_name', '未命名卡组')}（平均费用 {item.get('avg_elixir', '未知')}）"
        for item in top_decks
    ]
    card_lines = [
        f"- {item.get('card_name', '未命名卡牌')}：使用率 {item.get('usage_rate', '未知')}%，胜率 {item.get('win_rate', '未知')}%"
        for item in top_cards
    ]

    return (
        "模型服务暂不可用，以下是基于本地数据快照的直接整理，不是 LLM 的策略推演。\n\n"
        "本地排行榜前五卡组：\n"
        + ("\n".join(deck_lines) if deck_lines else "- 当前没有可用的卡组快照。")
        + "\n\n"
        "快照中使用率靠前的卡牌：\n"
        + ("\n".join(card_lines) if card_lines else "- 当前没有可用的卡牌快照。")
        + "\n\n"
        "数据边界：该项目保存的是排行榜和单卡静态快照，不含全量卡组使用率分布、版本更新时间或实时对局样本；"
        "因此可以展示榜单前列构筑，但不能严谨断言它们就是整个实时环境中占比最高的流派。"
    )


def build_retrieved_evidence_fallback(compressed_results: list[dict[str, Any]]) -> str:
    """Expose retrieved current-scope evidence when synthesis times out."""
    evidence_lines = []
    for item in compressed_results:
        doc = item.get("doc") if isinstance(item, dict) else None
        if not isinstance(doc, dict):
            continue
        text = str(item.get("compressed_text") or doc.get("text") or "").strip()
        if not text:
            continue
        title = str(doc.get("metadata", {}).get("title") or doc.get("source_type") or "检索证据")
        evidence_lines.append(f"- {title}：{text}")
    return (
        "模型综合请求超时，已保留本次检索到的当前数据范围证据。"
        "以下内容是证据原文摘要，未进行额外推演：\n"
        + ("\n".join(evidence_lines) if evidence_lines else "- 当前没有可显示的检索证据。")
    )


__all__ = [
    "build_retrieved_evidence_fallback",
    "build_snapshot_fallback_answer",
]
