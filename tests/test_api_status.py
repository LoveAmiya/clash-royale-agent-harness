import json
import tempfile
import types
import unittest
from pathlib import Path

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.api.status import (
    build_health_payload,
    build_live_sample_settings_payload,
    build_live_snapshot_data_sources_payload,
    build_live_snapshot_leaderboard_payload,
    build_live_snapshot_rag_payload,
    build_live_snapshot_retention_payload,
    build_live_snapshot_runtime_state,
    build_live_snapshot_status_payload,
    build_metrics_body,
    build_model_status_payload,
    build_rag_alignment_state,
    build_readiness_decision,
    build_readiness_payload,
    build_readiness_response,
    build_readiness_status,
    build_runtime_summary,
    get_snapshot_artifact_status,
    public_rag_validation,
)
from runtime_hardening import RuntimeMetrics


class ApiStatusHelperTests(unittest.TestCase):
    def test_health_payload_preserves_runtime_contract_fields(self):
        quota = types.SimpleNamespace(status=lambda: {"backend": "dummy", "available": True})
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                process_quota=quota,
                api_startup_baseline={"elapsed_seconds": 1.25},
                rag_preheat_baseline={"elapsed_seconds": 2.5, "outcome": "ready"},
            )
        )

        payload = build_health_payload(
            app,
            runtime_contract_version="contract-v1",
            runtime_file=Path("runtime_multi.py"),
            runtime_role="collector",
            supercell_live_data_enabled=True,
            supercell_api_token="configured",
            snapshot_auto_follow_enabled=True,
            external_api_required=True,
            model_api_configured=True,
            live_sample_target_battles=1000,
            process_quota_backend="memory",
        )

        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["runtime_contract_version"], "contract-v1")
        self.assertTrue(payload["live_data_enabled"])
        self.assertTrue(payload["official_collection_enabled"])
        self.assertFalse(payload["snapshot_auto_follow_enabled"])
        self.assertEqual(payload["live_sample_target_battles"], 1000)
        self.assertEqual(payload["quota"], {"backend": "dummy", "available": True})
        self.assertEqual(payload["performance_baseline"]["api_startup"]["elapsed_seconds"], 1.25)
        self.assertEqual(payload["performance_baseline"]["rag_preheat"]["outcome"], "ready")

    def test_health_payload_reports_unavailable_quota_when_missing(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        payload = build_health_payload(
            app,
            runtime_contract_version="contract-v1",
            runtime_file="runtime_multi.py",
            runtime_role="api",
            supercell_live_data_enabled=True,
            supercell_api_token=None,
            snapshot_auto_follow_enabled=True,
            external_api_required=False,
            model_api_configured=False,
            live_sample_target_battles=500,
            process_quota_backend="redis",
        )

        self.assertFalse(payload["live_data_enabled"])
        self.assertTrue(payload["snapshot_auto_follow_enabled"])
        self.assertEqual(payload["quota"], {"backend": "redis", "available": False})

    def test_model_status_payload_uses_sanitized_provider_snapshot(self):
        payload = build_model_status_payload(lambda: {"circuit_state": "closed"})

        self.assertEqual(payload, {"circuit_state": "closed"})

    def test_readiness_response_removes_internal_http_status(self):
        status_code, payload = build_readiness_response(
            {
                "status": "degraded",
                "http_status": 200,
                "blockers": [],
                "degraded_reasons": ["rag_loading"],
            }
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["degraded_reasons"], ["rag_loading"])
        self.assertNotIn("http_status", payload)

    def test_readiness_decision_reports_blockers_before_degraded_reasons(self):
        payload = build_readiness_decision(
            initialized=False,
            quota_available=False,
            quota_fail_mode="closed",
            strict=True,
            model_configured=False,
            model_provider={"circuit_state": "open"},
            snapshot_usable=False,
            snapshot_status="refreshing",
            rag_status="building",
            snapshot_id=None,
            rag_snapshot_id=None,
            rag_alignment={"fingerprint_aligned": False},
        )

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["http_status"], 503)
        self.assertEqual(
            payload["blockers"],
            [
                "runtime_initializing",
                "process_quota_unavailable",
                "model_api_unconfigured",
                "model_provider_circuit_open",
                "official_snapshot_unavailable",
            ],
        )
        self.assertEqual(payload["degraded_reasons"], ["rag_building"])

    def test_readiness_decision_reports_degraded_snapshot_and_rag_alignment(self):
        payload = build_readiness_decision(
            initialized=True,
            quota_available=True,
            quota_fail_mode="closed",
            strict=True,
            model_configured=True,
            model_provider={"circuit_state": "closed"},
            snapshot_usable=True,
            snapshot_status="cooldown",
            rag_status="ready",
            snapshot_id="snapshot-1",
            rag_snapshot_id="snapshot-old",
            rag_alignment={"fingerprint_aligned": False},
        )

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["http_status"], 200)
        self.assertEqual(payload["blockers"], [])
        self.assertEqual(
            payload["degraded_reasons"],
            [
                "snapshot_cooldown",
                "snapshot_rag_misaligned",
                "snapshot_rag_fingerprint_misaligned",
            ],
        )

    def test_readiness_decision_reports_ready_when_all_checks_pass(self):
        payload = build_readiness_decision(
            initialized=True,
            quota_available=True,
            quota_fail_mode="closed",
            strict=True,
            model_configured=True,
            model_provider={"circuit_state": "closed"},
            snapshot_usable=True,
            snapshot_status="ready",
            rag_status="bm25_only",
            snapshot_id="snapshot-1",
            rag_snapshot_id="snapshot-1",
            rag_alignment={"fingerprint_aligned": True},
        )

        self.assertEqual(
            payload,
            {"status": "ready", "http_status": 200, "blockers": [], "degraded_reasons": []},
        )

    def test_readiness_payload_preserves_public_contract_fields(self):
        validation = {
            "snapshot_id": "snapshot-1",
            "document_count": 42,
            "passed": True,
            "invalid_doc_ids": ["hidden-doc"],
        }

        payload = build_readiness_payload(
            initialized=True,
            model_configured=True,
            model_provider={"circuit_state": "closed"},
            quota_status={"backend": "memory", "available": True},
            process_quota_fail_mode="closed",
            strict=True,
            snapshot_status="ready",
            snapshot_usable=True,
            snapshot_id="snapshot-1",
            rag_status="ready",
            rag_snapshot_id="snapshot-1",
            rag_alignment={
                "fingerprint_aligned": True,
                "snapshot_docs_fingerprint": "fp-1",
                "active_docs_fingerprint": "fp-1",
                "index_docs_fingerprint": "fp-1",
            },
            rag_document_validation=validation,
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["http_status"], 200)
        self.assertTrue(payload["snapshot_rag_aligned"])
        self.assertTrue(payload["snapshot_rag_fingerprint_aligned"])
        self.assertEqual(payload["snapshot_docs_fingerprint"], "fp-1")
        self.assertEqual(payload["active_rag_docs_fingerprint"], "fp-1")
        self.assertEqual(payload["index_docs_fingerprint"], "fp-1")
        self.assertEqual(payload["rag_document_validation"]["document_count"], 42)
        self.assertNotIn("invalid_doc_ids", payload["rag_document_validation"])

    def test_readiness_payload_reports_degraded_policy(self):
        payload = build_readiness_payload(
            initialized=True,
            model_configured=True,
            model_provider={"circuit_state": "closed"},
            quota_status={"backend": "memory", "available": True},
            process_quota_fail_mode="closed",
            strict=True,
            snapshot_status="cooldown",
            snapshot_usable=True,
            snapshot_id="snapshot-new",
            rag_status="ready",
            rag_snapshot_id="snapshot-old",
            rag_alignment={
                "fingerprint_aligned": False,
                "snapshot_docs_fingerprint": "fp-new",
                "active_docs_fingerprint": "fp-old",
                "index_docs_fingerprint": "fp-old",
            },
            rag_document_validation=None,
        )

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(
            payload["degraded_reasons"],
            [
                "snapshot_cooldown",
                "snapshot_rag_misaligned",
                "snapshot_rag_fingerprint_misaligned",
            ],
        )
        self.assertFalse(payload["snapshot_rag_aligned"])
        self.assertIsNone(payload["rag_document_validation"])

    def test_readiness_status_reads_runtime_state_and_delegates_payload_contract(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                initialized=True,
                live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fp-1"},
                live_refresh_status="ready",
                rag_status="ready",
                rag_snapshot_id="snapshot-1",
                rag_docs_fingerprint="fp-1",
                rag_document_validation={"document_count": 11, "passed": True},
                retriever=types.SimpleNamespace(docs_fingerprint="fp-1"),
                process_quota=types.SimpleNamespace(status=lambda: {"backend": "memory", "available": True}),
            )
        )

        payload = build_readiness_status(
            app,
            strict=True,
            model_configured=True,
            is_snapshot_usable=lambda snapshot: snapshot["snapshot_id"] == "snapshot-1",
            process_quota_backend="redis",
            process_quota_fail_mode="closed",
            get_model_provider_status=lambda: {"circuit_state": "closed"},
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["quota"], {"backend": "memory", "available": True})
        self.assertTrue(payload["snapshot_rag_aligned"])
        self.assertTrue(payload["snapshot_rag_fingerprint_aligned"])
        self.assertEqual(payload["rag_document_validation"]["document_count"], 11)

    def test_readiness_status_uses_quota_fallback_when_runtime_quota_is_missing(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                initialized=False,
                live_snapshot=None,
                live_refresh_status="missing",
                rag_status="not_required",
            )
        )

        payload = build_readiness_status(
            app,
            strict=False,
            model_configured=False,
            is_snapshot_usable=lambda snapshot: False,
            process_quota_backend="memory",
            process_quota_fail_mode="closed",
            get_model_provider_status=lambda: {"circuit_state": "closed"},
        )

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["quota"], {"backend": "memory", "available": True})
        self.assertEqual(payload["blockers"], ["runtime_initializing"])

    def test_rag_alignment_state_requires_snapshot_and_fingerprint_alignment(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_snapshot={"snapshot_id": "snapshot-1", "rag_docs_fingerprint": "fp-1"},
                rag_snapshot_id="snapshot-1",
                rag_docs_fingerprint="fp-1",
                retriever=types.SimpleNamespace(docs_fingerprint="fp-2"),
            )
        )

        payload = build_rag_alignment_state(app)

        self.assertEqual(payload["snapshot_id"], "snapshot-1")
        self.assertTrue(payload["snapshot_aligned"])
        self.assertFalse(payload["fingerprint_aligned"])
        self.assertFalse(payload["fully_aligned"])

        app.state.retriever.docs_fingerprint = "fp-1"
        payload = build_rag_alignment_state(app)
        self.assertTrue(payload["fingerprint_aligned"])
        self.assertTrue(payload["fully_aligned"])

    def test_public_rag_validation_limits_invalid_document_ids(self):
        report = {
            "schema_version": 1,
            "snapshot_id": "snapshot-1",
            "docs_fingerprint": "abc",
            "document_count": 42,
            "source_counts": {"card": 10},
            "card_documents_checked": 5,
            "deck_documents_checked": 6,
            "matchup_documents_checked": 7,
            "passed": False,
            "failures": ["bad_doc"],
            "invalid_doc_ids": [f"doc-{index}" for index in range(25)],
        }

        payload = public_rag_validation(report)

        self.assertEqual(payload["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["invalid_document_count"], 25)
        self.assertEqual(payload["invalid_doc_ids_sample"], [f"doc-{index}" for index in range(20)])
        self.assertNotIn("invalid_doc_ids", payload)

    def test_snapshot_artifact_status_reads_compact_manifests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot_id = "snapshot-1"
            for folder in ("audit_exports", "structured_stats"):
                manifest_dir = root / folder / snapshot_id
                manifest_dir.mkdir(parents=True)
                (manifest_dir / "manifest.json").write_text(
                    json.dumps({"snapshot_id": snapshot_id, "counts": {"rows": 3}}),
                    encoding="utf-8",
                )
            review_dir = root / "external_reviews" / snapshot_id
            review_dir.mkdir(parents=True)
            (review_dir / "validation_report.json").write_text(
                json.dumps({"passed": True, "snapshot_id": snapshot_id, "document_count": 9, "activation": "ready"}),
                encoding="utf-8",
            )

            payload = get_snapshot_artifact_status(root, snapshot_id)

        self.assertEqual(payload["audit_export"]["status"], "ready")
        self.assertEqual(payload["structured_stats"]["counts"], {"rows": 3})
        self.assertEqual(payload["external_review"]["status"], "validated")
        self.assertEqual(payload["external_review"]["document_count"], 9)

    def test_live_sample_settings_payload_preserves_operator_fields(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(live_refresh_status="cooldown", live_cooldown_until=123.5)
        )

        payload = build_live_sample_settings_payload(
            app,
            target_battles=200000,
            min_target_battles=200000,
            max_target_battles=200000,
            can_update_target=False,
        )

        self.assertEqual(payload["target_battles"], 200000)
        self.assertEqual(payload["min_target_battles"], 200000)
        self.assertEqual(payload["max_target_battles"], 200000)
        self.assertEqual(payload["refresh_status"], "cooldown")
        self.assertFalse(payload["can_update_target"])
        self.assertEqual(payload["cooldown_until"], 123.5)

    def test_live_snapshot_runtime_state_exposes_display_safe_fields(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_refresh_status="cooldown",
                live_cooldown_until=150.25,
                live_collection_progress={"usable_battles": 10},
                live_last_refresh_attempt={"status": "failed"},
                live_error="TimeoutError",
            )
        )

        payload = build_live_snapshot_runtime_state(app, now_monotonic=120.0)

        self.assertEqual(payload["refresh_status"], "cooldown")
        self.assertEqual(payload["collection_progress"], {"usable_battles": 10})
        self.assertEqual(payload["last_refresh_attempt"], {"status": "failed"})
        self.assertEqual(payload["cooldown_remaining_seconds"], 30.2)
        self.assertEqual(payload["error"], "TimeoutError")

    def test_live_snapshot_runtime_state_clamps_elapsed_cooldown(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(live_refresh_status="ready", live_cooldown_until=100.0)
        )

        payload = build_live_snapshot_runtime_state(app, now_monotonic=120.0)

        self.assertEqual(payload["refresh_status"], "ready")
        self.assertEqual(payload["cooldown_remaining_seconds"], 0.0)
        self.assertIsNone(payload["collection_progress"])
        self.assertIsNone(payload["last_refresh_attempt"])
        self.assertIsNone(payload["error"])

    def test_live_snapshot_leaderboard_payload_uses_empty_snapshot_defaults(self):
        payload = build_live_snapshot_leaderboard_payload(
            None,
            default_candidate_limit=1000,
            default_queue_capacity=3000,
        )

        self.assertEqual(
            payload,
            {
                "candidate_limit": 1000,
                "queue_capacity": 3000,
                "rank_start": 1,
                "scanned_rank_end": None,
                "ranked_players_returned": 0,
                "sampled_players": 0,
                "failed_players": 0,
            },
        )

    def test_live_snapshot_leaderboard_payload_preserves_snapshot_coverage(self):
        payload = build_live_snapshot_leaderboard_payload(
            {
                "fetched_players": 811,
                "ranked_players": 1000,
                "sampled_players": 750,
                "failed_players": 2,
                "leaderboard_start_rank": 1,
                "leaderboard_last_scanned_rank": 822,
                "leaderboard_candidate_limit": 3000,
                "collection_metrics": {"player_queue_capacity": 2500},
            },
            default_candidate_limit=1000,
            default_queue_capacity=1000,
        )

        self.assertEqual(payload["candidate_limit"], 3000)
        self.assertEqual(payload["queue_capacity"], 2500)
        self.assertEqual(payload["rank_start"], 1)
        self.assertEqual(payload["scanned_rank_end"], 822)
        self.assertEqual(payload["ranked_players_returned"], 1000)
        self.assertEqual(payload["sampled_players"], 750)
        self.assertEqual(payload["failed_players"], 2)

    def test_live_snapshot_leaderboard_payload_falls_back_to_fetched_players(self):
        payload = build_live_snapshot_leaderboard_payload(
            {"fetched_players": 811},
            default_candidate_limit=1000,
            default_queue_capacity=3000,
        )

        self.assertEqual(payload["candidate_limit"], 1000)
        self.assertEqual(payload["queue_capacity"], 3000)
        self.assertEqual(payload["scanned_rank_end"], 811)

    def test_live_snapshot_rag_payload_uses_disabled_empty_snapshot_status(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                rag_status="ready",
                rag_snapshot_id="snapshot-1",
                rag_quality_report={"passed": True},
                rag_candidate_status="ready",
                rag_candidate_error=None,
            )
        )

        payload = build_live_snapshot_rag_payload(
            app,
            None,
            live_data_enabled=False,
            rag_alignment={"snapshot_aligned": False},
        )

        self.assertEqual(payload["status"], "not_required")
        self.assertEqual(payload["document_counts"], {})
        self.assertEqual(payload["quality"], {"passed": True})
        self.assertFalse(payload["snapshot_aligned"])
        self.assertIsNone(payload["validation"])

    def test_live_snapshot_rag_payload_preserves_active_snapshot_fields(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                rag_status="ready",
                rag_snapshot_id="snapshot-1",
                rag_quality_report={"passed": True},
                rag_document_validation=None,
                rag_candidate_status="warming",
                rag_candidate_error="preheat",
                rag_candidate_validation={"passed": False, "invalid_doc_ids": ["bad-doc"]},
            )
        )
        snapshot = {
            "rag_document_counts": {"card": 7},
            "rag_document_validation": {"passed": True, "invalid_doc_ids": ["ignored"]},
        }

        payload = build_live_snapshot_rag_payload(
            app,
            snapshot,
            live_data_enabled=True,
            rag_alignment={"snapshot_id": "snapshot-1", "fully_aligned": True},
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["document_counts"], {"card": 7})
        self.assertEqual(payload["quality"], {"passed": True})
        self.assertTrue(payload["fully_aligned"])
        self.assertIsNone(payload["validation"])
        self.assertEqual(payload["candidate_status"], "warming")
        self.assertEqual(payload["candidate_error"], "preheat")
        self.assertEqual(payload["candidate_validation"]["invalid_document_count"], 1)

    def test_live_snapshot_retention_payload_preserves_policy_fields(self):
        self.assertEqual(
            build_live_snapshot_retention_payload(days=35, max_complete_snapshots=8),
            {"days": 35, "max_complete_snapshots": 8},
        )

    def test_live_snapshot_data_sources_payload_reports_unavailable_and_official_sources(self):
        self.assertEqual(
            build_live_snapshot_data_sources_payload(snapshot_available=False),
            {
                "schedule": "disabled_clan_war_feature",
                "cards": "not_available",
                "decks": "not_available",
                "rag_documents": "not_available",
            },
        )
        self.assertEqual(
            build_live_snapshot_data_sources_payload(snapshot_available=True),
            {
                "schedule": "disabled_clan_war_feature",
                "cards": "official_weekly_snapshot",
                "decks": "official_weekly_snapshot",
                "rag_documents": "official_weekly_snapshot",
            },
        )

    def test_live_snapshot_status_payload_builds_empty_snapshot_contract(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(rag_status="not_ready", rag_snapshot_id=None)
        )
        runtime_state = {
            "refresh_status": "missing",
            "collection_progress": None,
            "last_refresh_attempt": None,
            "cooldown_remaining_seconds": 0.0,
            "error": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_live_snapshot_status_payload(
                app,
                None,
                runtime_state=runtime_state,
                rag_alignment={"fully_aligned": False},
                live_data_enabled=True,
                daily_target_battles=200000,
                pol_seed_players=1000,
                leaderboard_players=3000,
                refresh_interval_seconds=3600,
                retention_days=35,
                retention_max_complete=8,
                data_dir=Path(temp_dir),
                snapshot_age_seconds=lambda snapshot: 123.0,
                is_scope_verified=lambda snapshot: True,
            )

        self.assertEqual(payload["snapshot_status"], "missing")
        self.assertIsNone(payload["snapshot_id"])
        self.assertEqual(payload["sample_battles"], 0)
        self.assertEqual(payload["target_battles"], 200000)
        self.assertEqual(payload["leaderboard"]["candidate_limit"], 1000)
        self.assertEqual(payload["retention"], {"days": 35, "max_complete_snapshots": 8})
        self.assertEqual(payload["data_sources"]["cards"], "not_available")
        self.assertEqual(payload["rag"]["status"], "not_ready")
        self.assertNotIn("age_seconds", payload)

    def test_live_snapshot_status_payload_builds_active_snapshot_contract(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                rag_status="ready",
                rag_snapshot_id="snapshot-1",
                runtime_metrics=None,
            )
        )
        runtime_state = {
            "refresh_status": "ready",
            "collection_progress": {"usable_battles": 10},
            "last_refresh_attempt": {"status": "success"},
            "cooldown_remaining_seconds": 0.0,
            "error": None,
        }
        snapshot = {
            "snapshot_id": "snapshot-1",
            "fetched_at": "2026-08-15T10:00:00+00:00",
            "published_at": "2026-08-15T10:01:00+00:00",
            "sample_battles": 10,
            "target_battles": 20,
            "shortfall_battles": 0,
            "collection_scope": "path_of_legend_global_top",
            "leaderboard_candidate_limit": 1000,
            "collection_metrics": {"player_queue_capacity": 1500},
            "special_fields_probe": {"tower": True},
            "rag_document_counts": {"card": 7},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_live_snapshot_status_payload(
                app,
                snapshot,
                runtime_state=runtime_state,
                rag_alignment={"fully_aligned": True},
                live_data_enabled=True,
                daily_target_battles=200000,
                pol_seed_players=1000,
                leaderboard_players=3000,
                refresh_interval_seconds=3600,
                retention_days=35,
                retention_max_complete=8,
                data_dir=Path(temp_dir),
                snapshot_age_seconds=lambda snapshot: 42.0,
                is_scope_verified=lambda snapshot: True,
            )

        self.assertEqual(payload["snapshot_status"], "ready")
        self.assertEqual(payload["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["age_seconds"], 42.0)
        self.assertTrue(payload["scope_verified"])
        self.assertEqual(payload["leaderboard"]["queue_capacity"], 1500)
        self.assertEqual(payload["collection_progress"], {"usable_battles": 10})
        self.assertEqual(payload["special_fields_probe"], {"tower": True})
        self.assertEqual(payload["data_sources"]["cards"], "official_weekly_snapshot")
        self.assertEqual(payload["rag"]["document_counts"], {"card": 7})

    def test_runtime_summary_uses_zero_defaults_without_metrics(self):
        app = types.SimpleNamespace(state=types.SimpleNamespace())

        self.assertEqual(
            build_runtime_summary(app),
            {
                "process_requests": 0,
                "successes": 0,
                "failures": 0,
                "cancelled": 0,
                "rate_limited": 0,
                "process_p95_ms": 0.0,
                "sample_size": 0,
            },
        )

    def test_runtime_summary_delegates_to_metrics_registry(self):
        registry = RuntimeMetrics()
        registry.record_process(outcome="success", total_seconds=0.5)
        app = types.SimpleNamespace(state=types.SimpleNamespace(runtime_metrics=registry))

        payload = build_runtime_summary(app)

        self.assertEqual(payload["process_requests"], 1)
        self.assertEqual(payload["successes"], 1)
        self.assertEqual(payload["failures"], 0)

    def test_metrics_body_preserves_runtime_and_model_metrics(self):
        registry = RuntimeMetrics()
        registry.record_http(route="/process", status_code=200, duration_seconds=0.25)
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                live_refresh_status="ready",
                rag_status="ready",
                live_snapshot={"snapshot_id": "snapshot-1"},
                rag_snapshot_id="snapshot-1",
                runtime_metrics=registry,
            )
        )

        body = build_metrics_body(
            app,
            runtime_metrics_factory=RuntimeMetrics,
            render_model_provider_metrics=lambda: "cr_agent_model_provider_open_total 0\n",
        )

        self.assertIn(
            'cr_agent_http_requests_total{route="/process",status_class="2xx"} 1',
            body,
        )
        self.assertIn(
            'cr_agent_runtime_state{snapshot_status="ready",rag_status="ready",snapshot_aligned="true"} 1',
            body,
        )
        self.assertIn("cr_agent_model_provider_open_total 0", body)


if __name__ == "__main__":
    unittest.main()
