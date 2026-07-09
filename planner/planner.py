from planner.plan_schema import Plan, PlanStep
from skills.base import SkillContext


class RuleBasedPlanner:
    def build_plan(self, context: SkillContext) -> Plan | None:
        intent = context.parsed.get("intent")
        if intent != "match_preparation_query":
            return None

        return Plan(
            plan_type="rule_based_match_preparation",
            trigger_intent=intent,
            steps=[
                PlanStep(
                    step_id="step_1",
                    skill_name="ScheduleQuerySkill",
                    description="查找下一轮 upcoming 比赛",
                ),
                PlanStep(
                    step_id="step_2",
                    skill_name="DeckRankingSkill",
                    description="读取热门卡组 Top 5",
                ),
                PlanStep(
                    step_id="step_3",
                    skill_name="CardMetaSkill",
                    description="读取重点单卡 meta，例如使用率前 10",
                ),
                PlanStep(
                    step_id="step_4",
                    skill_name="MatchPreparationSkill",
                    description="综合赛程、热门卡组和单卡 meta 生成备战建议",
                ),
            ],
        )
