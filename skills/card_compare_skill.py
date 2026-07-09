from skills.base import DirectJSONSkill, SkillContext


class CardCompareSkill(DirectJSONSkill):
    name = "CardCompareSkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "card_compare_query"

    def run(self, context: SkillContext) -> str:
        parsed = context.parsed
        card_names = parsed.get("card_names") or []
        metric = parsed.get("compare_metric") or "usage_rate"

        if len(card_names) < 2:
            return "我不知道，你的问题里至少需要两张可识别的卡牌才能做对比。\n参考来源：无"

        metric_label_map = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率（CWR）",
        }
        metric_label = metric_label_map.get(metric, metric)

        selected_cards = []
        for name in card_names[:2]:
            for item in context.cards_meta_data:
                if str(item.get("card_name", "")).lower() == name.lower():
                    selected_cards.append(item)
                    break

        if len(selected_cards) < 2:
            return "我不知道，当前数据里找不到两张都存在的卡牌，无法完成对比。\n参考来源：无"

        first = selected_cards[0]
        second = selected_cards[1]
        first_value = float(first.get(metric, 0))
        second_value = float(second.get(metric, 0))
        diff = abs(first_value - second_value)

        if first_value > second_value:
            winner = first
            loser = second
            winner_value = first_value
            loser_value = second_value
        elif second_value > first_value:
            winner = second
            loser = first
            winner_value = second_value
            loser_value = first_value
        else:
            return (
                f"**{first.get('card_name')}** 和 **{second.get('card_name')}** 的**{metric_label}**相同，"
                f"都是 {first_value:.1f}%。\n\n"
                f"参考来源：\n"
                f"[1] cards_meta.json | rank={first.get('rank')} | {first.get('card_name')} | source={first.get('source')}\n"
                f"[2] cards_meta.json | rank={second.get('rank')} | {second.get('card_name')} | source={second.get('source')}"
            )

        return (
            f"按**{metric_label}**看，**{winner.get('card_name')}** 更高。\n"
            f"- {winner.get('card_name')}：{winner_value:.1f}%\n"
            f"- {loser.get('card_name')}：{loser_value:.1f}%\n"
            f"- 差值：{diff:.1f}%\n\n"
            f"参考来源：\n"
            f"[1] cards_meta.json | rank={first.get('rank')} | {first.get('card_name')} | source={first.get('source')}\n"
            f"[2] cards_meta.json | rank={second.get('rank')} | {second.get('card_name')} | source={second.get('source')}"
        )
