"""Intent routing helpers for QA answer orchestration."""

from __future__ import annotations


def query_needs_rag(parsed: dict) -> bool:
    """Return whether a top-level parsed query needs RAG resources."""
    if parsed.get("intent") == "multi_intent":
        return any(query_needs_rag(subquery) for subquery in parsed.get("subqueries", []))
    intent = parsed.get("intent")
    if intent == "meta_analysis_query":
        return True
    if intent == "deck_query":
        return (
            not parsed.get("deck_cards")
            and parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    if intent == "card_query":
        return (
            parsed.get("entity_mode") != "loadout_entity"
            and parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    return False


def subquery_needs_rag(parsed: dict) -> bool:
    """Return whether an answer subquery needs RAG resources."""
    intent = parsed.get("intent")
    if intent == "meta_analysis_query":
        return True
    if intent == "deck_query":
        return (
            not parsed.get("deck_cards")
            and parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    if intent == "card_query":
        return (
            parsed.get("entity_mode") != "loadout_entity"
            and parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    return False


def subquery_title(parsed: dict) -> str:
    intent = parsed.get("intent")
    if intent == "card_query":
        return f"卡牌数据：{parsed.get('card_name') or '卡牌排行'}"
    if intent == "card_compare_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("compare_metric"), "表现")
        return f"{' 与 '.join(names[:2]) or '两张卡牌'} {metric}比较"
    if intent == "meta_analysis_query":
        return "环境分析：当前主流卡组"
    if intent == "match_preparation_query":
        return "已移除的战队备战功能"
    if intent == "card_cooccurrence_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        if len(names) >= 2:
            return f"{' 与 '.join(names[:2])} 共现统计"
        return f"{parsed.get('card_name') or '卡牌'} 常见搭配"
    if intent == "deck_query":
        if parsed.get("deck_cards"):
            return "精确八卡卡组统计"
        if parsed.get("card_name"):
            return f"{parsed['card_name']} 卡组"
        return "热门卡组"
    if intent == "schedule_query":
        return "已移除的战队赛程功能"
    return "子问题结果"


def subquery_user_text(parsed: dict, original_text: str) -> str:
    intent = parsed.get("intent")
    if intent == "meta_analysis_query":
        return original_text
    if intent == "match_preparation_query":
        return "已移除的战队备战功能"
    if (
        intent == "deck_query"
        and parsed.get("card_name") is None
        and parsed.get("rank") is None
        and parsed.get("top_n") is None
    ):
        return "当前热门卡组分析"
    if intent == "card_query" and parsed.get("entity_mode") == "loadout_entity":
        state = parsed.get("special_state") or "ordinary"
        return f"{state} {parsed.get('entity_name') or parsed.get('card_name') or ''} {' '.join(parsed.get('metrics') or [])}"
    if intent == "card_query" and parsed.get("card_name"):
        return f"{parsed['card_name']} {' '.join(parsed.get('metrics') or [])}"
    return original_text


__all__ = [
    "query_needs_rag",
    "subquery_needs_rag",
    "subquery_title",
    "subquery_user_text",
]
