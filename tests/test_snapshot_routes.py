import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clashroyale_agent.api.snapshot_routes import register_snapshot_status_routes


class SnapshotRouteRegistrationTests(unittest.TestCase):
    def test_register_snapshot_status_routes_preserves_status_payload_contract(self):
        app = FastAPI()

        register_snapshot_status_routes(
            app,
            get_snapshot_status_payload=lambda: {
                "snapshot_status": "ready",
                "snapshot_id": "official-test",
                "rag_status": "ready",
            },
        )

        client = TestClient(app)
        try:
            response = client.get("/snapshot/status")
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "snapshot_status": "ready",
                "snapshot_id": "official-test",
                "rag_status": "ready",
            },
        )

    def test_register_snapshot_status_routes_require_admin_when_key_is_configured(self):
        app = FastAPI()

        register_snapshot_status_routes(
            app,
            get_snapshot_status_payload=lambda: {
                "snapshot_status": "ready",
                "snapshot_id": "official-test",
                "rag_status": "ready",
            },
            admin_api_key=lambda: "secret",
            authorize_admin=lambda expected, actual: expected == "secret" and actual == "secret",
        )

        client = TestClient(app)
        try:
            denied = client.get("/snapshot/status")
            accepted = client.get("/snapshot/status", headers={"X-Admin-Key": "secret"})
        finally:
            client.close()

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["detail"], "administrator credentials required")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["snapshot_id"], "official-test")


if __name__ == "__main__":
    unittest.main()
