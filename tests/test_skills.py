import json
import unittest
from pathlib import Path

from support import install_test_stubs

install_test_stubs()

from answer_builder import build_card_answer, build_deck_answer, build_schedule_answer
from skills.base import SkillContext
from skills.card_skill import CardMetaSkill
from skills.deck_skill import DeckRankingSkill
from skills.registry import SkillRegistry, build_default_registry
from skills.rag_skill import RAGEvidenceSkill
from skills.schedule_skill import ScheduleQuerySkill


DATA_DIR = Path("data")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class SkillImplementationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = load_json("schedule.json")
        cls.deck_data = load_json("top_decks.json")
        cls.card_data = load_json("cards_meta.json")

    def test_schedule_query_skill_matches_existing_builder(self):
        parsed = {"intent": "schedule_query", "round": 1, "date": None, "ask_players": False}
        skill = ScheduleQuerySkill()
        context = SkillContext(
            user_text="我们第一轮打谁",
            parsed=parsed,
            schedule_data=self.schedule_data,
            top_decks_data=self.deck_data,
            cards_meta_data=self.card_data,
        )

        answer = skill.run(context)

        self.assertEqual(answer, build_schedule_answer(parsed, self.schedule_data))

    def test_deck_ranking_skill_matches_existing_builder(self):
        parsed = {"intent": "deck_query", "rank": 2, "top_n": None}
        skill = DeckRankingSkill()
        context = SkillContext(
            user_text="热门卡组第二名",
            parsed=parsed,
            schedule_data=self.schedule_data,
            top_decks_data=self.deck_data,
            cards_meta_data=self.card_data,
        )

        answer = skill.run(context)

        self.assertEqual(answer, build_deck_answer(parsed, self.deck_data))

    def test_deck_ranking_skill_uses_card_filtered_snapshot_decks(self):
        parsed = {"intent": "deck_query", "card_name": "Electro Giant", "rank": None, "top_n": None}
        card_deck_stats = {
            "Electro Giant": [
                {
                    "deck_name": "Electro Giant / Lightning / Tornado",
                    "cards": ["Electro Giant", "Lightning", "Tornado"],
                    "battles": 42,
                    "sample_win_rate": 57.1,
                    "sample_battles": 20_000,
                    "source": "Supercell API live sample",
                }
            ]
        }
        skill = DeckRankingSkill()
        context = SkillContext(
            user_text="雷电巨人卡组有哪些",
            parsed=parsed,
            schedule_data=self.schedule_data,
            top_decks_data=self.deck_data,
            cards_meta_data=self.card_data,
            card_deck_stats=card_deck_stats,
        )

        answer = skill.run(context)

        self.assertTrue(skill.can_handle(parsed))
        self.assertIn("Electro Giant / Lightning / Tornado", answer)
        self.assertIn("42", answer)

    def test_card_meta_skill_matches_existing_builder(self):
        parsed = {"intent": "card_query", "card_name": "Fireball", "rank": None, "top_n": None, "metric": "win_rate"}
        skill = CardMetaSkill()
        context = SkillContext(
            user_text="火球胜率是多少",
            parsed=parsed,
            schedule_data=self.schedule_data,
            top_decks_data=self.deck_data,
            cards_meta_data=self.card_data,
        )

        answer = skill.run(context)

        self.assertEqual(answer, build_card_answer(parsed, self.card_data))

    def test_registry_resolves_direct_skills_before_rag_skill(self):
        registry = build_default_registry()

        self.assertIsInstance(
            registry.resolve({"intent": "schedule_query", "round": 3}),
            ScheduleQuerySkill,
        )
        self.assertIsInstance(
            registry.resolve({"intent": "deck_query", "rank": 1, "top_n": None}),
            DeckRankingSkill,
        )
        self.assertIsInstance(
            registry.resolve({"intent": "card_query", "card_name": "Fireball", "rank": None, "top_n": None}),
            CardMetaSkill,
        )
        self.assertIsNone(
            registry.resolve({"intent": "reject"}),
        )
        self.assertIsInstance(
            registry.resolve({"intent": "deck_query", "rank": None, "top_n": None}),
            RAGEvidenceSkill,
        )
        self.assertIsInstance(
            registry.resolve({"intent": "card_query", "card_name": None, "rank": None, "top_n": None}),
            RAGEvidenceSkill,
        )
