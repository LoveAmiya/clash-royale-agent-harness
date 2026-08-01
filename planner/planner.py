from planner.plan_schema import Plan, PlanStep
from skills.base import SkillContext


class RuleBasedPlanner:
    def build_plan(self, context: SkillContext) -> Plan | None:
        intent = context.parsed.get("intent")
        if intent != "meta_analysis_query":
            return None

        return Plan(
            plan_type="evidence_synthesis",
            trigger_intent=intent,
            steps=[
                PlanStep(
                    step_id="step_1",
                    skill_name="DeckRankingSkill",
                    description="读取热门卡组 Top 5",
                ),
                PlanStep(
                    step_id="step_2",
                    skill_name="CardMetaSkill",
                    description="读取重点单卡 meta，例如使用率前 10",
                ),
                PlanStep(
                    step_id="step_3",
                    skill_name="EvidenceSynthesisSkill",
                    description="基于可追溯快照证据调用模型生成环境分析",
                ),
            ],
        )
