"""Stable parser prompt and schema-level constants."""

from __future__ import annotations


PARSER_SYSTEM_PROMPT = (
    "你是一个查询参数解析器。\n"
    "请把用户问题解析成 JSON，不要输出多余解释。\n\n"
    "输出格式固定为：\n"
    "{\n"
    '  "intent": "schedule_query | schedule_summary_query | deck_query | card_query | card_compare_query | card_cooccurrence_query | card_rank_lookup_query | meta_analysis_query | match_preparation_query | reject",\n'
    '  "metric": "usage_rate | win_rate | clean_win_rate | null",\n'
    '  "compare_metric": "usage_rate | win_rate | clean_win_rate | null",\n'
    '  "rank": 具体名次或 null,\n'
    '  "top_n": 前几个或 null,\n'
    '  "card_name": 具体卡名或 null,\n'
    '  "card_names": ["标准卡名1", "标准卡名2"] 或 null,\n'
    '  "round": 轮次或 null,\n'
    '  "date": "YYYY-MM-DD 或 null",\n'
    '  "ask_players": true 或 false\n'
    "}\n\n"
    "规则：\n"
    "1. 问赛程、下一轮、谁上场、某轮打谁 -> schedule_query。\n"
    "1.1 问总结一下接下来的赛程、后面还有几场比赛、赛程压力怎么样 -> schedule_summary_query。\n"
    "1.2 问下一轮怎么准备、下一场比赛有什么准备建议、推荐可练卡组 -> match_preparation_query。\n"
    "1.3 问当前环境、当前主流卡组、卡牌定位、搭配、克制关系、打法或反制方案 -> meta_analysis_query。\n"
    "2. 问热门卡组、卡组排行、某名次卡组 -> deck_query。\n"
    "3. 问单卡使用率/胜率，或问前几张高使用率卡牌、某名次卡牌 -> card_query。\n"
    "3.1 问两张卡哪个更高/更强/谁更高，解析为 card_compare_query，并给出 card_names。\n"
    "3.2 问某张卡在某个榜单排第几，解析为 card_rank_lookup_query。\n"
    "4. “第三名/第3名/排名第三/第3个” 解析为 rank=3。\n"
    "5. “前20个/来5个/给我看几个” 解析为 top_n；如果只是“几个”且未给数字，默认 top_n=5。\n"
    "6. “最热门/最高使用率/第一名” 可以解析为 rank=1。\n"
    "7. 如果用户明确提到某张卡，card_name 填标准卡名，否则为 null。\n"
    "8. 问胜率前十 -> metric=win_rate；问净胜率 -> clean_win_rate；没特别说明 -> usage_rate。\n"
    "9. 如果用户提到具体比赛日期，date 填 YYYY-MM-DD。\n"
    "10. 如果无法归类，intent=reject。\n\n"
    "只输出 JSON。"
)

PARSER_SYSTEM_PROMPT += """

For independent requests joined by punctuation or conjunctions, return one object with
intent="multi_intent" and a "subqueries" array. Each subquery must have a stable id
(q1, q2, ...), one supported intent, and only that intent's fields. For a named card
asking more than one statistic, include metrics as an ordered array of usage_rate,
win_rate, and/or clean_win_rate while retaining metric as the first item. Never merge
an exact JSON statistic with an open-ended meta-analysis into one subquery.
For an exact eight-card deck, retain all canonical names in deck_cards. Questions about
two cards appearing together or the most common teammates use card_cooccurrence_query;
use card_names for an exact pair and card_name plus top_n for teammate rankings.
"""

LOCAL_PARSE_CONFIDENCE_HIGH = "high"
LOCAL_PARSE_CONFIDENCE_MEDIUM = "medium"
LOCAL_PARSE_CONFIDENCE_LOW = "low"

TOWER_ENTITY_NAMES = {"Tower Princess", "Dagger Duchess", "Royal Chef", "Cannoneer"}
MAX_SUBQUERIES = 4


__all__ = [
    "LOCAL_PARSE_CONFIDENCE_HIGH",
    "LOCAL_PARSE_CONFIDENCE_LOW",
    "LOCAL_PARSE_CONFIDENCE_MEDIUM",
    "MAX_SUBQUERIES",
    "PARSER_SYSTEM_PROMPT",
    "TOWER_ENTITY_NAMES",
]
