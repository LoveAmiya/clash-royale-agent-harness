import unittest

from support import install_test_stubs, sample_cards, sample_schedule

install_test_stubs()

from query_parser import apply_selected_entity_mode, fallback_parse_query
from skills.base import SkillContext
from skills.card_compare_skill import CardCompareSkill
from skills.card_rank_lookup_skill import CardRankLookupSkill
from skills.card_skill import CardMetaSkill
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill
from skills.loadout_entity_skill import LoadoutEntitySkill
from skills.structured_relationship_skill import StructuredRelationshipSkill
from skills.rag_skill import RAGEvidenceSkill
from skills.registry import build_default_registry
from skills.schedule_summary_skill import ScheduleSummarySkill
from skills.unsupported_clan_war_skill import UnsupportedClanWarSkill


class BusinessSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card_data = sample_cards()
        cls.schedule_data = sample_schedule()

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

    def test_explicit_card_form_routes_to_structured_entity_skill(self):
        registry = build_default_registry()
        parsed = fallback_parse_query("觉醒骑士的使用率是多少", self.card_data)

        selected_skill = registry.resolve(parsed)

        self.assertEqual(parsed["entity_mode"], "loadout_entity")
        self.assertIsInstance(selected_skill, LoadoutEntitySkill)

    def test_structured_entity_skill_returns_requested_metric_without_rag(self):
        class FakeRepository:
            def entity_stats_by_reference(self, entity_type, entity_name, special_state):
                self.reference = (entity_type, entity_name, special_state)
                return {
                    "entity": {
                        "display_name_zh": "觉醒骑士",
                        "usage_rate": 8.5,
                        "clean_win_rate": 51.25,
                        "net_win_rate": 2.5,
                        "rating": 0.73,
                        "appearances": 1234,
                    },
                    "matched_sample_count": 1234,
                    "provenance": {
                        "source": "Supercell API rolling Path of Legend corpus",
                        "dataset_scope": "7d_all",
                        "unique_battles": 937843,
                    },
                }

        parsed = fallback_parse_query("觉醒骑士的使用率是多少", self.card_data)
        repository = FakeRepository()
        context = self.build_context(parsed)
        context.structured_repository = repository

        answer = LoadoutEntitySkill().run(context)

        self.assertEqual(repository.reference, ("card", "Knight", "evolution"))
        self.assertIn("觉醒骑士", answer)
        self.assertIn("使用率：8.5%", answer)
        self.assertNotIn("胜率：", answer)
        self.assertIn("1234 次", answer)
        self.assertIn("Supercell API rolling Path of Legend corpus", answer)

    def test_selected_full_configuration_promotes_bare_card_to_ordinary_entity(self):
        parsed = fallback_parse_query("巨人的使用率是多少？", self.card_data)

        promoted = apply_selected_entity_mode(parsed, "loadout_entity")

        self.assertEqual(promoted["entity_mode"], "loadout_entity")
        self.assertEqual(promoted["entity_type"], "card")
        self.assertEqual(promoted["entity_name"], "Giant")
        self.assertEqual(promoted["special_state"], "ordinary")
        self.assertIsInstance(build_default_registry().resolve(promoted), LoadoutEntitySkill)

    def test_selected_base8_keeps_bare_card_in_base8(self):
        parsed = fallback_parse_query("巨人的使用率是多少？", self.card_data)

        unchanged = apply_selected_entity_mode(parsed, "base8")

        self.assertEqual(unchanged["entity_mode"], "base8")
        self.assertIsInstance(build_default_registry().resolve(unchanged), CardMetaSkill)

    def test_card_pair_cooccurrence_routes_to_structured_relationship_skill(self):
        parsed = fallback_parse_query("火球和野猪骑士共同出现了多少次？", self.card_data)

        self.assertEqual(parsed["intent"], "card_cooccurrence_query")
        self.assertEqual(parsed["card_names"], ["Fireball", "Hog Rider"])
        self.assertIsInstance(build_default_registry().resolve(parsed), StructuredRelationshipSkill)

    def test_common_teammates_routes_to_structured_relationship_skill(self):
        parsed = fallback_parse_query("哪些卡最常和巨人一起使用？", self.card_data)

        self.assertEqual(parsed["intent"], "card_cooccurrence_query")
        self.assertEqual(parsed["card_name"], "Giant")
        self.assertEqual(parsed["top_n"], 10)
        self.assertIsInstance(build_default_registry().resolve(parsed), StructuredRelationshipSkill)

    def test_exact_eight_card_deck_query_preserves_all_cards(self):
        parsed = fallback_parse_query(
            "野猪骑士、火枪手、火球、加农炮、戈仑冰人、冰雪精灵、骷髅兵、复仇滚木这套卡组有多少场？",
            self.card_data,
        )

        self.assertEqual(parsed["intent"], "deck_query")
        self.assertEqual(len(parsed["deck_cards"]), 8)
        self.assertIsNone(parsed["card_name"])
        self.assertIsNone(parsed["top_n"])
        self.assertEqual(
            set(parsed["deck_cards"]),
            {
                "Hog Rider", "Musketeer", "Fireball", "Cannon",
                "Ice Golem", "Ice Spirit", "Skeletons", "The Log",
            },
        )

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

        self.assertIsInstance(selected_skill, UnsupportedClanWarSkill)

    def test_summary_of_environment_is_not_parsed_as_schedule_summary_query(self):
        parsed = fallback_parse_query("总结一下环境", self.card_data)

        self.assertNotEqual(parsed["intent"], "schedule_summary_query")

    def test_match_preparation_query_parses_intent(self):
        parsed = fallback_parse_query("下一轮怎么准备？", self.card_data)

        self.assertEqual(parsed["intent"], "match_preparation_query")

    def test_match_preparation_resolves_to_removed_feature_boundary(self):
        registry = build_default_registry()
        parsed = fallback_parse_query("帮我推荐几套可练的卡组", self.card_data)

        selected_skill = registry.resolve(parsed)

        self.assertIsInstance(selected_skill, UnsupportedClanWarSkill)
        context = self.build_context(parsed)
        context.metadata = {}
        answer = selected_skill.run(context)
        self.assertEqual(context.metadata["error_code"], "UNSUPPORTED_CLAN_WAR_FEATURE")
        self.assertIn("已从本项目移除", answer)
