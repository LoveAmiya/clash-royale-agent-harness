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


def build_deck_answer(parsed: dict, top_decks_data: list[dict]) -> str:
    rank_target = parsed.get("rank")
    top_n = parsed.get("top_n") or 10

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
        f"[1] cards_meta.json | rank={card.get('rank')} | {card.get('card_name')} | source={card.get('source')}"
    )


def build_card_ranking_answer(parsed: dict, cards_meta_data: list[dict]) -> str:
    metric = parsed.get("metric", "usage_rate")
    rank_target = parsed.get("rank")
    top_n = parsed.get("top_n") or 10

    sorted_cards = sorted(
        cards_meta_data,
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
            f"- 全局排名：{c.get('rank')}\n"
            f"- 评分：{c.get('rating')}\n"
            f"- 使用率：{c.get('usage_rate')}%\n"
            f"- 胜率：{c.get('win_rate')}%\n"
            f"- 净胜率（CWR）：{c.get('clean_win_rate')}%\n\n"
            f"参考来源：\n"
            f"[1] cards_meta.json | rank={c.get('rank')} | {c.get('card_name')} | source={c.get('source')}"
        )

    selected = sorted_cards[:top_n]
    lines = [f"当前按 **{field_label}** 排序的前 {len(selected)} 张卡牌如下：\n"]
    refs = []

    for i, c in enumerate(selected, start=1):
        lines.append(
            f"{i}. **{c.get('card_name')}**\n"
            f"   全局排名：{c.get('rank')} | 评分：{c.get('rating')} | 使用率：{c.get('usage_rate')}% | 胜率：{c.get('win_rate')}% | 净胜率：{c.get('clean_win_rate')}%\n"
        )
        refs.append(
            f"[{i}] cards_meta.json | rank={c.get('rank')} | {c.get('card_name')} | source={c.get('source')}"
        )

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
            return build_single_card_answer(target)

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
        if card_name:
            return f"{card_name} 使用率 胜率 净胜率"
        if rank is not None:
            return f"卡牌 {metric} 排名 第{rank}"
        if top_n is not None:
            return f"卡牌 {metric} 前{top_n}"
        return f"卡牌 使用率 胜率 热门 {original_question}"

    return original_question
