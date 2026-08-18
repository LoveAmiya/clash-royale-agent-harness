import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from clashroyale_agent.api.process_routes import register_process_routes
from clashroyale_agent.api.schemas import ProcessRequest


class ProcessRouteRegistrationTests(unittest.TestCase):
    def test_register_process_routes_preserves_request_and_body_contract(self):
        app = FastAPI()
        calls = []

        async def process_endpoint(request: Request, payload: ProcessRequest | None = None):
            calls.append(
                {
                    "path": request.url.path,
                    "dataset_scope": payload.dataset_scope if payload is not None else None,
                    "input": payload.input if payload is not None else None,
                }
            )
            return {"status": "accepted", "dataset_scope": payload.dataset_scope}

        register_process_routes(app, process_endpoint=process_endpoint)

        client = TestClient(app)
        try:
            response = client.post(
                "/process",
                json={
                    "dataset_scope": "7d_all",
                    "input": [{"role": "user", "content": "test question"}],
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "accepted", "dataset_scope": "7d_all"})
        self.assertEqual(
            calls,
            [
                {
                    "path": "/process",
                    "dataset_scope": "7d_all",
                    "input": [{"role": "user", "content": "test question"}],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
