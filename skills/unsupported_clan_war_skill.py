"""Explicit boundary for clan-war capabilities removed from the product."""

from skills.base import DirectJSONSkill, SkillContext


UNSUPPORTED_CLAN_WAR_FEATURE = "UNSUPPORTED_CLAN_WAR_FEATURE"
REMOVED_CLAN_WAR_INTENTS = frozenset(
    {"schedule_query", "schedule_summary_query", "match_preparation_query"}
)


class UnsupportedClanWarSkill(DirectJSONSkill):
    name = "UnsupportedClanWarSkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") in REMOVED_CLAN_WAR_INTENTS

    def run(self, context: SkillContext) -> str:
        if context.metadata is not None:
            context.metadata["error_code"] = UNSUPPORTED_CLAN_WAR_FEATURE
            context.metadata["removed_intent"] = context.parsed.get("intent")
        return (
            "战队赛赛程查询和战队赛备战建议已从本项目移除。"
            "你仍可使用单卡数据、卡牌比较、卡组数据、对局优劣和环境分析功能。"
        )
