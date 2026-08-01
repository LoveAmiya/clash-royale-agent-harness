def build_card_source_reference(card: dict, index: int = 1) -> str:
    if card.get("source") == "Supercell API live sample":
        return (
            f"[{index}] Supercell API live sample | rank={card.get('rank')} | {card.get('card_name')} "
            f"| sample_battles={card.get('sample_battles', 0)}"
        )
    return f"[{index}] cards_meta.json | rank={card.get('rank')} | {card.get('card_name')} | source={card.get('source')}"


def get_next_round_matches(schedule_data: list[dict]) -> list[dict]:
    upcoming = [x for x in schedule_data if str(x.get("status", "")).lower() == "upcoming"]
    if not upcoming:
        return []

    next_round = min(int(x.get("round", 999999)) for x in upcoming)
    return [x for x in upcoming if int(x.get("round", 999999)) == next_round]


def build_schedule_answer(parsed: dict, schedule_data: list[dict]) -> str:
    target_round = parsed.get("round")
    target_date = parsed.get("date")
    ask_players = parsed.get("ask_players", False)

    if target_date:
        matches = [x for x in schedule_data if str(x.get("match_date", "")) == target_date]
    elif target_round is not None:
        matches = [x for x in schedule_data if int(x.get("round", 999999)) == target_round]
    else:
        matches = get_next_round_matches(schedule_data)

    if not matches:
        return "我不知道，当前数据里没有对应赛程信息。\n参考来源：无"

    if len(matches) == 1:
        item = matches[0]
        round_no = item.get("round")
        opponent_team = item.get("opponent_team")
        player_name = item.get("player_name")
        opponent_player = item.get("opponent_player")
        match_date = item.get("match_date")
        note = item.get("note", "")

        if ask_players:
            if str(player_name).upper() == "TBD" or str(opponent_player).upper() == "TBD":
                return (
                    f"当前已知下一场/该轮对手是 **{opponent_team}**，比赛时间为 {match_date}；"
                    f"但赛程里还没有填写具体上场选手。\n\n"
                    f"参考来源：\n"
                    f"[1] schedule.json | round={round_no} | 电子科技大学与四川大学联队 vs {opponent_team}"
                )
            return (
                f"第 {round_no} 轮我方选手 **{player_name}** 对阵 **{opponent_player}**（{opponent_team}），"
                f"比赛时间为 {match_date}，备注：{note}。\n\n"
                f"参考来源：\n"
                f"[1] schedule.json | round={round_no} | {player_name} vs {opponent_player}"
            )

        return (
            f"我们第 {round_no} 轮对阵 **{opponent_team}**，比赛时间为 {match_date}，备注：{note}。\n\n"
            f"参考来源：\n"
            f"[1] schedule.json | round={round_no} | 电子科技大学与四川大学联队 vs {opponent_team}"
        )

    lines = [f"查询到 {len(matches)} 场相关赛程：\n"]
    refs = []
    for i, item in enumerate(matches, start=1):
        lines.append(
            f"{i}. 第 {item.get('round')} 轮 vs {item.get('opponent_team')} | 日期：{item.get('match_date')} | 备注：{item.get('note', '')}"
        )
        refs.append(
            f"[{i}] schedule.json | round={item.get('round')} | 电子科技大学与四川大学联队 vs {item.get('opponent_team')}"
        )
    lines.append("\n参考来源：")
    lines.extend(refs)
    return "\n".join(lines)


def build_deck_answer(
    parsed: dict,
    top_decks_data: list[dict],
    card_deck_stats: dict[str, list[dict]] | None = None,
) -> str:
    rank_target = parsed.get("rank")
    top_n = parsed.get("top_n") or 10
    card_name = parsed.get("card_name")

    if card_name:
        card_deck_stats = card_deck_stats or {}
        variants = next(
            (
                decks
                for known_card, decks in card_deck_stats.items()
                if known_card.lower() == str(card_name).lower()
            ),
            [],
        )
        selected = variants[:top_n]
        if not selected:
            return (
                f"在当前 Supercell 官方 {next((item.get('sample_battles') for item in top_decks_data if item.get('sample_battles')), '未知')} 场快照中，"
                f"未观察到包含 **{card_name}** 的完整卡组。"
            )

        lines = [f"当前快照中包含 **{card_name}** 的高频完整卡组前 {len(selected)} 套如下：\n"]
        refs = []
        for index, deck in enumerate(selected, start=1):
            lines.append(
                f"{index}. **{deck.get('deck_name')}**\n"
                f"   样本场次：{deck.get('battles')} | 样本胜率：{deck.get('sample_win_rate')}%\n"
            )
            refs.append(
                f"[{index}] Supercell API live sample | {card_name} deck variant | sample_battles={deck.get('sample_battles')}"
            )
        lines.append("数据边界：按完整八卡组合聚合，统计来自同一份官方排行榜战斗日志快照。")
        lines.append("参考来源：")
        lines.extend(refs)
        return "\n".join(lines)

    sorted_decks = sorted(top_decks_data, key=lambda x: int(x.get("rank", 999999)))

    if rank_target is not None:
        selected = [d for d in sorted_decks if int(d.get("rank", 999999)) == rank_target]
        if not selected:
            return "我不知道，当前数据里没有这个排名的卡组。\n参考来源：无"

        d = selected[0]
        return (
            f"当前按热度看的第 {rank_target} 名卡组是 **{d.get('deck_name')}**。\n"
            f"- 排名：{d.get('rank')}\n"
            f"- 玩家：{d.get('player_name')}（{d.get('clan_name')}）\n"
            f"- 平均费用：{d.get('avg_elixir')}\n"
            f"- 近期战斗数：{d.get('battles')}\n"
            f"- 奖杯：{d.get('trophies')}\n"
            f"- 卡组构成：{', '.join(d.get('cards', []))}\n\n"
            f"参考来源：\n"
            f"[1] top_decks.json | rank={d.get('rank')} | {d.get('deck_name')} | source={d.get('source')}"
        )

    selected = sorted_decks[:top_n]

    if len(selected) == 1:
        d = selected[0]
        return (
            f"当前最热门的卡组之一是 **{d.get('deck_name')}**。\n"
            f"- 排名：{d.get('rank')}\n"
            f"- 玩家：{d.get('player_name')}（{d.get('clan_name')}）\n"
            f"- 平均费用：{d.get('avg_elixir')}\n"
            f"- 近期战斗数：{d.get('battles')}\n"
            f"- 奖杯：{d.get('trophies')}\n"
            f"- 卡组构成：{', '.join(d.get('cards', []))}\n\n"
            f"参考来源：\n"
            f"[1] top_decks.json | rank={d.get('rank')} | {d.get('deck_name')} | source={d.get('source')}"
        )

    lines = [f"当前热门卡组前 {len(selected)} 个如下：\n"]
    refs = []
    for i, d in enumerate(selected, start=1):
        lines.append(
            f"{i}. **{d.get('deck_name')}**\n"
            f"   排名：{d.get('rank')} | 玩家：{d.get('player_name')} | 平均费用：{d.get('avg_elixir')} | 奖杯：{d.get('trophies')}\n"
            f"   构成：{', '.join(d.get('cards', []))}\n"
        )
        refs.append(
            f"[{i}] top_decks.json | rank={d.get('rank')} | {d.get('deck_name')} | source={d.get('source')}"
        )

    lines.append("参考来源：")
    lines.extend(refs)
    return "\n".join(lines)


def build_single_card_answer(card: dict) -> str:
    return (
        f"**{card.get('card_name')}** 当前在 **{card.get('mode', '当前模式')}** 下的数据如下：\n"
        f"- 排名：{card.get('rank')}\n"
        f"- 评分：{card.get('rating')}\n"
        f"- 使用率：{card.get('usage_rate')}%\n"
        f"- 使用率变化：{card.get('usage_delta', 0)}%\n"
        f"- 胜率：{card.get('win_rate')}%\n"
        f"- 胜率变化：{card.get('win_delta', 0)}%\n"
        f"- 净胜率（CWR）：{card.get('clean_win_rate')}%\n\n"
        f"参考来源：\n"
        f"{build_card_source_reference(card)}"
    )


def build_named_card_metrics_answer(card: dict, metrics: list[str]) -> str:
    if card.get("source") == "Supercell API live sample":
        fetched_at = card.get("fetched_at") or "未知"
        labels = {
            "usage_rate": "\u4f7f\u7528\u7387",
            "win_rate": "\u80dc\u7387",
            "clean_win_rate": "\u51c0\u80dc\u7387\uff08CWR\uff09",
        }
        lines = [f"{card.get('card_name')} \u7684\u8bf7\u6c42\u6307\u6807\uff1a"]
        for metric in metrics:
            if metric in labels:
                lines.append(f"- {labels[metric]}\uff1a{card.get(metric)}%")
        if card.get("appearance_count") is not None:
            lines.append(f"- \u6837\u672c\u51fa\u573a\uff1a{card.get('appearance_count')} \u6b21")
        lines.extend(
            [
                "",
                "\u6570\u636e\u8fb9\u754c\uff1a\u4ee5\u4e0a\u4e3a Supercell \u5b98\u65b9\u5168\u7403\u6392\u884c\u699c\u73a9\u5bb6\u8fd1\u671f\u6218\u6597\u8bb0\u5f55\u7684\u6709\u9650\u6837\u672c"
                f"\uff08{card.get('sample_battles', 0)} \u573a\uff0c\u6293\u53d6\u4e8e {fetched_at}\uff09\uff0c\u5e76\u975e\u5168\u7403\u5b8c\u6574\u73af\u5883\u7edf\u8ba1\u3002",
                "\u53c2\u8003\u6765\u6e90\uff1a",
                f"[1] Supercell API live sample | {card.get('card_name')}",
            ]
        )
        return "\n".join(lines)

    labels = {
        "usage_rate": "使用率",
        "win_rate": "胜率",
        "clean_win_rate": "净胜率（CWR）",
    }
    lines = [f"{card.get('card_name')} 在 {card.get('mode', '当前模式')} 下的请求指标："]
    for metric in metrics:
        if metric in labels:
            lines.append(f"- {labels[metric]}：{card.get(metric)}%")
    lines.extend(
        [
            "",
            "数据边界：以上为仓库内的静态快照，不代表实时版本环境。",
            "参考来源：",
            build_card_source_reference(card),
        ]
    )
    return "\n".join(lines)


def build_card_ranking_answer(parsed: dict, cards_meta_data: list[dict]) -> str:
    metric = parsed.get("metric", "usage_rate")
    rank_target = parsed.get("rank")
    top_n = parsed.get("top_n") or 10

    ranking_cards = [card for card in cards_meta_data if not card.get("_fallback_only")]
    if not ranking_cards:
        ranking_cards = cards_meta_data
    sorted_cards = sorted(
        ranking_cards,
        key=lambda x: (-float(x.get(metric, 0)), int(x.get("rank", 999999))),
    )

    label_map = {
        "usage_rate": "使用率",
        "win_rate": "胜率",
        "clean_win_rate": "净胜率（CWR）",
    }
    field_label = label_map.get(metric, metric)

    if rank_target is not None:
        if not (1 <= rank_target <= len(sorted_cards)):
            return "我不知道，当前数据里没有这个排名的卡牌。\n参考来源：无"

        c = sorted_cards[rank_target - 1]
        return (
            f"当前按 **{field_label}** 排序的第 {rank_target} 名卡牌是 **{c.get('card_name')}**。\n"
            f"- 样本内排名：{c.get('rank')}\n"
            f"- 评分：{c.get('rating')}\n"
            f"- 使用率：{c.get('usage_rate')}%\n"
            f"- 胜率：{c.get('win_rate')}%\n"
            f"- 净胜率（CWR）：{c.get('clean_win_rate')}%\n\n"
            f"参考来源：\n"
            f"{build_card_source_reference(c)}"
        )

    selected = sorted_cards[:top_n]
    lines = [f"当前按 **{field_label}** 排序的前 {len(selected)} 张卡牌如下：\n"]
    refs = []

    for i, c in enumerate(selected, start=1):
        lines.append(
            f"{i}. **{c.get('card_name')}**\n"
            f"   样本内排名：{c.get('rank')} | 评分：{c.get('rating')} | 使用率：{c.get('usage_rate')}% | 胜率：{c.get('win_rate')}% | 净胜率：{c.get('clean_win_rate')}%\n"
        )
        refs.append(build_card_source_reference(c, i))

    lines.append("参考来源：")
    lines.extend(refs)
    return "\n".join(lines)


def build_card_answer(parsed: dict, cards_meta_data: list[dict]) -> str:
    card_name = parsed.get("card_name")
    if card_name and parsed.get("rank") is None and parsed.get("top_n") is None:
        target = None
        for item in cards_meta_data:
            if str(item.get("card_name", "")).lower() == card_name.lower():
                target = item
                break
        if target:
            metrics = parsed.get("metrics")
            if isinstance(metrics, list) and metrics:
                return build_named_card_metrics_answer(target, metrics)
            return build_single_card_answer(target)

        sample_card = next((item for item in cards_meta_data if item.get("source")), {})
        source = str(sample_card.get("source", ""))
        if source == "Supercell API live sample":
            sample_battles = sample_card.get("sample_battles")
            target_battles = sample_card.get("target_battles") or sample_battles
            observed = f"0/{sample_battles}" if sample_battles else "0/unknown"
            target_label = f"，目标 {target_battles} 场" if target_battles else ""
            return (
                f"当前 Supercell API 实时样本中 **{card_name}** 的观测为 {observed}{target_label}，因此无法计算其使用率或胜率。"
                "该样本是有限战斗日志，不代表全局环境。"
            )
        return f"当前卡牌快照中未找到 **{card_name}**，因此无法给出其使用率或胜率。"

    return build_card_ranking_answer(parsed, cards_meta_data)


def build_retrieval_query(parsed: dict, original_question: str) -> str:
    intent = parsed.get("intent")
    metric = parsed.get("metric")
    rank = parsed.get("rank")
    top_n = parsed.get("top_n")
    card_name = parsed.get("card_name")
    round_no = parsed.get("round")

    if intent == "schedule_query":
        target_date = parsed.get("date")
        if round_no is not None:
            return f"赛程 第{round_no}轮 对手 选手"
        if target_date:
            return f"赛程 {target_date} 对手 选手"
        return f"赛程 下一轮 对手 上场选手 {original_question}"

    if intent == "deck_query":
        if rank is not None:
            return f"热门卡组 排名 第{rank}"
        if top_n is not None:
            return f"热门卡组 前{top_n}"
        return f"热门卡组 热门 deck 排行 {original_question}"

    if intent == "card_query":
        if parsed.get("entity_mode") == "loadout_entity":
            state_labels = {"evolution": "觉醒", "elite": "精英", "tower": "塔楼", "ordinary": "普通"}
            state = state_labels.get(parsed.get("special_state"), "完整配置")
            entity_name = parsed.get("entity_name") or card_name or "卡牌"
            return f"{state}{entity_name} 完整配置 使用率 胜率 净胜率 评分 {original_question}"
        if card_name:
            return f"{card_name} 使用率 胜率 净胜率"
        if rank is not None:
            return f"卡牌 {metric} 排名 第{rank}"
        if top_n is not None:
            return f"卡牌 {metric} 前{top_n}"
        return f"卡牌 使用率 胜率 热门 {original_question}"

    return original_question
