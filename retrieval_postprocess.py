import re
from typing import Any, Dict, List, Tuple


def simple_tokenize(text: str) -> List[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]", text)
    return [t for t in tokens if t.strip()]


def lexical_overlap_score(question: str, text: str) -> float:
    q_tokens = set(simple_tokenize(question))
    t_tokens = set(simple_tokenize(text))

    if not q_tokens or not t_tokens:
        return 0.0

    overlap = q_tokens & t_tokens
    return len(overlap) / max(1, len(q_tokens))


def metadata_match_bonus(parsed: Dict[str, Any], doc: Dict[str, Any]) -> float:
    bonus = 0.0
    meta = doc.get("metadata", {})

    rank = parsed.get("rank")
    round_no = parsed.get("round")
    card_name = parsed.get("card_name")
    ask_players = parsed.get("ask_players", False)

    if rank is not None and meta.get("rank") == rank:
        bonus += 0.45

    if round_no is not None and meta.get("round") == round_no:
        bonus += 0.45

    if card_name is not None and str(meta.get("card_name", "")).lower() == str(card_name).lower():
        bonus += 0.60

    if ask_players and doc.get("source_type") == "schedule":
        player_name = str(meta.get("player_name", "")).upper()
        opponent_player = str(meta.get("opponent_player", "")).upper()
        if player_name and player_name != "TBD":
            bonus += 0.15
        if opponent_player and opponent_player != "TBD":
            bonus += 0.15

    return min(bonus, 1.0)


def rerank_results(
    question: str,
    parsed: Dict[str, Any],
    results: List[Dict[str, Any]],
    top_n: int = 4,
) -> List[Dict[str, Any]]:
    reranked = []

    for item in results:
        doc = item["doc"]
        text = doc.get("text", "")

        base_score = float(item.get("final_score", 0.0))
        lexical_score = lexical_overlap_score(question, text)
        bonus_score = metadata_match_bonus(parsed, doc)

        rerank_score = 0.55 * base_score + 0.25 * lexical_score + 0.20 * bonus_score

        new_item = dict(item)
        new_item["rerank_score"] = rerank_score
        new_item["lexical_score"] = lexical_score
        new_item["bonus_score"] = bonus_score
        reranked.append(new_item)

    reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
    return reranked[:top_n]


def compress_doc(doc: Dict[str, Any]) -> str:
    source_type = doc.get("source_type")
    meta = doc.get("metadata", {})

    if source_type == "schedule":
        return (
            f"赛程：第 {meta.get('round')} 轮，"
            f"{meta.get('team_name')} vs {meta.get('opponent_team')}；"
            f"日期：{meta.get('match_date')}；"
            f"我方选手：{meta.get('player_name')}；"
            f"对手选手：{meta.get('opponent_player')}；"
            f"状态：{meta.get('status')}；"
            f"备注：{meta.get('note', '')}"
        )

    if source_type == "deck":
        cards = meta.get("cards", [])
        cards_text = "，".join(cards[:8])
        return (
            f"卡组：排名第 {meta.get('rank')}，"
            f"{meta.get('deck_name')}；"
            f"玩家：{meta.get('player_name')}；"
            f"平均费用：{meta.get('avg_elixir')}；"
            f"奖杯：{meta.get('trophies')}；"
            f"构成：{cards_text}"
        )

    if source_type == "card":
        return (
            f"卡牌：{meta.get('card_name')}；"
            f"排名：{meta.get('rank')}；"
            f"模式：{meta.get('mode')}；"
            f"评分：{meta.get('rating')}；"
            f"使用率：{meta.get('usage_rate')}%；"
            f"胜率：{meta.get('win_rate')}%；"
            f"净胜率：{meta.get('clean_win_rate')}%"
        )

    return doc.get("text", "")


def compress_results(
    results: List[Dict[str, Any]],
    max_items: int = 4,
    char_budget: int = 1200,
) -> List[Dict[str, Any]]:
    compressed = []
    seen_doc_ids = set()
    seen_texts = set()
    total_len = 0

    for item in results:
        doc = item["doc"]
        doc_id = doc.get("doc_id")
        short_text = compress_doc(doc)

        if doc_id in seen_doc_ids:
            continue
        if short_text in seen_texts:
            continue

        if compressed and total_len + len(short_text) > char_budget:
            continue

        new_item = dict(item)
        new_item["compressed_text"] = short_text

        compressed.append(new_item)
        seen_doc_ids.add(doc_id)
        seen_texts.add(short_text)
        total_len += len(short_text)

        if len(compressed) >= max_items:
            break

    return compressed


def build_context_and_refs(results: List[Dict[str, Any]]) -> Tuple[str, str]:
    context_lines = []
    refs = []

    for i, item in enumerate(results, start=1):
        doc = item["doc"]
        text = item.get("compressed_text", doc.get("text", ""))

        context_lines.append(
            f"[{i}] source_type: {doc['source_type']}\n"
            f"doc_id: {doc['doc_id']}\n"
            f"rerank_score: {item.get('rerank_score', item.get('final_score', 0.0)):.4f}\n"
            f"text: {text}"
        )

        refs.append(f"[{i}] {doc['source_type']} | {doc['doc_id']}")

    return "\n\n".join(context_lines), "\n".join(refs)
