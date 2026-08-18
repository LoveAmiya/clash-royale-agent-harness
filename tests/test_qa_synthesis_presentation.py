import unittest

from support import install_test_stubs

install_test_stubs()

import app_config  # noqa: F401 - loads the src package path for direct package imports.
from clashroyale_agent.qa.presentation import emit_chunked_content
from clashroyale_agent.qa.synthesis_fallbacks import (
    build_retrieved_evidence_fallback,
    build_snapshot_fallback_answer,
)
from runtime_events import RuntimeEventEmitter


class SynthesisFallbackTests(unittest.TestCase):
    def test_snapshot_fallback_sorts_decks_and_cards_without_live_claims(self):
        answer = build_snapshot_fallback_answer(
            top_decks_data=[
                {"rank": 2, "deck_name": "Deck B", "avg_elixir": 3.5},
                {"rank": 1, "deck_name": "Deck A", "avg_elixir": 3.1},
            ],
            cards_meta_data=[
                {"card_name": "Card B", "usage_rate": 5.0, "win_rate": 51.0},
                {"card_name": "Card A", "usage_rate": 7.0, "win_rate": 52.0},
            ],
        )

        self.assertLess(answer.index("第 1 名：Deck A"), answer.index("第 2 名：Deck B"))
        self.assertLess(answer.index("Card A：使用率 7.0%"), answer.index("Card B：使用率 5.0%"))
        self.assertIn("不是 LLM 的策略推演", answer)
        self.assertIn("不能严谨断言", answer)

    def test_retrieved_evidence_fallback_uses_compressed_text_and_titles(self):
        answer = build_retrieved_evidence_fallback(
            [
                {
                    "doc": {"metadata": {"title": "Fixture Title"}, "text": "raw"},
                    "compressed_text": "compressed",
                },
                {"doc": {"source_type": "deck", "text": "deck text"}},
                {"doc": {}, "compressed_text": ""},
                {"not_doc": True},
            ]
        )

        self.assertIn("模型综合请求超时", answer)
        self.assertIn("- Fixture Title：compressed", answer)
        self.assertIn("- deck：deck text", answer)
        self.assertNotIn("not_doc", answer)


class PresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_emit_chunked_content_preserves_chunk_order(self):
        emitter = RuntimeEventEmitter()

        await emit_chunked_content(
            emitter,
            "abcdefghij",
            chunk_size=4,
            interval_seconds=0,
        )

        chunks = []
        while not emitter.empty():
            chunks.append((await emitter.next_event())["text"])

        self.assertEqual(chunks, ["abcd", "efgh", "ij"])

    async def test_emit_chunked_content_ignores_empty_text(self):
        emitter = RuntimeEventEmitter()

        await emit_chunked_content(emitter, "", interval_seconds=0)

        self.assertTrue(emitter.empty())


if __name__ == "__main__":
    unittest.main()
