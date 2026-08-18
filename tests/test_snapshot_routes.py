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


if __name__ == "__main__":
    unittest.main()
