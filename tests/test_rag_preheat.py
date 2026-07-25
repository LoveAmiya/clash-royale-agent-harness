import threading
import types
import unittest
from unittest.mock import patch

from support import install_test_stubs

install_test_stubs()

import runtime_multi
from skills.base import SkillContext
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill


def active_snapshot(snapshot_id="official-new"):
    return {
        "snapshot_id": snapshot_id,
        "fetched_at": "2026-07-25T00:00:00+00:00",
        "sample_battles": 20_000,
        "target_battles": 20_000,
        "shortfall_battles": 0,
        "cards_meta": [],
        "top_decks": [],
        "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
    }


def snapshot_docs(snapshot_id):
    return [
        {
            "doc_id": f"{snapshot_id}:overview",
            "source_type": "snapshot",
            "text": "official evidence",
            "metadata": {"snapshot_id": snapshot_id},
        }
    ]


class RAGPreheatTests(unittest.TestCase):
    def make_app(self):
        return types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=active_snapshot(),
                retriever="old-retriever",
                rag_snapshot_id="official-old",
                rag_status="not_ready",
                rag_error=None,
                rag_preheat_lock=threading.Lock(),
                rag_preheat_task=None,
                live_refresh_status="ready",
                live_error=None,
                live_cooldown_until=0.0,
            )
        )

    def test_preheat_switches_active_retriever_only_after_new_snapshot_index_builds(self):
        app = self.make_app()
        candidate = types.SimpleNamespace(snapshot_id="official-new", dense_available=True)

        with patch.object(runtime_multi, "load_docs", return_value=snapshot_docs("official-new")), patch.object(
            runtime_multi, "HybridRetriever", return_value=candidate
        ):
            retriever = runtime_multi.preheat_retriever(app)

        self.assertIs(retriever, candidate)
        self.assertIs(app.state.retriever, candidate)
        self.assertEqual(app.state.rag_snapshot_id, "official-new")
        self.assertEqual(app.state.rag_status, "ready")

    def test_preheat_uses_same_snapshot_bm25_when_dense_index_is_unavailable(self):
        app = self.make_app()
        candidate = types.SimpleNamespace(snapshot_id="official-new", dense_available=False)

        with patch.object(runtime_multi, "load_docs", return_value=snapshot_docs("official-new")), patch.object(
            runtime_multi, "HybridRetriever", return_value=candidate
        ):
            runtime_multi.preheat_retriever(app)

        self.assertIs(app.state.retriever, candidate)
        self.assertEqual(app.state.rag_snapshot_id, "official-new")
        self.assertEqual(app.state.rag_status, "bm25_only")

    def test_failed_preheat_keeps_old_retriever_but_marks_new_snapshot_rag_failed(self):
        app = self.make_app()

        with patch.object(runtime_multi, "load_docs", side_effect=RuntimeError("documents missing")):
            retriever = runtime_multi.preheat_retriever(app)

        self.assertIsNone(retriever)
        self.assertEqual(app.state.retriever, "old-retriever")
        self.assertEqual(app.state.rag_snapshot_id, "official-old")
        self.assertEqual(app.state.rag_status, "failed")

    def test_snapshot_status_exposes_rag_state_without_exposing_internal_error_details(self):
        app = self.make_app()
        app.state.rag_status = "building"
        app.state.rag_error = "embedding endpoint failed with internal detail"

        status = runtime_multi.get_live_snapshot_status(app)

        self.assertEqual(status["rag"]["status"], "building")
        self.assertEqual(status["rag"]["snapshot_id"], "official-old")
        self.assertNotIn("error", status["rag"])

    def test_missing_snapshot_reports_missing_data_and_not_ready_rag(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=None,
                live_refresh_status="missing",
                live_error=None,
                rag_status="not_ready",
                rag_snapshot_id=None,
                live_cooldown_until=0.0,
            )
        )

        status = runtime_multi.get_live_snapshot_status(app)

        self.assertEqual(status["status"], "missing")
        self.assertEqual(status["snapshot_status"], "missing")
        self.assertEqual(status["rag"]["status"], "not_ready")
        self.assertEqual(status["rag_status"], "not_ready")
        self.assertEqual(status["data_sources"]["cards"], "not_available")
        self.assertEqual(status["data_sources"]["schedule"], "local_schedule_json")


class RAGAvailabilityMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_building_index_returns_an_explicit_non_conclusion_message(self):
        answer = await EvidenceSynthesisSkill().run(
            SkillContext(
                user_text="当前环境如何",
                parsed={"intent": "meta_analysis_query"},
                schedule_data=[],
                top_decks_data=[],
                cards_meta_data=[],
                retriever=None,
                api_key="test-key",
                metadata={"rag_status": "building"},
            )
        )

        self.assertIn("后台预热", answer)
        self.assertIn("不会使用未完成", answer)


if __name__ == "__main__":
    unittest.main()
