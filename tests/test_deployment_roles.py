import asyncio
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock, patch

import runtime_multi
from hybrid_retriever import HybridRetriever


class DeploymentRoleTests(unittest.IsolatedAsyncioTestCase):
    ROOT = Path(__file__).resolve().parent.parent

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

    def test_observability_stack_provisions_dashboard_alerts_and_persistent_storage(self):
        compose = (self.ROOT / "compose.production.yml").read_text(encoding="utf-8")
        prometheus = (self.ROOT / "deploy" / "prometheus.yml").read_text(encoding="utf-8")
        dashboard_provider = (
            self.ROOT / "deploy" / "grafana-provisioning" / "dashboards" / "dashboards.yml"
        )
        dashboard = (
            self.ROOT / "deploy" / "grafana-provisioning" / "dashboards" / "clash-royale-agent.json"
        )
        alerts = self.ROOT / "deploy" / "prometheus-alerts.yml"

        self.assertTrue(dashboard_provider.exists())
        self.assertTrue(dashboard.exists())
        self.assertTrue(alerts.exists())
        self.assertIn("prometheus-alerts.yml", compose)
        self.assertIn("prometheus_data:/prometheus", compose)
        self.assertIn("loki_data:/loki", compose)
        self.assertIn("grafana_data:/var/lib/grafana", compose)
        self.assertIn("rule_files:", prometheus)
        self.assertIn("ClashRoyaleAgentHighFailureRate", alerts.read_text(encoding="utf-8"))
        self.assertIn("cr_agent_runtime_state", dashboard.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
