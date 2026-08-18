import types
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.api.snapshot_state import (
    activate_snapshot_state,
    active_snapshot_id,
    begin_rag_candidate_build,
    can_reuse_previous_rag_index_after_failure,
    cleanup_rag_snapshot_retention,
    complete_rag_candidate_build,
    fail_rag_candidate_build,
    live_snapshot_refresh_gate,
    next_live_refresh_delay_seconds,
    publish_rag_candidate_index,
    rag_ready_status,
    record_live_collection_progress,
    record_live_refresh_attempt,
    record_rag_candidate_validation,
    refresh_cooldown_seconds,
    run_rag_quality_gate,
    should_discard_rag_candidate_index,
    validate_rag_candidate_documents,
    validate_rag_candidate_index,
)


class ApiSnapshotStateTests(unittest.TestCase):
    def test_can_reuse_previous_rag_index_after_failure_accepts_aligned_ready_index(self):
        retriever = types.SimpleNamespace(docs_fingerprint="fingerprint-1")
        active_snapshot = {
            "snapshot_id": "snapshot-1",
            "rag_docs_fingerprint": "fingerprint-1",
        }

        self.assertTrue(
            can_reuse_previous_rag_index_after_failure(
                previous_status="ready",
                previous_retriever=retriever,
                previous_snapshot_id="snapshot-1",
                previous_fingerprint="fingerprint-1",
                active_snapshot=active_snapshot,
            )
        )
        self.assertTrue(
            can_reuse_previous_rag_index_after_failure(
                previous_status="bm25_only",
                previous_retriever=retriever,
                previous_snapshot_id="snapshot-1",
                previous_fingerprint="fingerprint-1",
                active_snapshot=active_snapshot,
            )
        )

    def test_can_reuse_previous_rag_index_after_failure_rejects_misaligned_inputs(self):
        retriever = types.SimpleNamespace(docs_fingerprint="fingerprint-1")
        cases = [
            {"previous_status": "failed"},
            {"previous_retriever": None},
            {"previous_snapshot_id": None},
            {"previous_fingerprint": None},
            {"active_snapshot": None},
            {"active_snapshot": {"snapshot_id": "snapshot-2", "rag_docs_fingerprint": "fingerprint-1"}},
            {"active_snapshot": {"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-2"}},
            {"previous_retriever": types.SimpleNamespace(docs_fingerprint="fingerprint-2")},
        ]

        defaults = {
            "previous_status": "ready",
            "previous_retriever": retriever,
            "previous_snapshot_id": "snapshot-1",
            "previous_fingerprint": "fingerprint-1",
            "active_snapshot": {"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fingerprint-1"},
        }
        for case in cases:
            with self.subTest(case=case):
                values = {**defaults, **case}
                self.assertFalse(can_reuse_previous_rag_index_after_failure(**values))

    def test_cleanup_rag_snapshot_retention_skips_memory_index_mode(self):
        calls = []

        report = cleanup_rag_snapshot_retention(
            index_mode="memory",
            cleanup=lambda *args, **kwargs: calls.append((args, kwargs)),
            data_dir="data",
            active_snapshot_id="snapshot-1",
        )

        self.assertIsNone(report)
        self.assertEqual(calls, [])

    def test_cleanup_rag_snapshot_retention_runs_for_persistent_index_mode(self):
        calls = []
        expected = {"retained_snapshot_ids": ["snapshot-1"], "removed_snapshot_ids": ["snapshot-0"]}

        def cleanup(data_dir, *, active_snapshot_id):
            calls.append((data_dir, active_snapshot_id))
            return expected

        report = cleanup_rag_snapshot_retention(
            index_mode="persistent",
            cleanup=cleanup,
            data_dir="data",
            active_snapshot_id="snapshot-1",
        )

        self.assertIs(report, expected)
        self.assertEqual(calls, [("data", "snapshot-1")])

    def test_publish_rag_candidate_index_updates_active_state_and_closes_previous(self):
        closed = []
        previous = types.SimpleNamespace(close=lambda: closed.append("closed"))
        candidate = types.SimpleNamespace(dense_available=True)
        validation = {"passed": True, "docs_fingerprint": "fingerprint-1"}
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                retriever=previous,
                rag_candidate_error="ValueError",
            )
        )

        publish_rag_candidate_index(
            app,
            candidate=candidate,
            snapshot_id="snapshot-1",
            docs_fingerprint="fingerprint-1",
            validation=validation,
            previous_retriever=previous,
        )

        self.assertIs(app.state.retriever, candidate)
        self.assertEqual(app.state.rag_snapshot_id, "snapshot-1")
        self.assertEqual(app.state.rag_docs_fingerprint, "fingerprint-1")
        self.assertEqual(app.state.rag_status, "ready")
        self.assertEqual(app.state.rag_candidate_status, "ready")
        self.assertIs(app.state.rag_document_validation, validation)
        self.assertIsNone(app.state.rag_candidate_error)
        self.assertEqual(closed, ["closed"])

    def test_publish_rag_candidate_index_does_not_close_reused_retriever(self):
        closed = []
        candidate = types.SimpleNamespace(dense_available=False, close=lambda: closed.append("closed"))
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        publish_rag_candidate_index(
            app,
            candidate=candidate,
            snapshot_id="snapshot-1",
            docs_fingerprint="fingerprint-1",
            validation={"passed": True},
            previous_retriever=candidate,
        )

        self.assertEqual(app.state.rag_status, "bm25_only")
        self.assertEqual(closed, [])

    def test_should_discard_rag_candidate_index_keeps_activation_builds(self):
        self.assertFalse(
            should_discard_rag_candidate_index(
                activate_snapshot=True,
                active_snapshot_id="snapshot-2",
                candidate_snapshot_id="snapshot-1",
            )
        )

    def test_should_discard_rag_candidate_index_keeps_matching_active_snapshot(self):
        self.assertFalse(
            should_discard_rag_candidate_index(
                activate_snapshot=False,
                active_snapshot_id="snapshot-1",
                candidate_snapshot_id="snapshot-1",
            )
        )

    def test_should_discard_rag_candidate_index_discards_stale_non_activation_builds(self):
        self.assertTrue(
            should_discard_rag_candidate_index(
                activate_snapshot=False,
                active_snapshot_id="snapshot-2",
                candidate_snapshot_id="snapshot-1",
            )
        )

    def test_run_rag_quality_gate_skips_when_disabled(self):
        calls = []

        report = run_rag_quality_gate(
            enabled=False,
            external_api_required=True,
            evaluate=lambda **kwargs: calls.append(("evaluate", kwargs)),
            persist=lambda **kwargs: calls.append(("persist", kwargs)),
            quality_gate_error=RuntimeError,
            snapshot_id="snapshot-1",
            docs=[],
            retriever=object(),
            report_dir="reports",
            min_documents=1,
            min_source_types=1,
            min_probe_recall=0.5,
            probes_per_source=2,
        )

        self.assertIsNone(report)
        self.assertEqual(calls, [])

    def test_run_rag_quality_gate_persists_report_and_returns_it(self):
        calls = []
        expected = {"snapshot_id": "snapshot-1", "passed": True}

        def evaluate(**kwargs):
            calls.append(("evaluate", kwargs))
            return expected

        def persist(**kwargs):
            calls.append(("persist", kwargs))

        report = run_rag_quality_gate(
            enabled=True,
            external_api_required=True,
            evaluate=evaluate,
            persist=persist,
            quality_gate_error=RuntimeError,
            snapshot_id="snapshot-1",
            docs=[{"doc_id": "doc-1"}],
            retriever="retriever",
            report_dir="reports",
            min_documents=4,
            min_source_types=2,
            min_probe_recall=0.75,
            probes_per_source=3,
        )

        self.assertIs(report, expected)
        self.assertEqual([name for name, _ in calls], ["evaluate", "persist"])
        self.assertEqual(calls[0][1]["min_documents"], 4)
        self.assertEqual(calls[0][1]["min_source_types"], 2)
        self.assertEqual(calls[0][1]["min_probe_recall"], 0.75)
        self.assertEqual(calls[1][1]["report"], expected)

    def test_run_rag_quality_gate_raises_after_persisting_failed_report(self):
        calls = []
        recorded = []
        failed = {"snapshot_id": "snapshot-1", "passed": False}

        with self.assertRaisesRegex(RuntimeError, "configured snapshot quality gate"):
            run_rag_quality_gate(
                enabled=True,
                external_api_required=True,
                evaluate=lambda **kwargs: failed,
                persist=lambda **kwargs: calls.append(kwargs["report"]),
                record=recorded.append,
                quality_gate_error=RuntimeError,
                snapshot_id="snapshot-1",
                docs=[],
                retriever="retriever",
                report_dir="reports",
                min_documents=1,
                min_source_types=1,
                min_probe_recall=0.5,
                probes_per_source=2,
            )

        self.assertEqual(calls, [failed])
        self.assertEqual(recorded, [failed])

    def test_validate_rag_candidate_documents_returns_validated_fingerprint(self):
        validation = {"passed": True, "docs_fingerprint": "fingerprint-1"}

        fingerprint = validate_rag_candidate_documents(
            validation,
            snapshot_fingerprint="fingerprint-1",
        )

        self.assertEqual(fingerprint, "fingerprint-1")

    def test_validate_rag_candidate_documents_rejects_failed_or_mismatched_documents(self):
        with self.assertRaisesRegex(ValueError, "failed full snapshot evidence validation"):
            validate_rag_candidate_documents(
                {"passed": False, "docs_fingerprint": "fingerprint-1"},
                snapshot_fingerprint="fingerprint-1",
            )

        with self.assertRaisesRegex(ValueError, "does not match the active official snapshot"):
            validate_rag_candidate_documents(
                {"passed": True, "docs_fingerprint": "fingerprint-1"},
                snapshot_fingerprint="fingerprint-2",
            )

    def test_validate_rag_candidate_index_rejects_snapshot_or_document_mismatch(self):
        validate_rag_candidate_index(
            candidate_snapshot_id="snapshot-1",
            expected_snapshot_id="snapshot-1",
            candidate_docs_fingerprint="fingerprint-1",
            expected_docs_fingerprint="fingerprint-1",
        )

        with self.assertRaisesRegex(ValueError, "does not match the active official weekly snapshot"):
            validate_rag_candidate_index(
                candidate_snapshot_id="snapshot-2",
                expected_snapshot_id="snapshot-1",
                candidate_docs_fingerprint="fingerprint-1",
                expected_docs_fingerprint="fingerprint-1",
            )

        with self.assertRaisesRegex(ValueError, "does not match validated RAG documents"):
            validate_rag_candidate_index(
                candidate_snapshot_id="snapshot-1",
                expected_snapshot_id="snapshot-1",
                candidate_docs_fingerprint="fingerprint-2",
                expected_docs_fingerprint="fingerprint-1",
            )

    def test_begin_rag_candidate_build_preserves_active_rag_status(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                rag_status="ready",
                rag_error="old-error",
                rag_candidate_status="failed",
            )
        )

        begin_rag_candidate_build(app, has_active_retriever=True)

        self.assertEqual(app.state.rag_status, "ready")
        self.assertEqual(app.state.rag_candidate_status, "building")
        self.assertIsNone(app.state.rag_error)

    def test_begin_rag_candidate_build_marks_empty_runtime_building(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                rag_status="not_ready",
                rag_error=None,
                rag_candidate_status="not_ready",
            )
        )

        begin_rag_candidate_build(app, has_active_retriever=False)

        self.assertEqual(app.state.rag_status, "building")
        self.assertEqual(app.state.rag_candidate_status, "building")

    def test_complete_rag_candidate_build_records_validation_and_optional_error_reset(self):
        validation = {"passed": True, "docs_fingerprint": "fingerprint-1"}
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                rag_status="building",
                rag_candidate_status="building",
                rag_candidate_error="ValueError",
            )
        )

        complete_rag_candidate_build(
            app,
            status="ready",
            validation=validation,
            clear_candidate_error=True,
        )

        self.assertEqual(app.state.rag_status, "ready")
        self.assertEqual(app.state.rag_candidate_status, "ready")
        self.assertIs(app.state.rag_document_validation, validation)
        self.assertIsNone(app.state.rag_candidate_error)

    def test_fail_rag_candidate_build_records_only_public_error_type(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        fail_rag_candidate_build(app, error_type="RuntimeError")

        self.assertEqual(app.state.rag_candidate_status, "failed")
        self.assertEqual(app.state.rag_candidate_error, "RuntimeError")
        self.assertEqual(app.state.rag_error, "RuntimeError")

    def test_record_rag_candidate_validation_keeps_report_for_redaction_boundary(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())
        validation = {"passed": False, "invalid_document_ids": ["private-detail"]}

        record_rag_candidate_validation(app, validation)

        self.assertIs(app.state.rag_candidate_validation, validation)

    def test_refresh_cooldown_seconds_bounds_consecutive_failures(self):
        self.assertEqual(refresh_cooldown_seconds(0), 300)
        self.assertEqual(refresh_cooldown_seconds(1), 300)
        self.assertEqual(refresh_cooldown_seconds(2), 900)
        self.assertEqual(refresh_cooldown_seconds(3), 1800)
        self.assertEqual(refresh_cooldown_seconds(99), 1800)

    def test_next_live_refresh_delay_uses_cooldown_floor(self):
        self.assertEqual(
            next_live_refresh_delay_seconds(
                refresh_status="cooldown",
                cooldown_until=125.0,
                now_monotonic=100.0,
                snapshot_present=True,
                snapshot_age_seconds=10.0,
                refresh_interval_seconds=3600.0,
            ),
            60.0,
        )
        self.assertEqual(
            next_live_refresh_delay_seconds(
                refresh_status="cooldown",
                cooldown_until=500.0,
                now_monotonic=100.0,
                snapshot_present=True,
                snapshot_age_seconds=10.0,
                refresh_interval_seconds=3600.0,
            ),
            400.0,
        )

    def test_next_live_refresh_delay_uses_source_exhausted_floor(self):
        self.assertEqual(
            next_live_refresh_delay_seconds(
                refresh_status="source_exhausted",
                cooldown_until=500.0,
                now_monotonic=100.0,
                snapshot_present=True,
                snapshot_age_seconds=10.0,
                refresh_interval_seconds=3600.0,
            ),
            3600.0,
        )

    def test_next_live_refresh_delay_handles_missing_and_fresh_snapshots(self):
        self.assertEqual(
            next_live_refresh_delay_seconds(
                refresh_status="ready",
                cooldown_until=0.0,
                now_monotonic=100.0,
                snapshot_present=False,
                snapshot_age_seconds=None,
                refresh_interval_seconds=3600.0,
            ),
            1800.0,
        )
        self.assertEqual(
            next_live_refresh_delay_seconds(
                refresh_status="ready",
                cooldown_until=0.0,
                now_monotonic=100.0,
                snapshot_present=True,
                snapshot_age_seconds=1200.0,
                refresh_interval_seconds=3600.0,
            ),
            2400.0,
        )
        self.assertEqual(
            next_live_refresh_delay_seconds(
                refresh_status="ready",
                cooldown_until=0.0,
                now_monotonic=100.0,
                snapshot_present=True,
                snapshot_age_seconds=7200.0,
                refresh_interval_seconds=3600.0,
            ),
            1.0,
        )

    def test_live_snapshot_refresh_gate_prefers_cooldown(self):
        self.assertEqual(
            live_snapshot_refresh_gate(
                now_monotonic=100.0,
                cooldown_until=101.0,
                cached_present=True,
                legacy_scope_refresh=False,
                refresh_due=False,
            ),
            "cooldown",
        )

    def test_live_snapshot_refresh_gate_returns_cached_when_still_fresh(self):
        self.assertEqual(
            live_snapshot_refresh_gate(
                now_monotonic=100.0,
                cooldown_until=0.0,
                cached_present=True,
                legacy_scope_refresh=False,
                refresh_due=False,
            ),
            "cached",
        )

    def test_live_snapshot_refresh_gate_refreshes_missing_stale_or_legacy_cache(self):
        cases = [
            {"cached_present": False, "legacy_scope_refresh": False, "refresh_due": False},
            {"cached_present": True, "legacy_scope_refresh": False, "refresh_due": True},
            {"cached_present": True, "legacy_scope_refresh": True, "refresh_due": False},
        ]

        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(
                    live_snapshot_refresh_gate(
                        now_monotonic=100.0,
                        cooldown_until=0.0,
                        **case,
                    ),
                    "refresh",
                )

    def test_rag_ready_status_reports_dense_or_bm25_only(self):
        self.assertEqual(rag_ready_status(types.SimpleNamespace(dense_available=True)), "ready")
        self.assertEqual(rag_ready_status(types.SimpleNamespace(dense_available=False)), "bm25_only")
        self.assertEqual(rag_ready_status(types.SimpleNamespace()), "bm25_only")

    def test_active_snapshot_id_returns_only_non_empty_string_ids(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace(live_snapshot=None))
        self.assertIsNone(active_snapshot_id(app))

        app.state.live_snapshot = {"snapshot_id": ""}
        self.assertIsNone(active_snapshot_id(app))

        app.state.live_snapshot = {"snapshot_id": 123}
        self.assertIsNone(active_snapshot_id(app))

        app.state.live_snapshot = {"snapshot_id": "snapshot-1"}
        self.assertEqual(active_snapshot_id(app), "snapshot-1")

    def test_activate_snapshot_state_updates_runtime_views(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())
        snapshot = {
            "snapshot_id": "snapshot-1",
            "cards_meta": [{"id": "card-1"}],
            "top_decks": [{"rank": 1}],
            "card_deck_stats": {"card-1": {"usage_rate": 0.2}},
        }

        activate_snapshot_state(app, snapshot, now_monotonic=123.5, target_battles=200000)

        self.assertIs(app.state.live_snapshot, snapshot)
        self.assertEqual(app.state.live_snapshot_at, 123.5)
        self.assertEqual(app.state.live_snapshot_target_battles, 200000)
        self.assertEqual(app.state.cards_meta_data, [{"id": "card-1"}])
        self.assertEqual(app.state.top_decks_data, [{"rank": 1}])
        self.assertEqual(app.state.card_deck_stats_data, {"card-1": {"usage_rate": 0.2}})

        snapshot["cards_meta"].append({"id": "card-2"})
        self.assertEqual(app.state.cards_meta_data, [{"id": "card-1"}])

    def test_record_live_refresh_attempt_stores_summary_and_metrics(self):
        calls = []
        metrics = types.SimpleNamespace(record_snapshot_collection=lambda value: calls.append(value))
        app = types.SimpleNamespace(state=types.SimpleNamespace(runtime_metrics=metrics))
        snapshot = {
            "sample_battles": 75,
            "target_battles": 100,
            "collection_metrics": {"request_count": 12},
        }

        record_live_refresh_attempt(
            app,
            status="success",
            default_target_battles=200,
            snapshot=snapshot,
            finished_at="2026-08-15T10:00:00+00:00",
        )

        self.assertEqual(
            app.state.live_last_refresh_attempt,
            {
                "status": "success",
                "finished_at": "2026-08-15T10:00:00+00:00",
                "sample_battles": 75,
                "target_battles": 100,
                "shortfall_battles": 25,
                "collection_metrics": {"request_count": 12},
                "error": None,
            },
        )
        self.assertEqual(calls, [{"request_count": 12}])

    def test_record_live_refresh_attempt_uses_defaults_without_snapshot(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        record_live_refresh_attempt(
            app,
            status="failed",
            default_target_battles=200,
            error="network",
            finished_at="2026-08-15T10:01:00+00:00",
        )

        self.assertEqual(app.state.live_last_refresh_attempt["sample_battles"], 0)
        self.assertEqual(app.state.live_last_refresh_attempt["target_battles"], 200)
        self.assertEqual(app.state.live_last_refresh_attempt["shortfall_battles"], 200)
        self.assertEqual(app.state.live_last_refresh_attempt["collection_metrics"], {})
        self.assertEqual(app.state.live_last_refresh_attempt["error"], "network")

    def test_record_live_collection_progress_copies_progress(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())
        progress = {"usable_battles": 10, "target_battles": 100}

        public_progress = record_live_collection_progress(app, progress)
        progress["usable_battles"] = 11

        self.assertEqual(public_progress, {"usable_battles": 10, "target_battles": 100})
        self.assertEqual(app.state.live_collection_progress, public_progress)


if __name__ == "__main__":
    unittest.main()
