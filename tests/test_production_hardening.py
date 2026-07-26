import asyncio
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from support import install_test_stubs

install_test_stubs()

from runtime_events import RuntimeEventEmitter
from runtime_hardening import ProcessQuota, RuntimeMetrics, authorize_admin, normalize_request_id, redact_for_client
from query_answering import AnswerResult
import runtime_multi
import web_app


class ProductionHardeningUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_quota_rejects_concurrent_and_rate_limited_requests(self):
        quota = ProcessQuota(max_concurrent=1, requests_per_minute=1)

        first = await quota.try_acquire("127.0.0.1")
        self.assertTrue(first.allowed)

        concurrent = await quota.try_acquire("127.0.0.2")
        self.assertFalse(concurrent.allowed)
        self.assertEqual(concurrent.reason, "concurrency")

        quota.release()
        rate_limited = await quota.try_acquire("127.0.0.1")
        self.assertFalse(rate_limited.allowed)
        self.assertEqual(rate_limited.reason, "rate_limit")

    async def test_event_emitter_propagates_request_id_without_breaking_content_contract(self):
        emitter = RuntimeEventEmitter(request_id="req-123")
        await emitter.execution(step_id="parse", phase="parse", status="running", title="parse", detail="validated stage")
        await emitter.content("answer", delta=True)

        execution = await emitter.next_event()
        content = await emitter.next_event()
        self.assertEqual(execution["request_id"], "req-123")
        self.assertEqual(content["request_id"], "req-123")
        self.assertEqual(content["text"], "answer")
        self.assertTrue(content["delta"])

    async def test_admin_access_requires_a_configured_matching_key(self):
        self.assertFalse(authorize_admin(None, "provided"))
        self.assertFalse(authorize_admin("expected", None))
        self.assertFalse(authorize_admin("expected", "wrong"))
        self.assertTrue(authorize_admin("expected", "expected"))

    async def test_request_id_rejects_unbounded_client_supplied_values(self):
        self.assertEqual(normalize_request_id("safe-request_1"), "safe-request_1")
        generated = normalize_request_id("x" * 100)
        self.assertNotEqual(generated, "x" * 100)
        self.assertLessEqual(len(generated), 64)

    async def test_client_redaction_recursively_removes_credential_shaped_metadata(self):
        redacted = redact_for_client({"api_key": "private", "nested": {"token": "private"}, "public": "ok"})
        self.assertEqual(redacted["api_key"], "[redacted]")
        self.assertEqual(redacted["nested"]["token"], "[redacted]")
        self.assertEqual(redacted["public"], "ok")

    async def test_readiness_is_unavailable_without_a_strict_snapshot_and_degraded_while_rag_builds(self):
        unavailable_app = types.SimpleNamespace(
            state=types.SimpleNamespace(initialized=True, live_snapshot=None, live_refresh_status="refreshing", rag_status="not_ready")
        )
        unavailable = runtime_multi.get_readiness_status(
            unavailable_app,
            external_api_required=True,
            model_api_configured=True,
        )
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["http_status"], 503)

        ready_snapshot = {
            "snapshot_id": "official-1",
            "sample_battles": 20_000,
            "target_battles": 20_000,
            "shortfall_battles": 0,
            "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
        }
        degraded_app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                initialized=True,
                live_snapshot=ready_snapshot,
                live_refresh_status="ready",
                rag_status="building",
                rag_snapshot_id=None,
                live_error=None,
            )
        )
        degraded = runtime_multi.get_readiness_status(
            degraded_app,
            external_api_required=True,
            model_api_configured=True,
        )
        self.assertEqual(degraded["status"], "degraded")
        self.assertEqual(degraded["http_status"], 200)

        for refresh_status in ("refreshing", "cooldown", "stale"):
            degraded_app.state.live_refresh_status = refresh_status
            degraded_app.state.rag_status = "ready"
            degraded_app.state.rag_snapshot_id = "official-1"
            result = runtime_multi.get_readiness_status(
                degraded_app,
                external_api_required=True,
                model_api_configured=True,
            )
            self.assertEqual(result["status"], "degraded")
            self.assertIn(f"snapshot_{refresh_status}", result["degraded_reasons"])

        degraded_app.state.live_refresh_status = "ready"
        degraded_app.state.rag_snapshot_id = "official-previous"
        misaligned = runtime_multi.get_readiness_status(
            degraded_app,
            external_api_required=True,
            model_api_configured=True,
        )
        self.assertEqual(misaligned["status"], "degraded")
        self.assertIn("snapshot_rag_misaligned", misaligned["degraded_reasons"])

    async def test_public_web_ui_hides_the_fixed_sample_control(self):
        self.assertIn(
            '<label id="sampleControl" class="sample-control" for="sampleTarget" hidden>',
            web_app.HTML_PAGE,
        )
        self.assertNotIn('<option value="400"', web_app.HTML_PAGE)

    async def test_external_api_unavailable_result_has_an_explicit_model_stream_state(self):
        result = runtime_multi.build_external_api_unavailable_result(
            {"intent": "card_query"},
            "external API unavailable",
            {"status": "unavailable"},
        )

        self.assertEqual(result.metadata["model_stream"], "unavailable")

    async def test_metrics_render_without_unbounded_request_labels(self):
        metrics = RuntimeMetrics()
        metrics.record_http(route="/process", status_code=200, duration_seconds=0.25)
        metrics.record_process(outcome="success", total_seconds=0.5, first_execution_seconds=0.01, first_content_seconds=0.2)
        metrics.record_model_stream("streaming", first_content_seconds=0.2, total_seconds=0.5)
        rendered = metrics.render_prometheus(snapshot_status="ready", rag_status="ready", snapshot_aligned=True)

        self.assertIn('cr_agent_http_requests_total{route="/process",status_class="2xx"} 1', rendered)
        self.assertIn('cr_agent_process_requests_total{outcome="success"} 1', rendered)
        self.assertIn('cr_agent_model_stream_first_content_seconds_count{mode="streaming"} 1', rendered)
        self.assertIn('cr_agent_model_stream_duration_seconds_sum{mode="streaming"} 0.500000', rendered)
        self.assertNotIn("request_id", rendered)

    async def test_sse_disconnect_cancels_answer_task_and_releases_quota(self):
        quota = ProcessQuota(max_concurrent=1, requests_per_minute=10)
        runtime_multi.app.state.process_quota = quota
        runtime_multi.app.state.runtime_metrics = RuntimeMetrics()
        cancelled = asyncio.Event()

        async def slow_answer(_user_text, _app, event_sink=None, request_id=None):
            await event_sink.execution(
                step_id="parse",
                phase="parse",
                status="running",
                title="parse",
                detail="validated stage",
            )
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        payload = runtime_multi.ProcessRequest(
            session_id="disconnect-test",
            input=[{"role": "user", "content": [{"type": "text", "text": "test"}]}],
        )
        with patch.object(runtime_multi, "build_answer", slow_answer):
            response = await runtime_multi.process(payload)
            iterator = response.body_iterator
            for _ in range(4):
                await anext(iterator)
            await iterator.aclose()

        await asyncio.wait_for(cancelled.wait(), timeout=1)
        self.assertEqual(quota.in_flight, 0)
        self.assertEqual(runtime_multi.app.state.runtime_metrics.public_summary()["cancelled"], 1)


class ProductionHardeningHTTPContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        cls.client = TestClient(runtime_multi.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def setUp(self):
        runtime_multi.app.state.runtime_metrics = RuntimeMetrics()
        runtime_multi.app.state.process_quota = ProcessQuota(max_concurrent=1, requests_per_minute=1)

    def _payload(self, question="Electro Giant usage rate"):
        return {"session_id": "test-session", "input": [{"role": "user", "content": [{"type": "text", "text": question}]}]}

    def _answer_result(self):
        return AnswerResult(
            answer="grounded answer",
            trace_id="trace-test",
            parsed={"intent": "card_query"},
            plan={"plan_type": "direct"},
            selected_skill="CardMetaSkill",
            mode="skill_executor",
            metadata={
                "model_stream": "fallback_chunked",
                "api_key": "trace-only-secret",
                "live_data": {"collection_metrics": {"request_count": 2}},
            },
        )

    def test_health_ready_and_metrics_have_runtime_contract_headers(self):
        health = self.client.get("/health", headers={"X-Request-ID": "contract-1"})
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.headers["x-request-id"], "contract-1")
        self.assertEqual(health.headers["x-content-type-options"], "nosniff")

        ready = self.client.get("/ready")
        self.assertIn(ready.status_code, {200, 503})
        self.assertIn(ready.json()["status"], {"ready", "degraded", "unavailable"})

        metrics = self.client.get("/metrics")
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("cr_agent_runtime_state", metrics.text)

    def test_process_sse_includes_request_id_and_enforces_rate_limit(self):
        with patch.object(runtime_multi, "build_answer", AsyncMock(return_value=self._answer_result())):
            first = self.client.post("/process", json=self._payload(), headers={"X-Request-ID": "sse-contract"})
            second = self.client.post("/process", json=self._payload())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["x-request-id"], "sse-contract")
        self.assertIn('"request_id": "sse-contract"', first.text)
        self.assertIn('"object": "trace"', first.text)
        self.assertNotIn("trace-only-secret", first.text)
        self.assertIn('"api_key": "[redacted]"', first.text)
        events = [json.loads(line.removeprefix("data: ")) for line in first.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(
            [event["object"] for event in events],
            ["response", "message", "progress", "progress", "content", "trace", "message", "response"],
        )
        self.assertEqual(second.status_code, 429)
        self.assertIn("retry-after", second.headers)

    def test_process_rejects_an_oversized_question_before_model_execution(self):
        oversized = "x" * (runtime_multi.MAX_QUERY_CHARS + 1)
        with patch.object(runtime_multi, "build_answer", AsyncMock()) as build_answer:
            response = self.client.post("/process", json=self._payload(oversized))

        self.assertEqual(response.status_code, 413)
        build_answer.assert_not_awaited()

    def test_live_settings_require_an_admin_key_when_enabled(self):
        with patch.object(runtime_multi, "LIVE_SAMPLE_SETTINGS_ADMIN_ENABLED", True), patch.object(
            runtime_multi, "ADMIN_API_KEY", "expected-key"
        ):
            denied = self.client.put("/settings/live-sample", json={"target_battles": 20000})
            accepted = self.client.put(
                "/settings/live-sample",
                json={"target_battles": 20000},
                headers={"X-Admin-Key": "expected-key"},
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 409)


if __name__ == "__main__":
    unittest.main()
