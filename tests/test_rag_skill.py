import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import install_test_stubs

install_test_stubs()

import query_answering
from harness.executor import SkillExecutor
from harness.trace import TraceRecorder
from query_answering import answer_query
from skills.base import SkillContext
from skills.rag_skill import RAGEvidenceSkill
from skills.registry import SkillRegistry


DATA_DIR = Path("data")


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class RAGEvidenceSkillTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = load_json("schedule.json")
        cls.deck_data = load_json("top_decks.json")
        cls.card_data = load_json("cards_meta.json")

    def build_context(
        self,
        *,
        user_text: str,
        parsed: dict,
        retriever=None,
        api_key: str = "",
    ) -> SkillContext:
        return SkillContext(
            user_text=user_text,
            parsed=parsed,
            schedule_data=self.schedule_data,
            top_decks_data=self.deck_data,
            cards_meta_data=self.card_data,
            retriever=retriever,
            api_key=api_key,
        )

    def test_can_handle_open_ended_deck_and_card_queries(self):
        skill = RAGEvidenceSkill()

        self.assertTrue(skill.can_handle({"intent": "deck_query", "rank": None, "top_n": None}))
        self.assertTrue(
            skill.can_handle(
                {"intent": "card_query", "card_name": None, "rank": None, "top_n": None}
            )
        )
        self.assertFalse(skill.can_handle({"intent": "deck_query", "rank": 1, "top_n": None}))
        self.assertFalse(
            skill.can_handle(
                {"intent": "card_query", "card_name": "Fireball", "rank": None, "top_n": None}
            )
        )

    async def test_returns_original_missing_retriever_message_for_open_ended_deck_query(self):
        skill = RAGEvidenceSkill()
        context = self.build_context(
            user_text="现在热门卡组怎么看",
            parsed={"intent": "deck_query", "rank": None, "top_n": None},
            retriever=None,
            api_key="test-key",
        )

        answer = await skill.run(context)

        self.assertEqual(
            answer,
            "当前无法使用检索回答卡组开放问题，请先启动 Ollama embedding 服务后重试。",
        )

    async def test_returns_original_missing_api_key_message_for_open_ended_card_query(self):
        skill = RAGEvidenceSkill()
        context = self.build_context(
            user_text="最近热门卡牌环境怎么样",
            parsed={"intent": "card_query", "card_name": None, "rank": None, "top_n": None},
            retriever=object(),
            api_key="",
        )

        answer = await skill.run(context)

        self.assertEqual(
            answer,
            "当前无法使用检索回答卡牌开放问题，请先设置 OPENAI_API_KEY 后重试。",
        )

    async def test_reuses_existing_rag_callbacks_with_context_retriever_and_api_key(self):
        rag_answer = AsyncMock(return_value="rag:deck")
        reviewer_model_builder = lambda api_key: {"api_key": api_key}
        skill = RAGEvidenceSkill(
            rag_answer_builder=rag_answer,
            reviewer_model_builder=reviewer_model_builder,
        )
        retriever = object()
        context = self.build_context(
            user_text="现在热门卡组怎么看",
            parsed={"intent": "deck_query", "rank": None, "top_n": None},
            retriever=retriever,
            api_key="test-key",
        )

        answer = await skill.run(context)

        self.assertEqual(answer, "rag:deck")
        self.assertEqual(rag_answer.await_count, 1)
        self.assertEqual(rag_answer.await_args.kwargs["user_text"], "现在热门卡组怎么看")
        self.assertIs(rag_answer.await_args.kwargs["retriever"], retriever)
        self.assertEqual(rag_answer.await_args.kwargs["source_type"], "deck")
        self.assertEqual(rag_answer.await_args.kwargs["reviewer_model"], {"api_key": "test-key"})
        self.assertEqual(rag_answer.await_args.kwargs["api_key"], "test-key")

    async def test_model_sdk_failure_returns_grounded_fallback_instead_of_raising(self):
        class StaticRetriever:
            def hybrid_search(self, *args, **kwargs):
                return [
                    {
                        "final_score": 1.0,
                        "doc": {
                            "doc_id": "deck_1",
                            "source_type": "deck",
                            "text": "测试卡组证据",
                            "metadata": {
                                "rank": 1,
                                "deck_name": "Test Deck",
                                "player_name": "Tester",
                                "avg_elixir": 3.2,
                                "trophies": 9000,
                                "cards": ["Knight"],
                                "source": "test fixture",
                            },
                        },
                    }
                ]

        with patch.object(
            query_answering,
            "generate_model_text",
            AsyncMock(side_effect=RuntimeError("Request timed out")),
        ), patch.object(query_answering, "uses_responses_api", return_value=True):
            answer = await query_answering.build_rag_answer(
                user_text="当前环境以什么卡组为主？",
                parsed={"intent": "deck_query"},
                retriever=StaticRetriever(),
                source_type="deck",
                reviewer_model=object(),
                api_key="test-key",
            )

        self.assertIn("模型调用失败", answer)
        self.assertIn("[1] deck | deck_1", answer)
        self.assertNotIn("Request timed out", answer)


class StubSkill:
    name = "StubSkill"

    def __init__(self):
        self.seen = []

    def can_handle(self, parsed: dict) -> bool:
        return True

    def run(self, context: SkillContext) -> str:
        self.seen.append(context)
        return f"stub:{context.parsed['intent']}"


class AnswerQueryRAGRoutingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule_data = load_json("schedule.json")
        cls.deck_data = load_json("top_decks.json")
        cls.card_data = load_json("cards_meta.json")

    async def test_answer_query_uses_skill_registry_for_direct_queries(self):
        stub_skill = StubSkill()
        registry = SkillRegistry([stub_skill])
        executor = SkillExecutor(registry, recorder=TraceRecorder(log_path=Path(self.deck_data.__class__.__name__ + "_unused.jsonl")))
        parsed = {"intent": "schedule_query", "round": 1, "date": None, "ask_players": False}

        with patch.object(query_answering, "SKILL_EXECUTOR", executor):
            answer = await answer_query(
                user_text="我们第一轮打谁",
                parsed=parsed,
                schedule_data=self.schedule_data,
                top_decks_data=self.deck_data,
                cards_meta_data=self.card_data,
                retriever=None,
                api_key="",
            )

        self.assertEqual(answer, "stub:schedule_query")
        self.assertEqual(len(stub_skill.seen), 1)
        self.assertEqual(stub_skill.seen[0].parsed, parsed)

    async def test_answer_query_routes_open_ended_deck_query_through_rag_skill(self):
        rag_answer = AsyncMock(return_value="rag:deck")
        rag_skill = RAGEvidenceSkill(
            rag_answer_builder=rag_answer,
            reviewer_model_builder=lambda api_key: {"api_key": api_key},
        )
        registry = SkillRegistry([rag_skill])
        executor = SkillExecutor(registry, recorder=TraceRecorder(log_path=Path("deck_trace_unused.jsonl")))
        retriever = object()
        parsed = {"intent": "deck_query", "rank": None, "top_n": None}

        with patch.object(query_answering, "SKILL_EXECUTOR", executor):
            answer = await answer_query(
                user_text="现在热门卡组怎么看",
                parsed=parsed,
                schedule_data=self.schedule_data,
                top_decks_data=self.deck_data,
                cards_meta_data=self.card_data,
                retriever=retriever,
                api_key="test-key",
            )

        self.assertEqual(answer, "rag:deck")
        self.assertEqual(rag_answer.await_count, 1)
        self.assertIs(rag_answer.await_args.kwargs["retriever"], retriever)
        self.assertEqual(rag_answer.await_args.kwargs["source_type"], "deck")

    async def test_answer_query_routes_open_ended_card_query_through_rag_skill(self):
        rag_answer = AsyncMock(return_value="rag:card")
        rag_skill = RAGEvidenceSkill(
            rag_answer_builder=rag_answer,
            reviewer_model_builder=lambda api_key: {"api_key": api_key},
        )
        registry = SkillRegistry([rag_skill])
        executor = SkillExecutor(registry, recorder=TraceRecorder(log_path=Path("card_trace_unused.jsonl")))
        retriever = object()
        parsed = {"intent": "card_query", "card_name": None, "rank": None, "top_n": None}

        with patch.object(query_answering, "SKILL_EXECUTOR", executor):
            answer = await answer_query(
                user_text="最近热门卡牌环境怎么样",
                parsed=parsed,
                schedule_data=self.schedule_data,
                top_decks_data=self.deck_data,
                cards_meta_data=self.card_data,
                retriever=retriever,
                api_key="test-key",
            )

        self.assertEqual(answer, "rag:card")
        self.assertEqual(rag_answer.await_count, 1)
        self.assertIs(rag_answer.await_args.kwargs["retriever"], retriever)
        self.assertEqual(rag_answer.await_args.kwargs["source_type"], "card")
