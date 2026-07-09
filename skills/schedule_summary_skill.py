from skills.base import DirectJSONSkill, SkillContext


class ScheduleSummarySkill(DirectJSONSkill):
    name = "ScheduleSummarySkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "schedule_summary_query"

    def run(self, context: SkillContext) -> str:
        upcoming = [
            item
            for item in context.schedule_data
            if str(item.get("status", "")).lower() == "upcoming"
        ]

        if not upcoming:
            return "我不知道，当前数据里没有后续赛程可供总结。\n参考来源：无"

        sorted_upcoming = sorted(
            upcoming,
            key=lambda item: (str(item.get("match_date", "")), int(item.get("round", 999999))),
        )
        remaining_count = len(sorted_upcoming)
        next_match = sorted_upcoming[0]
        start_date = sorted_upcoming[0].get("match_date")
        end_date = sorted_upcoming[-1].get("match_date")

        tbd_count = 0
        fully_tbd_rounds = 0
        for item in sorted_upcoming:
            player_name = str(item.get("player_name", "")).upper()
            opponent_player = str(item.get("opponent_player", "")).upper()
            if player_name == "TBD":
                tbd_count += 1
            if opponent_player == "TBD":
                tbd_count += 1
            if player_name == "TBD" and opponent_player == "TBD":
                fully_tbd_rounds += 1

        month_buckets = {}
        for item in sorted_upcoming:
            match_date = str(item.get("match_date", ""))
            month_key = match_date[:7]
            month_buckets[month_key] = month_buckets.get(month_key, 0) + 1

        busiest_month = max(month_buckets.items(), key=lambda item: (item[1], item[0]))
        busiest_month_label = busiest_month[0]
        busiest_month_count = busiest_month[1]

        pressure_text = "较均衡"
        if busiest_month_count >= 5:
            pressure_text = "较集中"
        elif busiest_month_count >= 3:
            pressure_text = "中等"

        return (
            f"接下来的赛程摘要如下：\n"
            f"- 剩余 upcoming 场次：{remaining_count} 场\n"
            f"- 最近一场比赛：第 {next_match.get('round')} 轮 vs {next_match.get('opponent_team')}，日期 {next_match.get('match_date')}\n"
            f"- 日期范围：{start_date} 到 {end_date}\n"
            f"- TBD 选手情况：共有 {tbd_count} 个选手槽位仍是 TBD，其中 {fully_tbd_rounds} 场双方选手都未确定\n"
            f"- 赛程压力判断：{busiest_month_label} 共有 {busiest_month_count} 场，整体压力 {pressure_text}\n\n"
            f"参考来源：\n"
            f"[1] schedule.json | upcoming_count={remaining_count} | range={start_date}~{end_date}"
        )
