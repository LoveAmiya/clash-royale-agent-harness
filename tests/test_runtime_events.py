import unittest

from support import install_test_stubs

install_test_stubs()

from runtime_events import RuntimeEventEmitter


class RuntimeEventEmitterTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_events_use_stable_step_ids_and_preserve_content_contract(self):
        emitter = RuntimeEventEmitter()

        await emitter.execution(
            step_id="parse",
            phase="parse",
            status="running",
            title="正在解析问题",
            detail="调用模型解析结构化意图",
        )
        await emitter.execution(
            step_id="parse",
            phase="parse",
            status="completed",
            title="已解析问题",
            detail="识别到卡牌指标查询",
        )
        await emitter.content("**Electro Giant**", delta=True)

        events = [await emitter.next_event(), await emitter.next_event(), await emitter.next_event()]

        self.assertEqual(events[0]["object"], "execution")
        self.assertEqual(events[0]["step_id"], "parse")
        self.assertTrue(events[0]["replace"])
        self.assertEqual(events[1]["status"], "completed")
        self.assertEqual(events[2], {"object": "content", "type": "text", "text": "**Electro Giant**", "delta": True})

    async def test_trace_metadata_hashes_question_and_never_stores_player_tags(self):
        emitter = RuntimeEventEmitter(
            request_id="req-1",
            question="查询玩家 #SECRET 的觉醒骑士胜率",
            attributes={"dataset_scope": "7d_all", "entity_mode": "loadout_entity"},
        )

        await emitter.execution(
            step_id="q1.retrieve",
            phase="retrieve",
            status="completed",
            title="已检索",
            detail="完成证据检索",
        )
        event = await emitter.next_event()

        self.assertEqual(event["span_name"], "retrieval")
        self.assertEqual(event["trace_id"], "req-1")
        self.assertEqual(event["telemetry"]["dataset_scope"], "7d_all")
        self.assertGreater(event["telemetry"]["question_length"], 0)
        self.assertIn("question_hash", event["telemetry"])
        serialized = str(event)
        self.assertNotIn("#SECRET", serialized)
        self.assertNotIn("觉醒骑士胜率", serialized)
