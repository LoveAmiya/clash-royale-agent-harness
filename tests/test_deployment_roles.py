import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

import runtime_multi
from hybrid_retriever import HybridRetriever


class DeploymentRoleTests(unittest.IsolatedAsyncioTestCase):
    def test_in_memory_retriever_never_creates_persistent_manifest(self):
        docs = [{
            "doc_id": "snapshot-1:overview",
            "source_type": "overview",
            "text": "snapshot overview",
            "metadata": {"snapshot_id": "snapshot-1"},
        }]
        with patch.object(HybridRetriever, "_build_dense_index"):
            retriever = HybridRetriever(docs, in_memory=True)
        self.assertIsNone(retriever.index_path)
        self.assertIsNone(retriever.manifest_path)

    async def test_api_follower_switches_only_after_new_published_snapshot(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(
            live_snapshot={"snapshot_id": "old"},
            rag_snapshot_id="old",
            retriever=object(),
            rag_status="ready",
        ))
        published = {
            "snapshot_id": "new",
            "published_at": "2026-07-27T00:00:00+00:00",
            "cards_meta": [],
            "top_decks": [],
            "card_deck_stats": {},
        }
        with patch("runtime_multi.load_published_snapshot", return_value=published), patch(
            "runtime_multi.preheat_retriever_in_background", new=AsyncMock()
        ) as preheat, patch("runtime_multi.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await runtime_multi.follow_published_snapshot_loop(app)
        self.assertEqual(app.state.live_snapshot["snapshot_id"], "new")
        preheat.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
