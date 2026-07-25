import sys
import types
import unittest
from unittest.mock import patch

from model_gateway import generate_model_text_stream


class _AsyncEvents:
    def __init__(self, events):
        self._events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class ModelGatewayStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_stream_yields_only_public_text_deltas(self):
        captured = {}
        events = _AsyncEvents(
            [
                types.SimpleNamespace(type="response.created"),
                types.SimpleNamespace(type="response.output_text.delta", delta="第一段"),
                types.SimpleNamespace(type="response.reasoning_summary_text.delta", delta="must not leak"),
                types.SimpleNamespace(type="response.output_text.delta", delta="第二段"),
            ]
        )

        class FakeResponses:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return events

        class FakeClient:
            def __init__(self, **_kwargs):
                self.responses = FakeResponses()

        fake_openai = types.SimpleNamespace(AsyncOpenAI=FakeClient)
        with patch.dict(sys.modules, {"openai": fake_openai}), patch("model_gateway.uses_responses_api", return_value=True):
            chunks = [
                chunk
                async for chunk in generate_model_text_stream(
                    api_key="test-key",
                    instructions="test instructions",
                    input_text="test input",
                    reasoning_effort="low",
                )
            ]

        self.assertEqual(chunks, ["第一段", "第二段"])
        self.assertTrue(captured["stream"])
        self.assertEqual(captured["reasoning"], {"effort": "low"})
