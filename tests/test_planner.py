import json
import unittest
from pathlib import Path

from support import install_test_stubs

install_test_stubs()

from planner.plan_schema import Plan, PlanStep
from planner.planner import RuleBasedPlanner
from skills.base import SkillContext


DATA_DIR = Path("data")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = load_json("schedule.json")
        cls.deck_data = load_json("top_decks.json")
        cls.card_data = load_json("cards_meta.json")

    def build_context(self, parsed: dict) -> SkillContext:
        return SkillContext(
            user_text="test",
            parsed=parsed,
            schedule_data=self.schedule_data,
            top_decks_data=self.deck_data,
            cards_meta_data=self.card_data,
            metadata={},
        )

    def test_match_preparation_query_does_not_generate_plan(self):
        planner = RuleBasedPlanner()
        context = self.build_context({"intent": "match_preparation_query"})

        self.assertIsNone(planner.build_plan(context))

    def test_meta_analysis_plan_has_three_steps(self):
        planner = RuleBasedPlanner()
        context = self.build_context({"intent": "meta_analysis_query"})

        plan = planner.build_plan(context)

        self.assertEqual(len(plan.steps), 3)

    def test_meta_analysis_plan_contains_expected_skills(self):
        planner = RuleBasedPlanner()
        context = self.build_context({"intent": "meta_analysis_query"})

        plan = planner.build_plan(context)
        skill_names = [step.skill_name for step in plan.steps]

        self.assertEqual(
            skill_names,
            ["DeckRankingSkill", "CardMetaSkill", "EvidenceSynthesisSkill"],
        )

    def test_card_query_does_not_generate_plan(self):
        planner = RuleBasedPlanner()
        context = self.build_context({"intent": "card_query"})

        self.assertIsNone(planner.build_plan(context))

    def test_schedule_query_does_not_generate_plan(self):
        planner = RuleBasedPlanner()
        context = self.build_context({"intent": "schedule_query"})

        self.assertIsNone(planner.build_plan(context))

    def test_plan_is_json_friendly(self):
        plan = Plan(
            plan_type="rule_based_match_preparation",
            trigger_intent="match_preparation_query",
            steps=[
                PlanStep("step_1", "ScheduleQuerySkill", "查找下一轮 upcoming 比赛"),
                PlanStep("step_2", "DeckRankingSkill", "读取热门卡组 Top 5"),
            ],
        )

        plan_dict = plan.to_dict()

        self.assertEqual(plan_dict["trigger_intent"], "match_preparation_query")
        self.assertEqual(len(plan_dict["steps"]), 2)
        self.assertEqual(plan_dict["steps"][0]["skill_name"], "ScheduleQuerySkill")
