from skills.base import DirectJSONSkill, SkillContext


class CardRankLookupSkill(DirectJSONSkill):
    name = "CardRankLookupSkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "card_rank_lookup_query" and parsed.get("card_name") is not None

    def run(self, context: SkillContext) -> str:
        parsed = context.parsed
        card_name = parsed.get("card_name")
        metric = parsed.get("metric") or "usage_rate"

        metric_label_map = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率（CWR）",
        }
        metric_label = metric_label_map.get(metric, metric)

        sorted_cards = sorted(
            context.cards_meta_data,
            key=lambda item: (-float(item.get(metric, 0)), int(item.get("rank", 999999))),
        )

        target_index = None
        target_card = None
        for idx, item in enumerate(sorted_cards, start=1):
            if str(item.get("card_name", "")).lower() == str(card_name).lower():
                target_index = idx
                target_card = item
                break

        if target_card is None or target_index is None:
            return "我不知道，当前数据里没有这张卡牌的排名信息。\n参考来源：无"

        return (
            f"**{target_card.get('card_name')}** 当前按**{metric_label}**排序排在第 **{target_index}** 名。\n"
            f"- {metric_label}：{target_card.get(metric)}%\n"
            f"- 全局原始 rank：{target_card.get('rank')}\n"
            f"- 模式：{target_card.get('mode')}\n\n"
            f"参考来源：\n"
            f"[1] cards_meta.json | rank={target_card.get('rank')} | {target_card.get('card_name')} | source={target_card.get('source')}"
        )
