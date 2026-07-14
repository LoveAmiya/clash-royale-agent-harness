import json
from pathlib import Path


DATA_DIR = Path("data")
SCHEDULE_FILE = DATA_DIR / "schedule.json"
TOP_DECKS_FILE = DATA_DIR / "top_decks.json"
CARDS_META_FILE = DATA_DIR / "cards_meta.json"
OUTPUT_FILE = DATA_DIR / "rag_documents.json"


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_schedule_docs(schedule_data: list[dict]) -> list[dict]:
    docs = []

    for item in schedule_data:
        round_no = item.get("round")
        match_date = item.get("match_date")
        team_name = item.get("team_name")
        player_name = item.get("player_name")
        opponent_team = item.get("opponent_team")
        opponent_player = item.get("opponent_player")
        status = item.get("status")
        note = item.get("note", "")

        text = (
            f"赛程信息。"
            f"第 {round_no} 轮，"
            f"{team_name} 对阵 {opponent_team}。"
            f"比赛日期是 {match_date}。"
            f"我方选手是 {player_name}。"
            f"对手选手是 {opponent_player}。"
            f"比赛状态是 {status}。"
            f"备注是 {note}。"
        )

        docs.append(
            {
                "doc_id": f"schedule_{round_no}",
                "source_type": "schedule",
                "text": text,
                "metadata": {
                    "round": round_no,
                    "match_date": match_date,
                    "team_name": team_name,
                    "player_name": player_name,
                    "opponent_team": opponent_team,
                    "opponent_player": opponent_player,
                    "status": status,
                "note": note,
                "source": "schedule.json",
                },
            }
        )

    return docs


def build_deck_docs(deck_data: list[dict]) -> list[dict]:
    docs = []

    for item in deck_data:
        rank = item.get("rank")
        player_name = item.get("player_name")
        clan_name = item.get("clan_name")
        deck_name = item.get("deck_name")
        avg_elixir = item.get("avg_elixir")
        battles = item.get("battles")
        trophies = item.get("trophies")
        cards = item.get("cards", [])

        cards_text = "，".join(cards)

        text = (
            f"热门卡组信息。"
            f"排名第 {rank} 的卡组是 {deck_name}。"
            f"使用该卡组的玩家是 {player_name}，战队是 {clan_name}。"
            f"平均费用是 {avg_elixir}。"
            f"战斗数是 {battles}。"
            f"奖杯数是 {trophies}。"
            f"卡组构成包括：{cards_text}。"
        )

        docs.append(
            {
                "doc_id": f"deck_{rank}",
                "source_type": "deck",
                "text": text,
                "metadata": {
                    "rank": rank,
                    "player_name": player_name,
                    "clan_name": clan_name,
                    "deck_name": deck_name,
                    "avg_elixir": avg_elixir,
                    "battles": battles,
                    "trophies": trophies,
                "cards": cards,
                "source": item.get("source", "top_decks.json"),
                "source_url": item.get("source_url", ""),
                },
            }
        )

    return docs


def build_card_docs(card_data: list[dict]) -> list[dict]:
    docs = []

    for item in card_data:
        rank = item.get("rank")
        card_name = item.get("card_name")
        rating = item.get("rating")
        usage_rate = item.get("usage_rate")
        usage_delta = item.get("usage_delta")
        win_rate = item.get("win_rate")
        win_delta = item.get("win_delta")
        clean_win_rate = item.get("clean_win_rate")
        mode = item.get("mode")

        text = (
            f"卡牌统计信息。"
            f"{card_name} 在 {mode} 模式下，"
            f"排名第 {rank}。"
            f"评分是 {rating}。"
            f"使用率是 {usage_rate}%，变化是 {usage_delta}%。"
            f"胜率是 {win_rate}%，变化是 {win_delta}%。"
            f"净胜率是 {clean_win_rate}%。"
        )

        docs.append(
            {
                "doc_id": f"card_{rank}_{card_name}",
                "source_type": "card",
                "text": text,
                "metadata": {
                    "rank": rank,
                    "card_name": card_name,
                    "rating": rating,
                    "usage_rate": usage_rate,
                    "usage_delta": usage_delta,
                    "win_rate": win_rate,
                    "win_delta": win_delta,
                    "clean_win_rate": clean_win_rate,
                "mode": mode,
                "source": item.get("source", "cards_meta.json"),
                "source_url": item.get("source_url", ""),
                },
            }
        )

    return docs


def build_strategy_docs() -> list[dict]:
    """提供可检索的通用对局原则，不将其伪装成版本实时统计。"""
    entries = [
        (
            "strategy_air_defense",
            "空军防守与反制",
            "面对空军训练假设时，卡组至少保留稳定对空单位和范围法术，避免两张关键对空牌同时被同一法术换掉。"
            "防守成功后，再用存活单位配合低费单位进行反推进；不要在对手核心空军单位未交前过早投入全部对空资源。",
        ),
        (
            "strategy_fast_cycle",
            "速转体系应对",
            "面对速转体系时，优先记录对手核心防守牌和小法术的轮转，不要用高费单位追逐每一次小进攻。"
            "通过费用正交换和保留关键解牌，等待对手关键卡不在手中时再发起完整推进。",
        ),
        (
            "strategy_heavy_push",
            "重甲推进应对",
            "面对重甲推进时，先确认主坦克和后排支援的位置，再分配建筑、单体高伤或拉扯单位处理主坦克，"
            "范围伤害或法术处理后排。不要把所有防守单位堆在同一路，以免被范围法术获得高收益。",
        ),
        (
            "strategy_bo3",
            "Bo3 备战",
            "Bo3 备战要让三套候选卡组覆盖不同节奏，并避免三套卡组共享同一种明显弱点。"
            "首局优先使用熟练度最高且信息暴露较少的体系；后续对局根据已出现的核心卡和法术调整，不把训练假设当作对手情报。",
        ),
    ]
    return [
        {
            "doc_id": doc_id,
            "source_type": "strategy",
            "text": f"通用战术手册。主题：{title}。{text}",
            "metadata": {
                "title": title,
                "source": "项目内置通用战术手册",
                "scope": "通用对局原则，不是版本实时统计或对手情报",
            },
        }
        for doc_id, title, text in entries
    ]


def main():
    schedule_data = load_json(SCHEDULE_FILE)
    deck_data = load_json(TOP_DECKS_FILE)
    card_data = load_json(CARDS_META_FILE)

    docs = []
    docs.extend(build_schedule_docs(schedule_data))
    docs.extend(build_deck_docs(deck_data))
    docs.extend(build_card_docs(card_data))
    docs.extend(build_strategy_docs())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)

    print(f"已生成 {len(docs)} 条 RAG 文档到 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
