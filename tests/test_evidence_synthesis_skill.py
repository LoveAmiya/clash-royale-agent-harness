import json
import tempfile
import unittest
from pathlib import Path

from support import install_test_stubs

install_test_stubs()

from skills.base import SkillContext
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill
from skills.meta_evidence import build_meta_evidence_pack
from skills.registry import SkillRegistry
from harness.executor import SkillExecutor
from harness.trace import TraceRecorder


DATA_DIR = Path("data")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class MetaEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = load_json("schedule.json")
        cls.deck_data = load_json("top_decks.json")
        cls.card_data = load_json("cards_meta.json")

    def test_evidence_pack_contains_local_facts_and_source_urls(self):
        evidence, sources = build_meta_evidence_pack(
            self.schedule_data,
            self.deck_data,
            self.card_data,
        )

        self.assertIn("Electro Dragon Evolution / Monk", evidence)
        self.assertIn("Fireball", evidence)
        self.assertIn("静态快照", evidence)
        self.assertIn("https://royaleapi.com/decks/leaderboard", sources)
        self.assertIn("https://royaleapi.com/cards/popular", sources)


class EvidenceSynthesisSkillTests(unittest.IsolatedAsyncioTestCase):
    def build_context(self, intent: str, api_key: str = "test-key") -> SkillContext:
        return SkillContext(
            user_text="根据当前热门卡组制定战队赛备战策略",
            parsed={"intent": intent},
            schedule_data=[{"round": 1, "status": "upcoming"}],
            top_decks_data=[{"rank": 1, "deck_name": "Deck A"}],
            cards_meta_data=[{"rank": 1, "card_name": "Card A", "usage_rate": 10}],
            api_key=api_key,
        )

    async def test_routes_meta_analysis_to_configured_builder(self):
        calls = []

        async def builder(**kwargs):
            calls.append(kwargs)
            return "模型综合结论"

        skill = EvidenceSynthesisSkill(answer_builder=builder)
        answer = await skill.run(self.build_context("meta_analysis_query"))

        self.assertEqual(answer, "模型综合结论")
        self.assertEqual(calls[0]["user_text"], "根据当前热门卡组制定战队赛备战策略")
        self.assertEqual(calls[0]["api_key"], "test-key")

    async def test_requires_api_key_instead_of_using_old_template(self):
        skill = EvidenceSynthesisSkill(answer_builder=lambda **kwargs: "should not run")
        answer = await skill.run(self.build_context("match_preparation_query", api_key=""))

        self.assertIn("OPENAI_API_KEY", answer)

    async def test_trace_marks_evidence_synthesis_mode(self):
        async def builder(**kwargs):
            return "模型综合结论"

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "traces.jsonl"
            executor = SkillExecutor(
                SkillRegistry([EvidenceSynthesisSkill(answer_builder=builder)]),
                recorder=TraceRecorder(log_path=log_path),
            )

            answer = await executor.execute(self.build_context("match_preparation_query"))

            self.assertEqual(answer, "模型综合结论")
            last_event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last_event["mode"], "evidence_synthesis")
