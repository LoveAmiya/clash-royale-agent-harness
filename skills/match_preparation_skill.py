from skills.base import DirectJSONSkill, SkillContext


class MatchPreparationSkill(DirectJSONSkill):
    name = "MatchPreparationSkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "match_preparation_query"

    def run(self, context: SkillContext) -> str:
        upcoming = [
            item
            for item in context.schedule_data
            if str(item.get("status", "")).lower() == "upcoming"
        ]

        if not upcoming:
            return (
                "当前 schedule.json 中没有 upcoming 比赛，暂时无法生成下一轮备战建议。\n"
                "参考来源：\n"
                "[1] schedule.json | upcoming_count=0"
            )

        sorted_upcoming = sorted(
            upcoming,
            key=lambda item: (str(item.get("match_date", "")), int(item.get("round", 999999))),
        )
        next_match = sorted_upcoming[0]

        top_decks = sorted(
            context.top_decks_data,
            key=lambda item: int(item.get("rank", 999999)),
        )[:5]

        top_cards = sorted(
            context.cards_meta_data,
            key=lambda item: (-float(item.get("usage_rate", 0)), int(item.get("rank", 999999))),
        )[:10]

        deck_lines = []
        for idx, deck in enumerate(top_decks, start=1):
            deck_lines.append(
                f"{idx}. {deck.get('deck_name')}（rank={deck.get('rank')}，均费={deck.get('avg_elixir')}）"
            )

        card_lines = []
        for idx, card in enumerate(top_cards, start=1):
            card_lines.append(
                f"{idx}. {card.get('card_name')}（使用率={card.get('usage_rate')}%，胜率={card.get('win_rate')}%）"
            )

        note_values = []
        for item in sorted_upcoming:
            note = str(item.get("note", "")).strip()
            if note and note not in note_values:
                note_values.append(note)
        note_text = "、".join(note_values) if note_values else "无特殊备注"

        return (
            "以下建议基于当前赛程、热门卡组和单卡 meta 数据生成，不代表对手真实出牌预测。\n\n"
            f"下一轮赛程信息：\n"
            f"- 第 {next_match.get('round')} 轮 vs {next_match.get('opponent_team')}\n"
            f"- 比赛日期：{next_match.get('match_date')}\n"
            f"- 备注：{next_match.get('note', '')}\n\n"
            f"热门卡组参考：\n"
            + "\n".join(deck_lines)
            + "\n\n"
            + "重点卡牌参考：\n"
            + "\n".join(card_lines)
            + "\n\n"
            + "训练建议：\n"
            + f"- 优先围绕下一轮赛程准备常见对局节奏，先熟悉 {next_match.get('note', '') or '当前赛制'} 下的出牌与换牌思路。\n"
            + "- 先从热门卡组里选 2 到 3 套做针对性训练，重点练防守转换和对主流法术的处理。\n"
            + "- 对使用率靠前的重点卡牌做针对练习，确保关键解牌、费用交换和轮转节奏稳定。\n"
            + f"- 如果后续赛程备注集中在 {note_text}，训练时要同步适应该赛制节奏。\n\n"
            + "数据限制说明：\n"
            + "- 这里只基于 schedule.json、top_decks.json、cards_meta.json 做静态整理。\n"
            + "- 不能据此判断对手真实卡组、真实上场选手或实际临场选择。\n\n"
            + "参考来源：\n"
            + f"[1] schedule.json | next_round={next_match.get('round')} | {next_match.get('opponent_team')}\n"
            + f"[2] top_decks.json | top_n={len(top_decks)}\n"
            + f"[3] cards_meta.json | top_usage_n={len(top_cards)}"
        )
