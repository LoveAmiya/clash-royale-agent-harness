"""Deterministic local intent predicates for the query parser."""

from __future__ import annotations

import re
from typing import Callable

from clashroyale_agent.qa.parser_primitives import normalize_text


CardResolver = Callable[[str, list[dict]], str | None]
CardListResolver = Callable[[str, list[dict]], list[str]]


def is_asking_players(question: str) -> bool:
    q = question.lower()
    keywords = ["谁上", "谁打", "上场", "选手", "对战选手", "player", "who plays"]
    return any(k in q for k in keywords)


def is_schedule_query(question: str) -> bool:
    q = question.lower()
    keywords = ["下一轮", "赛程", "对战", "打谁", "上场", "round", "match", "轮"]
    return any(k in q for k in keywords)


def is_schedule_summary_query(question: str) -> bool:
    q = question.lower()
    explicit_phrases = [
        "接下来的赛程",
        "后面的赛程",
        "赛程压力",
        "赛程总结",
        "总结赛程",
        "总结一下赛程",
        "后面还有几场比赛",
        "还有几场比赛",
        "剩下几场比赛",
        "剩余几场比赛",
    ]
    if any(phrase in q for phrase in explicit_phrases):
        return True

    summary_intent_keywords = [
        "总结", "概况", "压力", "密集", "还有几场", "剩下几场", "剩余几场"
    ]
    schedule_domain_keywords = [
        "赛程", "比赛", "对阵", "下一轮", "后面几轮", "后续几轮", "轮次",
        "round", "match", "upcoming",
    ]

    return any(keyword in q for keyword in summary_intent_keywords) and any(
        keyword in q for keyword in schedule_domain_keywords
    )


def is_match_preparation_query(question: str) -> bool:
    q = question.lower()
    explicit_phrases = [
        "下一轮怎么准备",
        "下一场比赛有什么准备建议",
        "备战建议",
        "推荐几套可练的卡组",
        "帮我推荐几套可练的卡组",
        "给我备战建议",
    ]
    if any(phrase in q for phrase in explicit_phrases):
        return True

    preparation_keywords = ["准备", "备战", "练", "训练", "推荐"]
    match_domain_keywords = [
        "下一轮", "下一场", "比赛", "对手", "赛程", "卡组", "meta", "单卡"
    ]

    return any(keyword in q for keyword in preparation_keywords) and any(
        keyword in q for keyword in match_domain_keywords
    )


def is_meta_delta_query(question: str) -> bool:
    q = normalize_text(question)
    temporal_markers = (
        "最近环境发生了什么变化",
        "环境发生了什么变化",
        "环境有什么变化",
        "最近一周变化",
        "相比上周",
        "对比上周",
        "上周相比",
        "趋势变化",
        "meta change",
        "meta changes",
        "changed since last week",
        "compared with last week",
    )
    return any(marker in q for marker in temporal_markers)


def is_meta_analysis_query(question: str, resolve_card_name: CardResolver) -> bool:
    q = question.lower()
    if is_meta_delta_query(question):
        return True
    if any(
        phrase in q
        for phrase in (
            "current meta",
            "current environment",
            "meta decks",
            "mainstream decks",
        )
    ):
        return True
    analysis_keywords = [
        "当前版本",
        "当前环境",
        "现在的环境",
        "环境是怎样",
        "当前主流卡组",
        "整体环境",
        "环境是什么样",
        "环境怎么样",
        "meta环境",
        "进攻风格",
        "卡组构筑",
        "构筑思路",
        "卡组体系",
        "定位",
        "搭配",
        "主要怕什么",
        "克制",
        "反制",
        "速转",
        "空军",
        "重甲推进",
        "打法",
    ]
    domain_keywords = [
        "卡组", "卡牌", "单卡", "meta", "环境", "绿龙", "青龙", "龙宝", "baby dragon"
    ]
    return any(keyword in q for keyword in analysis_keywords) and (
        any(keyword in q for keyword in domain_keywords)
        or resolve_card_name(question, []) is not None
    )


def is_deck_query(question: str) -> bool:
    q = question.lower()
    keywords = ["热门卡组", "高使用率卡组", "最热门卡组", "卡组", "deck"]
    return any(k in q for k in keywords)


def is_card_query(
    question: str, cards_meta_data: list[dict], resolve_card_name: CardResolver
) -> bool:
    q = question.lower()
    keywords = ["使用率", "胜率", "单卡", "卡牌", "meta", "热门卡牌", "card"]
    return any(k in q for k in keywords) or resolve_card_name(question, cards_meta_data) is not None


def is_card_ranking_query(question: str) -> bool:
    q = question.lower()
    keywords = [
        "前", "排行", "排名", "高使用率", "热门卡牌", "使用率最高", "胜率最高",
        "top", "分别是谁", "第",
    ]
    return any(k in q for k in keywords)


def is_card_compare_query(
    question: str, cards_meta_data: list[dict], resolve_card_names: CardListResolver
) -> bool:
    q = question.lower()
    compare_keywords = ["哪个", "谁更", "更高", "更强", "比较", "vs", "对比"]
    return len(resolve_card_names(question, cards_meta_data)) >= 2 and any(
        k in q for k in compare_keywords
    )


def is_card_cooccurrence_query(
    question: str, cards_meta_data: list[dict], resolve_card_names: CardListResolver
) -> bool:
    q = question.lower()
    card_names = resolve_card_names(question, cards_meta_data)
    pair_markers = (
        "共同出现", "一起出现", "同时出现", "共现", "搭配了多少", "appear together"
    )
    teammate_markers = (
        "最常和", "最常与", "经常和", "经常与", "常和", "常与",
        "一起使用", "一起出现", "常见搭配", "队友", "teammate",
    )
    return (
        len(card_names) >= 2 and any(marker in q for marker in pair_markers)
    ) or (
        len(card_names) >= 1 and any(marker in q for marker in teammate_markers)
    )


def is_card_rank_lookup_query(
    question: str, cards_meta_data: list[dict], resolve_card_name: CardResolver
) -> bool:
    if resolve_card_name(question, cards_meta_data) is None:
        return False
    q = question.lower()
    ranking_keywords = ["排第几", "排名多少", "排名第几", "榜排第几", "榜排名多少", "榜单排名多少"]
    english_rank_lookup_phrases = [
        "ranking position", "rank position", "what rank", "what position"
    ]
    return any(keyword in q for keyword in ranking_keywords + english_rank_lookup_phrases)


def has_explicit_rank_signal(question: str) -> bool:
    patterns = [
        r"第\s*\d+\s*名",
        r"排名\s*\d+",
        r"第\s*[一二两三四五六七八九十]+\s*名",
        r"排名\s*[一二两三四五六七八九十]+",
    ]
    return any(re.search(pattern, question) for pattern in patterns)


def has_explicit_top_n_signal(question: str) -> bool:
    patterns = [
        r"(?:最高|最多|最常见|最常用|排名靠前)(?:的)?\s*\d+\s*(?:张|个)?",
        r"(?:最高|最多|最常见|最常用|排名靠前)(?:的)?\s*[一二两三四五六七八九十]+\s*(?:张|个)?",
        r"前\s*\d+",
        r"给我看\s*\d+\s*个",
        r"来\s*\d+\s*个",
        r"前\s*[一二两三四五六七八九十]+",
        r"\btop\s*\d+\b",
    ]
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in patterns)


def has_implicit_list_signal(question: str) -> bool:
    return any(
        keyword in question
        for keyword in ["有哪些", "哪些", "分别是谁", "都有什么", "几个", "一些"]
    )


__all__ = [
    "has_explicit_rank_signal",
    "has_explicit_top_n_signal",
    "has_implicit_list_signal",
    "is_asking_players",
    "is_card_compare_query",
    "is_card_cooccurrence_query",
    "is_card_query",
    "is_card_rank_lookup_query",
    "is_card_ranking_query",
    "is_deck_query",
    "is_match_preparation_query",
    "is_meta_analysis_query",
    "is_meta_delta_query",
    "is_schedule_query",
    "is_schedule_summary_query",
]
