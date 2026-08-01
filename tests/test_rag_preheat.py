import threading
import types
import unittest
from unittest.mock import patch

from support import install_test_stubs

install_test_stubs()

import runtime_multi
from snapshot_store import DAILY_TARGET_BATTLES, build_snapshot_rag_documents, compute_rag_docs_fingerprint
from skills.base import SkillContext
from skills.evidence_synthesis_skill import EvidenceSynthesisSkill


def active_snapshot(snapshot_id="official-new"):
    return {
        "snapshot_id": snapshot_id,
        "fetched_at": "2026-07-25T00:00:00+00:00",
        "sample_battles": DAILY_TARGET_BATTLES,
        "target_battles": DAILY_TARGET_BATTLES,
        "shortfall_battles": 0,
        "cards_meta": [
            {
                "rank": 1,
                "card_name": "Electro Giant",
                "usage_rate": 8.1,
                "win_rate": 54.2,
                "clean_win_rate": 54.2,
                "appearance_count": 1620,
            }
        ],
        "top_decks": [
            {
                "rank": 1,
                "deck_name": "Baby Dragon / Barbarian Barrel / Bowler / Electro Giant / Goblin Cage / Ice Wizard / Lightning / Tornado",
                "cards": ["Baby Dragon", "Barbarian Barrel", "Bowler", "Electro Giant", "Goblin Cage", "Ice Wizard", "Lightning", "Tornado"],
                "battles": 25,
                "sample_win_rate": 56.0,
            }
        ],
        "deck_matchups": [],
        "raw_battles": [],
        "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
    }


def snapshot_docs(snapshot_id):
    return build_snapshot_rag_documents(active_snapshot(snapshot_id))


class RAGPreheatTests(unittest.TestCase):
    def make_app(self):
        return types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot=active_snapshot(),
                retriever="old-retriever",
                rag_snapshot_id="official-old",
                rag_docs_fingerprint="old-documents",
                rag_document_validation=None,
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
        documents = snapshot_docs("official-new")
        fingerprint = compute_rag_docs_fingerprint(documents)
        app.state.live_snapshot["rag_docs_fingerprint"] = fingerprint
        candidate = types.SimpleNamespace(snapshot_id="official-new", dense_available=True, docs_fingerprint=fingerprint)

        with patch.object(runtime_multi, "load_docs", return_value=documents), patch.object(
            runtime_multi, "HybridRetriever", return_value=candidate
        ):
            retriever = runtime_multi.preheat_retriever(app)

        self.assertIs(retriever, candidate)
        self.assertIs(app.state.retriever, candidate)
        self.assertEqual(app.state.rag_snapshot_id, "official-new")
        self.assertEqual(app.state.rag_docs_fingerprint, fingerprint)
        self.assertEqual(app.state.rag_status, "ready")

    def test_preheat_uses_same_snapshot_bm25_when_dense_index_is_unavailable(self):
        app = self.make_app()
        documents = snapshot_docs("official-new")
        fingerprint = compute_rag_docs_fingerprint(documents)
        app.state.live_snapshot["rag_docs_fingerprint"] = fingerprint
        candidate = types.SimpleNamespace(snapshot_id="official-new", dense_available=False, docs_fingerprint=fingerprint)

        with patch.object(runtime_multi, "load_docs", return_value=documents), patch.object(
            runtime_multi, "HybridRetriever", return_value=candidate
        ):
            runtime_multi.preheat_retriever(app)

        self.assertIs(app.state.retriever, candidate)
        self.assertEqual(app.state.rag_snapshot_id, "official-new")
        self.assertEqual(app.state.rag_status, "bm25_only")

    def test_ensure_retriever_rejects_a_same_snapshot_stale_document_fingerprint(self):
        app = self.make_app()
        app.state.live_snapshot["rag_docs_fingerprint"] = "new-documents"
        app.state.rag_snapshot_id = "official-new"
        app.state.rag_docs_fingerprint = "old-documents"
        app.state.rag_status = "ready"
        app.state.retriever = types.SimpleNamespace(docs_fingerprint="old-documents")

        self.assertIsNone(runtime_multi.ensure_retriever(app))

    def test_failed_preheat_keeps_old_retriever_but_marks_new_snapshot_rag_failed(self):
        app = self.make_app()

        with patch.object(runtime_multi, "load_docs", side_effect=RuntimeError("documents missing")):
            retriever = runtime_multi.preheat_retriever(app)

        self.assertIsNone(retriever)
        self.assertEqual(app.state.retriever, "old-retriever")
        self.assertEqual(app.state.rag_snapshot_id, "official-old")
        self.assertEqual(app.state.rag_status, "failed")

    def test_quality_gate_rejects_new_index_without_switching_old_retriever(self):
        app = self.make_app()
        documents = snapshot_docs("official-new")
        fingerprint = compute_rag_docs_fingerprint(documents)
        app.state.live_snapshot["rag_docs_fingerprint"] = fingerprint
        candidate = types.SimpleNamespace(snapshot_id="official-new", dense_available=True, docs_fingerprint=fingerprint)
        failed_report = {"snapshot_id": "official-new", "passed": False, "failures": ["probe_recall_below_threshold"]}

        with patch.object(runtime_multi, "EXTERNAL_API_REQUIRED", True), patch.object(
            runtime_multi, "RAG_QUALITY_GATE_ENABLED", True
        ), patch.object(runtime_multi, "load_docs", return_value=documents), patch.object(
            runtime_multi, "HybridRetriever", return_value=candidate
        ), patch.object(runtime_multi, "evaluate_rag_quality", return_value=failed_report), patch.object(
            runtime_multi, "persist_quality_report"
        ):
            retriever = runtime_multi.preheat_retriever(app)

        self.assertIsNone(retriever)
        self.assertEqual(app.state.retriever, "old-retriever")
        self.assertEqual(app.state.rag_snapshot_id, "official-old")
        self.assertEqual(app.state.rag_status, "failed")
        self.assertEqual(app.state.rag_quality_report, failed_report)

    def test_same_snapshot_id_with_changed_documents_rebuilds_before_switching(self):
        app = self.make_app()
        app.state.rag_snapshot_id = "official-new"
        app.state.rag_status = "ready"
        app.state.rag_docs_fingerprint = "old-documents"
        app.state.retriever = types.SimpleNamespace(snapshot_id="official-new", docs_fingerprint="old-documents")
        app.state.live_snapshot["fetched_at"] = "2026-07-25T01:00:00+00:00"
        documents = build_snapshot_rag_documents(app.state.live_snapshot)
        fingerprint = compute_rag_docs_fingerprint(documents)
        app.state.live_snapshot["rag_docs_fingerprint"] = fingerprint
        candidate = types.SimpleNamespace(snapshot_id="official-new", dense_available=True, docs_fingerprint=fingerprint)

        with patch.object(runtime_multi, "load_docs", return_value=documents), patch.object(
            runtime_multi, "HybridRetriever", return_value=candidate
        ) as retriever_class:
            result = runtime_multi.preheat_retriever(app)

        retriever_class.assert_called_once()
        self.assertIs(result, candidate)
        self.assertEqual(app.state.rag_docs_fingerprint, fingerprint)

    def test_invalid_new_documents_do_not_replace_previous_retriever(self):
        app = self.make_app()
        old = app.state.retriever
        documents = snapshot_docs("official-new")
        next(doc for doc in documents if doc["source_type"] == "card")["metadata"]["win_rate"] = None
        app.state.live_snapshot["rag_docs_fingerprint"] = compute_rag_docs_fingerprint(documents)

        with patch.object(runtime_multi, "load_docs", return_value=documents), patch.object(
            runtime_multi, "HybridRetriever"
        ) as retriever_class:
            result = runtime_multi.preheat_retriever(app)

        self.assertIsNone(result)
        self.assertIs(app.state.retriever, old)
        retriever_class.assert_not_called()
        self.assertEqual(app.state.rag_status, "failed")

    def test_candidate_snapshot_and_index_switch_together_after_validation(self):
        app = self.make_app()
        previous_snapshot = active_snapshot("official-old")
        previous_fingerprint = compute_rag_docs_fingerprint(build_snapshot_rag_documents(previous_snapshot))
        previous_snapshot["rag_docs_fingerprint"] = previous_fingerprint
        previous_retriever = types.SimpleNamespace(
            snapshot_id="official-old",
            docs_fingerprint=previous_fingerprint,
            dense_available=True,
        )
        app.state.live_snapshot = previous_snapshot
        app.state.retriever = previous_retriever
        app.state.rag_snapshot_id = "official-old"
        app.state.rag_docs_fingerprint = previous_fingerprint
        app.state.rag_status = "ready"

        candidate_snapshot = active_snapshot("official-new")
        candidate_snapshot["fetched_at"] = "2026-07-26T00:00:00+00:00"
        documents = build_snapshot_rag_documents(candidate_snapshot)
        fingerprint = compute_rag_docs_fingerprint(documents)
        candidate_snapshot["rag_docs_fingerprint"] = fingerprint
        candidate = types.SimpleNamespace(
            snapshot_id="official-new",
            docs_fingerprint=fingerprint,
            dense_available=True,
        )

        with patch.object(runtime_multi, "load_docs", return_value=documents), patch.object(
            runtime_multi, "HybridRetriever", return_value=candidate
        ):
            result = runtime_multi.preheat_retriever(
                app,
                candidate_snapshot=candidate_snapshot,
                activate_snapshot=True,
            )

        self.assertIs(result, candidate)
        self.assertEqual(app.state.live_snapshot["snapshot_id"], "official-new")
        self.assertIs(app.state.retriever, candidate)
        self.assertEqual(app.state.rag_docs_fingerprint, fingerprint)

    def test_failed_candidate_validation_preserves_previous_snapshot_and_index(self):
        app = self.make_app()
        previous_snapshot = active_snapshot("official-old")
        previous_fingerprint = compute_rag_docs_fingerprint(build_snapshot_rag_documents(previous_snapshot))
        previous_snapshot["rag_docs_fingerprint"] = previous_fingerprint
        previous_retriever = types.SimpleNamespace(
            snapshot_id="official-old",
            docs_fingerprint=previous_fingerprint,
            dense_available=True,
        )
        app.state.live_snapshot = previous_snapshot
        app.state.retriever = previous_retriever
        app.state.rag_snapshot_id = "official-old"
        app.state.rag_docs_fingerprint = previous_fingerprint
        app.state.rag_status = "ready"

        candidate_snapshot = active_snapshot("official-new")
        documents = build_snapshot_rag_documents(candidate_snapshot)
        next(doc for doc in documents if doc["source_type"] == "card")["metadata"]["usage_rate"] = None
        candidate_snapshot["rag_docs_fingerprint"] = compute_rag_docs_fingerprint(documents)

        with patch.object(runtime_multi, "load_docs", return_value=documents), patch.object(
            runtime_multi, "HybridRetriever"
        ) as retriever_class:
            result = runtime_multi.preheat_retriever(
                app,
                candidate_snapshot=candidate_snapshot,
                activate_snapshot=True,
            )

        self.assertIsNone(result)
        retriever_class.assert_not_called()
        self.assertEqual(app.state.live_snapshot["snapshot_id"], "official-old")
        self.assertIs(app.state.retriever, previous_retriever)
        self.assertEqual(app.state.rag_status, "ready")
        self.assertEqual(app.state.rag_candidate_status, "failed")

    def test_snapshot_status_exposes_rag_state_without_exposing_internal_error_details(self):
        app = self.make_app()
        app.state.rag_status = "building"
        app.state.rag_error = "embedding endpoint failed with internal detail"

        status = runtime_multi.get_live_snapshot_status(app)

        self.assertEqual(status["rag"]["status"], "building")
        self.assertEqual(status["rag"]["snapshot_id"], "official-old")
        self.assertNotIn("error", status["rag"])

    def test_status_and_readiness_require_snapshot_document_and_index_fingerprints_to_align(self):
        app = self.make_app()
        app.state.initialized = True
        app.state.live_refresh_status = "ready"
        app.state.rag_status = "ready"
        app.state.rag_snapshot_id = "official-new"
        app.state.live_snapshot["rag_docs_fingerprint"] = "fingerprint-1"
        app.state.rag_docs_fingerprint = "fingerprint-1"
        app.state.retriever = types.SimpleNamespace(docs_fingerprint="fingerprint-1")

        status = runtime_multi.get_live_snapshot_status(app)
        readiness = runtime_multi.get_readiness_status(
            app,
            external_api_required=True,
            model_api_configured=True,
        )

        self.assertTrue(status["rag"]["snapshot_aligned"])
        self.assertTrue(status["rag"]["fingerprint_aligned"])
        self.assertEqual(status["rag"]["snapshot_docs_fingerprint"], "fingerprint-1")
        self.assertTrue(readiness["snapshot_rag_fingerprint_aligned"])
        self.assertNotIn("snapshot_rag_fingerprint_misaligned", readiness["degraded_reasons"])

        app.state.retriever.docs_fingerprint = "fingerprint-stale"
        degraded = runtime_multi.get_readiness_status(
            app,
            external_api_required=True,
            model_api_configured=True,
        )
        self.assertEqual(degraded["status"], "degraded")
        self.assertIn("snapshot_rag_fingerprint_misaligned", degraded["degraded_reasons"])

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
        self.assertEqual(status["data_sources"]["schedule"], "disabled_clan_war_feature")


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
