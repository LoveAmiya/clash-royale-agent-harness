import unittest
from unittest.mock import patch

from support import install_test_stubs

install_test_stubs()

import query_answering
from clashroyale_agent.qa.structured_answering import (
    StructuredAnswerDependencies,
    answer_structured_query,
)


class FakePlan:
    def to_dict(self):
        return {"plan_type": "fake_direct"}


class FakePlanner:
    def build_plan(self, context):
        self.context = context
        return FakePlan()


class FakeExecutor:
    async def execute(self, context):
        self.context = context
        context.metadata.setdefault("trace_id", "trace-structured")
        context.metadata["selected_skill"] = "FakeDirectSkill"
        context.metadata["mode"] = "direct"
        context.metadata["executor_seen_stream"] = context.stream_content
        return "direct answer"


class EmptyExecutor:
    async def execute(self, context):
        self.context = context
        return None


class StructuredAnsweringTests(unittest.IsolatedAsyncioTestCase):
    async def test_packaged_structured_answer_matches_root_single_intent_contract(self):
        parsed = {"intent": "card_query", "card_name": "Fireball"}
        runtime_metadata = {"dataset_scope": "official_top_1000"}
        package_executor = FakeExecutor()
        package_planner = FakePlanner()

        packaged = await answer_structured_query(
            user_text="card stats",
            parsed=parsed,
            schedule_data=[],
            top_decks_data=[],
            cards_meta_data=[],
            retriever=None,
            api_key="",
            runtime_metadata=runtime_metadata,
            card_deck_stats={"Fireball": [{"usage_rate": 1.0}]},
            structured_repository=object(),
            event_sink=object(),
            stream_content=False,
            fallback_answer="fallback",
            dependencies=StructuredAnswerDependencies(
                skill_executor=package_executor,
                planner=package_planner,
            ),
        )

        root_executor = FakeExecutor()
        root_planner = FakePlanner()
        with patch.object(query_answering, "SKILL_EXECUTOR", root_executor), patch.object(
            query_answering, "RULE_BASED_PLANNER", root_planner
        ):
            root = await query_answering.answer_query(
                user_text="card stats",
                parsed=parsed,
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=None,
                api_key="",
                include_metadata=True,
                runtime_metadata=runtime_metadata,
                card_deck_stats={"Fireball": [{"usage_rate": 1.0}]},
                structured_repository=object(),
                event_sink=object(),
                stream_content=False,
            )

        self.assertEqual(root.answer, packaged.answer)
        self.assertEqual(root.trace_id, packaged.trace_id)
        self.assertEqual(root.plan, packaged.plan)
        self.assertEqual(root.selected_skill, packaged.selected_skill)
        self.assertEqual(root.mode, packaged.mode)
        self.assertEqual(root.metadata["dataset_scope"], "official_top_1000")
        self.assertFalse(root.metadata["executor_seen_stream"])
        self.assertFalse(package_executor.context.stream_content)
        self.assertEqual(package_executor.context.card_deck_stats["Fireball"][0]["usage_rate"], 1.0)

    async def test_packaged_structured_answer_keeps_fallback_contract(self):
        result = await answer_structured_query(
            user_text="unknown",
            parsed={"intent": "unknown_query"},
            schedule_data=[],
            top_decks_data=[],
            cards_meta_data=[],
            retriever=None,
            api_key="",
            fallback_answer="fallback answer",
            dependencies=StructuredAnswerDependencies(
                skill_executor=EmptyExecutor(),
                planner=FakePlanner(),
            ),
        )

        self.assertEqual(result.answer, "fallback answer")
        self.assertIsNone(result.selected_skill)
        self.assertEqual(result.mode, "fallback")
        self.assertEqual(result.plan, {"plan_type": "fake_direct"})


if __name__ == "__main__":
    unittest.main()
