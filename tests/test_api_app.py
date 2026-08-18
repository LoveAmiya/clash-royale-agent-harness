import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from clashroyale_agent.api.app import create_runtime_app
from clashroyale_agent.ops.runtime_hardening import RequestBodyLimitMiddleware


class _StructuredError(Exception):
    status_code = 409

    def response(self):
        return {"error": "structured_boundary"}


class _Metrics:
    def __init__(self):
        self.calls = []

    def record_http(self, *, route, status_code, duration_seconds):
        self.calls.append((route, status_code, duration_seconds >= 0))


class RuntimeAppFactoryTests(unittest.TestCase):
    def test_create_runtime_app_installs_shared_runtime_boundaries(self):
        app = create_runtime_app(
            title="Test Runtime",
            lifespan=None,
            allowed_origins=("http://ui.local",),
            request_body_limit_middleware_class=RequestBodyLimitMiddleware,
            max_request_body_bytes=5,
            normalize_request_id=lambda value: value or "generated",
            structured_error_type=_StructuredError,
            structured_error_response=lambda exc: JSONResponse(
                status_code=exc.status_code,
                content=exc.response(),
            ),
        )
        app.state.runtime_metrics = _Metrics()

        @app.get("/ok")
        async def ok():
            return {"ok": True}

        @app.get("/structured-error")
        async def structured_error():
            raise _StructuredError()

        client = TestClient(app)
        try:
            response = client.get("/ok", headers={"X-Request-ID": "request-1"})
            error_response = client.get("/structured-error")
            oversized = client.post(
                "/ok",
                content=b"abcdef",
                headers={"X-Request-ID": "request-2"},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "request-1")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
        self.assertEqual(error_response.status_code, 409)
        self.assertEqual(error_response.json(), {"error": "structured_boundary"})
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.headers["X-Request-ID"], "request-2")
        self.assertIn(("/ok", 200, True), app.state.runtime_metrics.calls)
        self.assertIn(("/structured-error", 409, True), app.state.runtime_metrics.calls)


if __name__ == "__main__":
    unittest.main()
