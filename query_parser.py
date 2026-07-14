"""将自由表达的玩家问题转换为经过校验的路由字段。

解析器优先使用兼容 LLM 的 JSON 契约，同时保留本地归一化和兜底规则作为确定性
安全网。因此 Router 消费的是标准卡名、范围受限的排名和已知意图，而不是原始自然语言。
"""

import json
import re
from typing import Any


# 路由前先归一化玩家昵称和中英文写法；标准 key 也是卡牌元数据和下游 Skill 使用的名称。
CARD_ALIASES = {
    "Hog Rider": ["hog rider", "hog", "野猪骑士", "猪"],
    "Miner": ["miner", "矿工"],
    "Poison": ["poison", "毒药"],
    "Firecracker": ["firecracker", "烟花炮手"],
    "Lava Hound": ["lava hound", "熔岩猎犬", "天狗"],
    "Balloon": ["balloon", "气球兵", "气球"],
    "Skeleton King": ["skeleton king", "骷髅王"],
    "Zappies": ["zappies", "电击车小队"],
    "Rascals": ["rascals", "淘气三人组"],
    "Tower Princess": ["tower princess", "公主塔"],
    "The Log": ["the log", "滚木"],
    "Skeletons": ["skeletons", "小骷髅"],
    "Fireball": ["fireball", "火球"],
    "Arrows": ["arrows", "箭雨"],
    "Tornado": ["tornado", "龙卷风"],
    "Barbarian Barrel": ["barbarian barrel", "滚桶", "野蛮人滚桶"],
    "Electro Spirit": ["electro spirit", "电灵"],
    "Dark Prince": ["dark prince", "黑王"],
    "Royal Giant": ["royal giant", "皇巨", "皇家巨人"],
    "X-Bow": ["x-bow", "xbow", "弩", "连弩"],
    "Goblin Drill": ["goblin drill", "钻机"],
    "Graveyard": ["graveyard", "墓园"],
    "Princess": ["princess", "公主"],
    "Monk": ["monk", "武僧"],
    "Goblin Cage": ["goblin cage", "哥布林牢笼"],
    "Freeze": ["freeze", "冰冻"],
    "Executioner": ["executioner", "刽子手"],
    "Electro Wizard": ["electro wizard", "电法"],
    "Baby Dragon": ["baby dragon", "绿龙", "青龙", "龙宝"],
}


CHINESE_NUM_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
    "三十": 30,
}


PARSER_SYSTEM_PROMPT = (
    "你是一个查询参数解析器。\n"
    "请把用户问题解析成 JSON，不要输出多余解释。\n\n"
    "输出格式固定为：\n"
    "{\n"
    '  "intent": "schedule_query | schedule_summary_query | deck_query | card_query | card_compare_query | card_rank_lookup_query | meta_analysis_query | match_preparation_query | reject",\n'
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
    "1.3 问当前环境、卡牌定位、搭配、克制关系、打法或反制方案 -> meta_analysis_query。\n"
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

LOCAL_PARSE_CONFIDENCE_HIGH = "high"
LOCAL_PARSE_CONFIDENCE_MEDIUM = "medium"
LOCAL_PARSE_CONFIDENCE_LOW = "low"


def normalize_text(text: str) -> str:
    return text.strip().lower()


def extract_text_content(result: Any) -> str:
    if hasattr(result, "get_text_content"):
        return result.get_text_content()
    return str(result)


def extract_json_block(text: str) -> dict | None:
    """尽力从模型输出中提取一个 JSON 对象。

    此处只做语法提取；后续归一化仍会在 Skill 接收结果前校验意图白名单和字段范围。
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def extract_cn_number(text: str) -> int | None:
    return CHINESE_NUM_MAP.get(text)


def coerce_rank_value(value: Any, max_n: int = 30) -> int | None:
    """将模型或用户提供的排名转换为安全的 1..max_n 整数边界。"""
    if isinstance(value, int):
        return max(1, min(value, max_n))
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return max(1, min(int(stripped), max_n))

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return max(1, min(cn_number, max_n))

    return extract_rank_target(stripped, max_n=max_n)


def coerce_top_n_value(value: Any, max_n: int = 30) -> int | None:
    if isinstance(value, int):
        return max(1, min(value, max_n))
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return max(1, min(int(stripped), max_n))

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return max(1, min(cn_number, max_n))

    extracted = extract_top_n(stripped, default=max_n, max_n=max_n)
    if extracted == max_n and stripped not in {"前30", "三十"} and not any(ch.isdigit() for ch in stripped):
        return None
    return extracted


def coerce_round_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return int(stripped)

    cn_number = extract_cn_number(stripped)
    if cn_number is not None:
        return cn_number

    return extract_round_number(stripped)


def extract_round_number(question: str) -> int | None:
    patterns = [
        r"第\s*(\d+)\s*轮",
        r"round\s*(\d+)",
        r"\br\s*(\d+)\b",
    ]
    q = question.lower()
    for pattern in patterns:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            return int(m.group(1))

    m_cn = re.search(r"第\s*([一二两三四五六七八九十]+)\s*轮", question)
    if m_cn:
        return extract_cn_number(m_cn.group(1))

    return None


def extract_date(question: str) -> str | None:
    iso_match = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", question)
    if iso_match:
        year, month, day = iso_match.group(1).split("-")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    md_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", question)
    if md_match:
        month = int(md_match.group(1))
        day = int(md_match.group(2))
        return f"2026-{month:02d}-{day:02d}"

    return None


def extract_rank_target(question: str, max_n: int = 30) -> int | None:
    patterns = [
        r"第\s*(\d+)\s*名",
        r"排名\s*(\d+)",
        r"第\s*(\d+)\s*个",
        r"第\s*(\d+)(?!\s*轮)",
    ]
    for pattern in patterns:
        m = re.search(pattern, question)
        if m:
            return max(1, min(int(m.group(1)), max_n))

    cn_patterns = [
        r"第\s*([一二两三四五六七八九十]+)\s*名",
        r"排名\s*([一二两三四五六七八九十]+)",
        r"第\s*([一二两三四五六七八九十]+)\s*个",
        r"第\s*([一二两三四五六七八九十]+)(?!\s*轮)",
    ]
    for pattern in cn_patterns:
        m = re.search(pattern, question)
        if m:
            n = extract_cn_number(m.group(1))
            if n is not None:
                return max(1, min(n, max_n))

    return None


def extract_top_n(question: str, default: int | None = None, max_n: int = 30) -> int | None:
    patterns = [
        r"前\s*(\d+)",
        r"给我看\s*(\d+)\s*个",
        r"来\s*(\d+)\s*个",
    ]
    for pattern in patterns:
        m = re.search(pattern, question)
        if m:
            return max(1, min(int(m.group(1)), max_n))

    cn_patterns = [
        r"前\s*([一二两三四五六七八九十]+)",
    ]
    for pattern in cn_patterns:
        m = re.search(pattern, question)
        if m:
            n = extract_cn_number(m.group(1))
            if n is not None:
                return max(1, min(n, max_n))

    if "几个" in question or "一些" in question:
        return min(5, max_n)

    if any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么"]):
        return min(5, max_n)

    return default


def resolve_card_name(text: str, cards_meta_data: list[dict]) -> str | None:
    """将别名解析为卡牌 Skill 使用的数据集标准名称。"""
    q = normalize_text(text)

    for card_name, aliases in CARD_ALIASES.items():
        if any(alias in q for alias in aliases):
            return card_name

    for item in cards_meta_data:
        card_name = str(item.get("card_name", ""))
        if card_name and card_name.lower() in q:
            return card_name

    return None


def resolve_card_names(text: str, cards_meta_data: list[dict]) -> list[str]:
    q = normalize_text(text)
    found: list[str] = []

    for card_name, aliases in CARD_ALIASES.items():
        if any(alias in q for alias in aliases) and card_name not in found:
            found.append(card_name)

    for item in cards_meta_data:
        card_name = str(item.get("card_name", ""))
        if card_name and card_name.lower() in q and card_name not in found:
            found.append(card_name)

    return found


def get_metric(question: str) -> str:
    q = question.lower()
    if "净胜率" in q or "cwr" in q:
        return "clean_win_rate"
    if "胜率" in q:
        return "win_rate"
    return "usage_rate"


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

    summary_intent_keywords = ["总结", "概况", "压力", "密集", "还有几场", "剩下几场", "剩余几场"]
    schedule_domain_keywords = ["赛程", "比赛", "对阵", "下一轮", "后面几轮", "后续几轮", "轮次", "round", "match", "upcoming"]

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
    match_domain_keywords = ["下一轮", "下一场", "比赛", "对手", "赛程", "卡组", "meta", "单卡"]

    return any(keyword in q for keyword in preparation_keywords) and any(
        keyword in q for keyword in match_domain_keywords
    )


def is_meta_analysis_query(question: str) -> bool:
    q = question.lower()
    analysis_keywords = [
        "当前版本",
        "当前环境",
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
    domain_keywords = ["卡组", "卡牌", "单卡", "meta", "环境", "绿龙", "青龙", "龙宝", "baby dragon"]
    return any(keyword in q for keyword in analysis_keywords) and (
        any(keyword in q for keyword in domain_keywords) or resolve_card_name(question, []) is not None
    )


def is_deck_query(question: str) -> bool:
    q = question.lower()
    keywords = ["热门卡组", "高使用率卡组", "最热门卡组", "卡组", "deck"]
    return any(k in q for k in keywords)


def is_card_query(question: str, cards_meta_data: list[dict]) -> bool:
    q = question.lower()
    keywords = ["使用率", "胜率", "单卡", "卡牌", "meta", "热门卡牌", "card"]
    return any(k in q for k in keywords) or resolve_card_name(question, cards_meta_data) is not None


def is_card_ranking_query(question: str) -> bool:
    q = question.lower()
    keywords = ["前", "排行", "排名", "高使用率", "热门卡牌", "使用率最高", "胜率最高", "top", "分别是谁", "第"]
    return any(k in q for k in keywords)


def is_card_compare_query(question: str, cards_meta_data: list[dict]) -> bool:
    q = question.lower()
    compare_keywords = ["哪个", "谁更", "更高", "更强", "比较", "vs", "对比"]
    return len(resolve_card_names(question, cards_meta_data)) >= 2 and any(k in q for k in compare_keywords)


def is_card_rank_lookup_query(question: str, cards_meta_data: list[dict]) -> bool:
    if resolve_card_name(question, cards_meta_data) is None:
        return False
    q = question.lower()
    ranking_keywords = ["排第几", "排名多少", "排名第几", "榜排第几", "榜排名多少", "榜单排名多少"]
    return any(keyword in q for keyword in ranking_keywords)


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
        r"前\s*\d+",
        r"给我看\s*\d+\s*个",
        r"来\s*\d+\s*个",
        r"前\s*[一二两三四五六七八九十]+",
        r"\btop\s*\d+\b",
    ]
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in patterns)


def has_implicit_list_signal(question: str) -> bool:
    return any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么", "几个", "一些"])


def build_parse_metadata(
    *,
    parse_source: str,
    parse_confidence: str,
    parse_reason: str,
) -> dict:
    return {
        "parse_source": parse_source,
        "parse_confidence": parse_confidence,
        "parse_reason": parse_reason,
    }


def merge_parse_metadata(parsed: dict, metadata: dict) -> dict:
    result = dict(parsed)
    result.update(metadata)
    return result


def infer_local_parse_metadata(parsed: dict, question: str) -> dict:
    q = question.lower()
    intent = parsed.get("intent")
    rank = parsed.get("rank")
    top_n = parsed.get("top_n")
    card_name = parsed.get("card_name")
    round_no = parsed.get("round")
    target_date = parsed.get("date")
    ask_players = parsed.get("ask_players", False)
    metric = parsed.get("metric")
    compare_metric = parsed.get("compare_metric")
    card_names = parsed.get("card_names") or []

    if intent == "reject":
        return build_parse_metadata(
            parse_source="local_reject",
            parse_confidence=LOCAL_PARSE_CONFIDENCE_LOW,
            parse_reason="local rules could not classify the query",
        )

    strong_signals = 0
    weak_signals = 0
    reasons = [f"intent={intent}"]

    if intent == "schedule_query":
        if round_no is not None:
            strong_signals += 1
            reasons.append("round matched")
        if target_date is not None:
            strong_signals += 1
            reasons.append("date matched")
        if ask_players:
            weak_signals += 1
            reasons.append("player intent matched")
        if any(keyword in q for keyword in ["下一轮", "赛程", "对战", "打谁", "上场", "round", "match", "轮"]):
            strong_signals += 1
            reasons.append("schedule keyword matched")

    elif intent == "schedule_summary_query":
        if is_schedule_summary_query(question):
            strong_signals += 2
            reasons.append("strict schedule summary pattern matched")

    elif intent == "match_preparation_query":
        if is_match_preparation_query(question):
            strong_signals += 2
            reasons.append("strict match preparation pattern matched")

    elif intent == "meta_analysis_query":
        if is_meta_analysis_query(question):
            strong_signals += 2
            reasons.append("strict meta analysis pattern matched")

    elif intent == "deck_query":
        if rank is not None and has_explicit_rank_signal(question):
            strong_signals += 1
            reasons.append("explicit rank matched")
        elif rank is not None:
            weak_signals += 1
            reasons.append("implicit rank inferred")
        if top_n is not None and has_explicit_top_n_signal(question):
            strong_signals += 1
            reasons.append("explicit top_n matched")
        elif top_n is not None and has_implicit_list_signal(question):
            weak_signals += 1
            reasons.append("implicit list size inferred")
        if "热门卡组" in question or "deck" in q or "卡组" in question:
            strong_signals += 1
            reasons.append("deck keyword matched")
        if metric is not None:
            weak_signals += 1
            reasons.append("metric inferred")

    elif intent == "card_query":
        if card_name is not None:
            strong_signals += 1
            reasons.append("card_name matched")
        if rank is not None and has_explicit_rank_signal(question):
            strong_signals += 1
            reasons.append("explicit rank matched")
        elif rank is not None:
            weak_signals += 1
            reasons.append("implicit rank inferred")
        if top_n is not None and has_explicit_top_n_signal(question):
            strong_signals += 1
            reasons.append("explicit top_n matched")
        elif top_n is not None and has_implicit_list_signal(question):
            weak_signals += 1
            reasons.append("implicit list size inferred")
        if (
            ("胜率" in question)
            or ("净胜率" in question)
            or ("使用率" in question)
            or ("cwr" in q)
        ):
            strong_signals += 1
            reasons.append("metric keyword matched")
        elif metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            weak_signals += 1
            reasons.append("metric inferred")
        if "卡牌" in question or "热门卡牌" in question or "card" in q:
            strong_signals += 1
            reasons.append("card keyword matched")

    elif intent == "card_compare_query":
        if len(card_names) >= 2:
            strong_signals += 1
            reasons.append("multiple card names matched")
        if any(keyword in q for keyword in ["哪个", "谁更", "更高", "更强", "比较", "vs", "对比"]):
            strong_signals += 1
            reasons.append("compare keyword matched")
        if compare_metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            strong_signals += 1
            reasons.append("compare metric matched")

    elif intent == "card_rank_lookup_query":
        if card_name is not None:
            strong_signals += 1
            reasons.append("card_name matched")
        if metric in {"usage_rate", "win_rate", "clean_win_rate"}:
            strong_signals += 1
            reasons.append("metric matched")
        if any(keyword in q for keyword in ["排第几", "排名多少", "排名第几", "榜排第几", "榜排名多少", "榜单排名多少"]):
            strong_signals += 1
            reasons.append("rank lookup keyword matched")

    if strong_signals >= 2:
        confidence = LOCAL_PARSE_CONFIDENCE_HIGH
    elif strong_signals >= 1 or weak_signals >= 2:
        confidence = LOCAL_PARSE_CONFIDENCE_MEDIUM
    else:
        confidence = LOCAL_PARSE_CONFIDENCE_LOW

    return build_parse_metadata(
        parse_source="local_rule",
        parse_confidence=confidence,
        parse_reason=", ".join(reasons),
    )


def fallback_parse_query(question: str, cards_meta_data: list[dict]) -> dict:
    """结构化模型输出失败时提供确定性的路由字段。

    兜底逻辑刻意保守：保留可追溯的本地依据，不会把无法识别的问题伪装为合法 Skill 调用。
    """
    intent = "reject"
    if is_schedule_summary_query(question):
        intent = "schedule_summary_query"
    elif is_match_preparation_query(question):
        intent = "match_preparation_query"
    elif is_meta_analysis_query(question):
        intent = "meta_analysis_query"
    elif is_card_compare_query(question, cards_meta_data):
        intent = "card_compare_query"
    elif is_card_rank_lookup_query(question, cards_meta_data):
        intent = "card_rank_lookup_query"
    elif is_schedule_query(question):
        intent = "schedule_query"
    elif is_deck_query(question):
        intent = "deck_query"
    elif is_card_query(question, cards_meta_data):
        intent = "card_query"

    card_name = resolve_card_name(question, cards_meta_data)
    card_names = resolve_card_names(question, cards_meta_data)
    metric = get_metric(question) if intent in {"deck_query", "card_query", "card_rank_lookup_query"} else None
    compare_metric = get_metric(question) if intent == "card_compare_query" else None
    rank_target = extract_rank_target(question, max_n=30)
    top_n = extract_top_n(question, default=None, max_n=30)
    round_no = extract_round_number(question)
    target_date = extract_date(question)

    if card_name and not is_card_ranking_query(question):
        rank_target = None
        top_n = None

    if rank_target is not None:
        top_n = None

    if intent == "schedule_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None

    if intent == "schedule_summary_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "match_preparation_query":
        metric = None
        compare_metric = None
        card_name = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "meta_analysis_query":
        metric = None
        compare_metric = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "deck_query":
        card_name = None
        card_names = None

    if intent == "card_query":
        card_names = None

    if intent == "card_rank_lookup_query":
        compare_metric = None
        card_names = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None

    if intent == "card_compare_query":
        metric = None
        card_name = None
        rank_target = None
        top_n = None
        round_no = None
        target_date = None
        if not card_names:
            card_names = None

    if intent == "deck_query" and rank_target is None and top_n is None:
        if any(keyword in question for keyword in ["热门卡组", "高使用率卡组", "最热门卡组", "卡组"]):
            if any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么"]):
                top_n = 5

    if intent == "card_query" and card_name is None and rank_target is None and top_n is None:
        if any(keyword in question for keyword in ["热门卡牌", "高使用率卡牌", "使用率最高", "胜率最高"]):
            if any(keyword in question for keyword in ["有哪些", "哪些", "分别是谁", "都有什么"]):
                top_n = 5

    parsed = {
        "intent": intent,
        "metric": metric,
        "compare_metric": compare_metric,
        "rank": rank_target,
        "top_n": top_n,
        "card_name": card_name,
        "card_names": card_names,
        "round": round_no,
        "date": target_date,
        "ask_players": is_asking_players(question),
    }
    return merge_parse_metadata(parsed, infer_local_parse_metadata(parsed, question))


def normalize_parsed_query(parsed: dict, question: str, cards_meta_data: list[dict]) -> dict:
    """在 Router/Skill 选择前校验并修复解析字段。

    这是模型输出的可信边界：它会限制数值范围、标准化卡牌别名，并写入解析置信度元数据。
    """
    result = {
        "intent": parsed.get("intent"),
        "metric": parsed.get("metric"),
        "compare_metric": parsed.get("compare_metric"),
        "rank": parsed.get("rank"),
        "top_n": parsed.get("top_n"),
        "card_name": parsed.get("card_name"),
        "card_names": parsed.get("card_names"),
        "round": parsed.get("round"),
        "date": parsed.get("date"),
        "ask_players": parsed.get("ask_players", False),
        "parse_source": parsed.get("parse_source"),
        "parse_confidence": parsed.get("parse_confidence"),
        "parse_reason": parsed.get("parse_reason"),
    }

    if result["intent"] not in {"schedule_query", "schedule_summary_query", "deck_query", "card_query", "card_compare_query", "card_rank_lookup_query", "meta_analysis_query", "match_preparation_query", "reject"}:
        return fallback_parse_query(question, cards_meta_data)

    # Model-provided Chinese aliases must become the same canonical keys used by JSON Skills.
    if isinstance(result["card_name"], str):
        result["card_name"] = (
            resolve_card_name(result["card_name"], cards_meta_data)
            or resolve_card_name(question, cards_meta_data)
        )
    if isinstance(result["card_names"], list):
        canonical_names: list[str] = []
        for raw_name in result["card_names"]:
            canonical_name = resolve_card_name(str(raw_name), cards_meta_data)
            if canonical_name and canonical_name not in canonical_names:
                canonical_names.append(canonical_name)
        for canonical_name in resolve_card_names(question, cards_meta_data):
            if canonical_name not in canonical_names:
                canonical_names.append(canonical_name)
        result["card_names"] = canonical_names

    if result["metric"] not in {"usage_rate", "win_rate", "clean_win_rate", None}:
        result["metric"] = get_metric(question)
    if result["compare_metric"] not in {"usage_rate", "win_rate", "clean_win_rate", None}:
        result["compare_metric"] = get_metric(question)

    coerced_rank = coerce_rank_value(result["rank"], max_n=30)
    if coerced_rank is not None:
        result["rank"] = coerced_rank

    coerced_top_n = coerce_top_n_value(result["top_n"], max_n=30)
    if coerced_top_n is not None:
        result["top_n"] = coerced_top_n

    coerced_round = coerce_round_value(result["round"])
    if coerced_round is not None:
        result["round"] = coerced_round

    if not isinstance(result["ask_players"], bool):
        result["ask_players"] = is_asking_players(question)

    if result["intent"] == "schedule_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        if not isinstance(result["round"], int):
            result["round"] = extract_round_number(question)
        if not result["date"]:
            result["date"] = extract_date(question)

    if result["intent"] == "schedule_summary_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None

    if result["intent"] == "match_preparation_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_name"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None

    if result["intent"] == "meta_analysis_query":
        result["metric"] = None
        result["compare_metric"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not result["card_name"]:
            result["card_name"] = resolve_card_name(question, cards_meta_data)

    if result["intent"] == "deck_query":
        result["card_name"] = None
        result["card_names"] = None
        if result["metric"] is None:
            result["metric"] = "usage_rate"

    if result["intent"] == "card_query":
        result["card_names"] = None
        if not result["card_name"]:
            result["card_name"] = resolve_card_name(question, cards_meta_data)
        if result["card_name"] and not is_card_ranking_query(question):
            result["rank"] = None
            result["top_n"] = None
        if result["metric"] is None:
            result["metric"] = get_metric(question)

    if result["intent"] == "card_rank_lookup_query":
        result["compare_metric"] = None
        result["card_names"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not result["card_name"]:
            result["card_name"] = resolve_card_name(question, cards_meta_data)
        if result["metric"] is None:
            result["metric"] = get_metric(question)

    if result["intent"] == "card_compare_query":
        result["metric"] = None
        result["card_name"] = None
        result["rank"] = None
        result["top_n"] = None
        result["round"] = None
        result["date"] = None
        if not isinstance(result["card_names"], list) or len(result["card_names"]) < 2:
            result["card_names"] = resolve_card_names(question, cards_meta_data)
        if result["compare_metric"] is None:
            result["compare_metric"] = get_metric(question)

    if not result["parse_source"]:
        result["parse_source"] = "llm_parser"
    if result["parse_confidence"] not in {
        LOCAL_PARSE_CONFIDENCE_HIGH,
        LOCAL_PARSE_CONFIDENCE_MEDIUM,
        LOCAL_PARSE_CONFIDENCE_LOW,
    }:
        result["parse_confidence"] = LOCAL_PARSE_CONFIDENCE_MEDIUM
    if not result["parse_reason"]:
        result["parse_reason"] = "normalized parser output"

    return result
