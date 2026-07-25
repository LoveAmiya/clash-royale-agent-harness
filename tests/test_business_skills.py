import json
import unittest
from pathlib import Path

from support import install_test_stubs

install_test_stubs()

from query_parser import fallback_parse_query
from skills.base import SkillContext
from skills.card_compare_skill import CardCompareSkill
from skills.card_rank_lookup_skill import CardRankLookupSkill
from skills.card_skill import CardMetaSkill
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill
from skills.registry import build_default_registry
from skills.schedule_summary_skill import ScheduleSummarySkill


DATA_DIR = Path("data")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class BusinessSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card_data = load_json("cards_meta.json")
        cls.schedule_data = load_json("schedule.json")

    def build_context(self, parsed: dict) -> SkillContext:
        return SkillContext(
            user_text="test",
            parsed=parsed,
            schedule_data=self.schedule_data,
            top_decks_data=[],
            cards_meta_data=self.card_data,
        )

    def test_two_cards_are_parsed_as_card_names(self):
        parsed = fallback_parse_query("火球和毒药哪个胜率高", self.card_data)

        self.assertEqual(parsed["intent"], "card_compare_query")
        self.assertEqual(parsed["card_names"], ["Fireball", "Poison"])

    def test_compare_metric_is_win_rate(self):
        parsed = fallback_parse_query("火球和毒药哪个胜率高", self.card_data)

        self.assertEqual(parsed["compare_metric"], "win_rate")

    def test_card_compare_skill_can_handle_compare_query(self):
        parsed = fallback_parse_query("火球和毒药哪个胜率高", self.card_data)
        skill = CardCompareSkill()

        self.assertTrue(skill.can_handle(parsed))

    def test_card_compare_skill_returns_cards_and_difference(self):
        parsed = fallback_parse_query("火球和毒药哪个胜率高", self.card_data)
        skill = CardCompareSkill()

        answer = skill.run(self.build_context(parsed))

        self.assertIn("Fireball", answer)
        self.assertIn("Poison", answer)
        self.assertIn("差值", answer)
        self.assertIn("胜率", answer)

    def test_fireball_and_poison_which_has_higher_win_rate(self):
        parsed = fallback_parse_query("火球和毒药哪个胜率高", self.card_data)
        skill = CardCompareSkill()

        answer = skill.run(self.build_context(parsed))

        self.assertTrue("Fireball" in answer or "Poison" in answer)
        self.assertIn("更高", answer)

    def test_single_card_query_still_resolves_to_card_meta_skill(self):
        registry = build_default_registry()
        parsed = fallback_parse_query("火球的胜率是多少", self.card_data)

        selected_skill = registry.resolve(parsed)

        self.assertEqual(parsed["intent"], "card_query")
        self.assertIsInstance(selected_skill, CardMetaSkill)

    def test_card_compare_skill_returns_controlled_failure_for_single_recognized_card(self):
        parsed = {
            "intent": "card_compare_query",
            "card_names": ["Fireball"],
            "compare_metric": "win_rate",
        }
        skill = CardCompareSkill()

        answer = skill.run(self.build_context(parsed))

        self.assertIn("至少需要两张", answer)

    def test_card_rank_lookup_query_parses_fireball_win_rate_rank(self):
        parsed = fallback_parse_query("火球在胜率榜排第几？", self.card_data)

        self.assertEqual(parsed["intent"], "card_rank_lookup_query")
        self.assertEqual(parsed["card_name"], "Fireball")
        self.assertEqual(parsed["metric"], "win_rate")

    def test_card_rank_lookup_query_parses_log_usage_rate_rank(self):
        parsed = fallback_parse_query("滚木使用率排名多少？", self.card_data)

        self.assertEqual(parsed["intent"], "card_rank_lookup_query")
        self.assertEqual(parsed["card_name"], "The Log")
        self.assertEqual(parsed["metric"], "usage_rate")

    def test_card_rank_lookup_query_parses_poison_clean_win_rate_rank(self):
        parsed = fallback_parse_query("毒药净胜率排第几？", self.card_data)

        self.assertEqual(parsed["intent"], "card_rank_lookup_query")
        self.assertEqual(parsed["card_name"], "Poison")
        self.assertEqual(parsed["metric"], "clean_win_rate")

    def test_card_rank_lookup_skill_can_handle_rank_lookup_query(self):
        parsed = fallback_parse_query("火球在胜率榜排第几？", self.card_data)
        skill = CardRankLookupSkill()

        self.assertTrue(skill.can_handle(parsed))

    def test_card_rank_lookup_skill_returns_metric_rank(self):
        parsed = fallback_parse_query("火球在胜率榜排第几？", self.card_data)
        skill = CardRankLookupSkill()

        answer = skill.run(self.build_context(parsed))

        self.assertIn("Fireball", answer)
        self.assertIn("胜率", answer)
        self.assertIn("第 **", answer)

    def test_rank_lookup_resolves_to_card_rank_lookup_skill(self):
        registry = build_default_registry()
        parsed = fallback_parse_query("火球在胜率榜排第几？", self.card_data)

        selected_skill = registry.resolve(parsed)

        self.assertIsInstance(selected_skill, CardRankLookupSkill)

    def test_schedule_summary_query_parses_summary_intent(self):
        parsed = fallback_parse_query("总结一下接下来的赛程", self.card_data)

        self.assertEqual(parsed["intent"], "schedule_summary_query")

    def test_schedule_summary_skill_can_handle_summary_query(self):
        parsed = fallback_parse_query("后面还有几场比赛？", self.card_data)
        skill = ScheduleSummarySkill()

        self.assertTrue(skill.can_handle(parsed))

    def test_schedule_summary_skill_returns_required_summary_fields(self):
        parsed = fallback_parse_query("这个月赛程压力怎么样？", self.card_data)
        skill = ScheduleSummarySkill()

        answer = skill.run(self.build_context(parsed))

        self.assertIn("剩余 upcoming 场次", answer)
        self.assertIn("11 场", answer)
        self.assertIn("最近一场比赛", answer)
        self.assertIn("日期范围", answer)
        self.assertIn("TBD", answer)
        self.assertIn("schedule.json", answer)

    def test_schedule_summary_resolves_to_schedule_summary_skill(self):
        registry = build_default_registry()
        parsed = fallback_parse_query("总结一下接下来的赛程", self.card_data)

        selected_skill = registry.resolve(parsed)

        self.assertIsInstance(selected_skill, ScheduleSummarySkill)

    def test_summary_of_environment_is_not_parsed_as_schedule_summary_query(self):
        parsed = fallback_parse_query("总结一下环境", self.card_data)

        self.assertNotEqual(parsed["intent"], "schedule_summary_query")

    def test_match_preparation_query_parses_intent(self):
        parsed = fallback_parse_query("下一轮怎么准备？", self.card_data)

        self.assertEqual(parsed["intent"], "match_preparation_query")

    def test_match_preparation_resolves_to_evidence_synthesis_skill(self):
        registry = build_default_registry()
        parsed = fallback_parse_query("帮我推荐几套可练的卡组", self.card_data)

        selected_skill = registry.resolve(parsed)

        self.assertIsInstance(selected_skill, EvidenceSynthesisSkill)
