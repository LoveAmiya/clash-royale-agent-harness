import json
import os
import tempfile
import unittest
from pathlib import Path

from support import install_test_stubs

install_test_stubs()

from harness.executor import SkillExecutor
from harness.state import FAILED, FALLBACK, PENDING, RUNNING, SUCCESS
from harness.trace import TraceRecorder
from skills.base import SkillContext
from skills.registry import SkillRegistry


class StaticSkill:
    name = "StaticSkill"

    def __init__(self, answer="ok"):
        self.answer = answer

    def can_handle(self, parsed: dict) -> bool:
        return True

    def run(self, context: SkillContext) -> str:
        return self.answer


class AsyncSkill:
    name = "AsyncSkill"

    def can_handle(self, parsed: dict) -> bool:
        return True

    async def run(self, context: SkillContext) -> str:
        return "async-ok"


class FailingSkill:
    name = "FailingSkill"

    def can_handle(self, parsed: dict) -> bool:
        return True

    def run(self, context: SkillContext) -> str:
        raise RuntimeError("boom")


def build_context(parsed=None) -> SkillContext:
    return SkillContext(
        user_text="test question",
        parsed=parsed or {"intent": "schedule_query"},
        schedule_data=[],
        top_decks_data=[],
        cards_meta_data=[],
        retriever=None,
        api_key="",
    )


class HarnessTraceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.tmpdir.name) / "traces.jsonl"
        self.recorder = TraceRecorder(log_path=self.log_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def read_events(self):
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    async def test_executor_records_pending_running_success_for_sync_skill(self):
        executor = SkillExecutor(SkillRegistry([StaticSkill(answer="sync-ok")]), recorder=self.recorder)

        answer = await executor.execute(build_context())

        self.assertEqual(answer, "sync-ok")
        events = self.read_events()
        self.assertEqual([event["state"] for event in events], [PENDING, RUNNING, SUCCESS])
        self.assertEqual(events[1]["selected_skill"], "StaticSkill")
        self.assertEqual(events[2]["success"], True)
        self.assertEqual(events[2]["mode"], "direct")
        self.assertIn("parsed", events[2])

    async def test_executor_records_success_for_async_skill(self):
        executor = SkillExecutor(SkillRegistry([AsyncSkill()]), recorder=self.recorder)

        answer = await executor.execute(build_context({"intent": "card_query"}))

        self.assertEqual(answer, "async-ok")
        events = self.read_events()
        self.assertEqual(events[-1]["state"], SUCCESS)
        self.assertEqual(events[-1]["selected_skill"], "AsyncSkill")

    async def test_executor_records_fallback_when_no_skill_matches(self):
        executor = SkillExecutor(SkillRegistry([]), recorder=self.recorder)

        answer = await executor.execute(build_context({"intent": "reject"}))

        self.assertIsNone(answer)
        events = self.read_events()
        self.assertEqual([event["state"] for event in events], [PENDING, FALLBACK])
        self.assertEqual(events[-1]["mode"], "fallback")
        self.assertEqual(events[-1]["selected_skill"], None)

    async def test_executor_records_failed_and_reraises(self):
        executor = SkillExecutor(SkillRegistry([FailingSkill()]), recorder=self.recorder)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await executor.execute(build_context({"intent": "deck_query"}))

        events = self.read_events()
        self.assertEqual([event["state"] for event in events], [PENDING, RUNNING, FAILED])
        self.assertEqual(events[-1]["success"], False)
        self.assertEqual(events[-1]["error"], "boom")

    async def test_trace_recorder_writes_expected_fields(self):
        executor = SkillExecutor(SkillRegistry([StaticSkill()]), recorder=self.recorder)

        await executor.execute(build_context({"intent": "schedule_query"}))

        event = self.read_events()[-1]
        self.assertIn("trace_id", event)
        self.assertIn("state", event)
        self.assertIn("selected_skill", event)
        self.assertIn("intent", event)
        self.assertIn("mode", event)
        self.assertIn("latency_ms", event)
        self.assertIn("success", event)
        self.assertIn("error", event)
        self.assertIn("parsed", event)
        self.assertIn("timestamp_ms", event)

    def test_default_trace_path_can_be_redirected_for_test_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            isolated_path = Path(directory) / "isolated-traces.jsonl"
            previous = os.environ.get("CR_AGENT_TRACE_LOG_PATH")
            os.environ["CR_AGENT_TRACE_LOG_PATH"] = str(isolated_path)
            try:
                recorder = TraceRecorder()
            finally:
                if previous is None:
                    os.environ.pop("CR_AGENT_TRACE_LOG_PATH", None)
                else:
                    os.environ["CR_AGENT_TRACE_LOG_PATH"] = previous

        self.assertEqual(recorder.log_path, isolated_path)
