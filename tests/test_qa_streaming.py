import asyncio
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.

from clashroyale_agent.qa.streaming import (
    ModelFirstTokenTimeout,
    stream_with_first_token_watchdog,
)
from runtime_events import RuntimeEventEmitter


class QAStreamingWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_with_first_token_watchdog_yields_model_deltas(self):
        async def model_stream():
            yield "first"
            yield "second"

        emitter = RuntimeEventEmitter()

        deltas = [
            delta
            async for delta in stream_with_first_token_watchdog(
                model_stream(),
                event_sink=emitter,
                step_id="q.generate",
                subquery_id="q",
                first_token_timeout_seconds=1,
                progress_interval_seconds=0.01,
            )
        ]

        events = []
        while not emitter.empty():
            events.append(await emitter.next_event())

        self.assertEqual(deltas, ["first", "second"])
        self.assertTrue(
            any(
                event.get("object") == "execution"
                and event.get("operation") == "synthesize.validate_stream"
                for event in events
            )
        )

    async def test_stream_with_first_token_watchdog_times_out_and_closes_stream(self):
        closed = False

        async def delayed_stream():
            nonlocal closed
            try:
                await asyncio.sleep(1)
                yield "too late"
            finally:
                closed = True

        emitter = RuntimeEventEmitter()

        with self.assertRaises(ModelFirstTokenTimeout):
            _ = [
                delta
                async for delta in stream_with_first_token_watchdog(
                    delayed_stream(),
                    event_sink=emitter,
                    step_id="q.generate",
                    subquery_id="q",
                    first_token_timeout_seconds=0.03,
                    progress_interval_seconds=0.01,
                )
            ]

        events = []
        while not emitter.empty():
            events.append(await emitter.next_event())

        self.assertTrue(closed)
        wait_events = [
            event
            for event in events
            if event.get("object") == "execution"
            and event.get("operation") == "synthesize.await_first_text"
        ]
        self.assertEqual(len(wait_events), 1)
        self.assertTrue(any(event.get("object") == "progress" for event in events))


if __name__ == "__main__":
    unittest.main()
