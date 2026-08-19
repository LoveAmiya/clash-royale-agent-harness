import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clashroyale_agent.api.status_routes import register_status_routes


class StatusRouteRegistrationTests(unittest.TestCase):
    def test_register_status_routes_preserves_public_status_contracts(self):
        app = FastAPI()
        calls = []

        register_status_routes(
            app,
            get_health_payload=lambda: {"status": "healthy"},
            get_readiness_status=lambda: {
                "status": "degraded",
                "http_status": 200,
                "blockers": [],
                "degraded_reasons": ["rag_loading"],
            },
            get_model_status_payload=lambda: {"circuit_state": "closed"},
            get_metrics_body=lambda: "cr_agent_runtime_state 1\n",
        )

        client = TestClient(app)
        try:
            health = client.get("/health")
            ready = client.get("/ready")
            model = client.get("/model/status")
            metrics = client.get("/metrics")
        finally:
            client.close()

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "healthy"})
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(
            ready.json(),
            {
                "status": "degraded",
                "blockers": [],
                "degraded_reasons": ["rag_loading"],
            },
        )
        self.assertEqual(model.status_code, 200)
        self.assertEqual(model.json(), {"circuit_state": "closed"})
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("text/plain", metrics.headers["content-type"])
        self.assertEqual(metrics.text, "cr_agent_runtime_state 1\n")
        self.assertEqual(calls, [])

    def test_register_status_routes_require_admin_when_key_is_configured(self):
        app = FastAPI()

        register_status_routes(
            app,
            get_health_payload=lambda: {"status": "healthy"},
            get_readiness_status=lambda: {
                "status": "ready",
                "http_status": 200,
                "blockers": [],
                "degraded_reasons": [],
            },
            get_model_status_payload=lambda: {"circuit_state": "closed"},
            get_metrics_body=lambda: "cr_agent_runtime_state 1\n",
            admin_api_key=lambda: "secret",
            authorize_admin=lambda expected, actual: expected == "secret" and actual == "secret",
        )

        client = TestClient(app)
        try:
            self.assertEqual(client.get("/health").status_code, 200)
            for path in ("/ready", "/model/status", "/metrics"):
                with self.subTest(path=path):
                    denied = client.get(path)
                    accepted = client.get(path, headers={"X-Admin-Key": "secret"})
                    self.assertEqual(denied.status_code, 401)
                    self.assertEqual(denied.json()["detail"], "administrator credentials required")
                    self.assertEqual(accepted.status_code, 200)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
