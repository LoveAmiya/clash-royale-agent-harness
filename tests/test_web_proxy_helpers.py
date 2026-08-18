from __future__ import annotations

import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi import HTTPException

from clashroyale_agent.web import proxy_helpers, sse_proxy


class _FakeResponse:
    def __init__(self, status_code: int, body: object = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, *args: object, **kwargs: object) -> _FakeResponse:
        self.calls.append(("get", args, kwargs))
        return self.response

    async def request(self, *args: object, **kwargs: object) -> _FakeResponse:
        self.calls.append(("request", args, kwargs))
        return self.response


class _FakeHttpx:
    class HTTPError(Exception):
        pass

    class ConnectError(HTTPError):
        pass

    def __init__(self, response: _FakeResponse) -> None:
        self.client = _FakeClient(response)
        self.client_kwargs: dict[str, object] | None = None

    def AsyncClient(self, **kwargs: object) -> _FakeClient:
        self.client_kwargs = kwargs
        return self.client


class WebProxyHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_proxy_keeps_success_body_and_client_options(self) -> None:
        httpx_module = _FakeHttpx(_FakeResponse(200, {"ok": True}))

        result = await proxy_helpers.proxy_backend_json(
            "http://backend/ready",
            unavailable="unavailable",
            failed="failed",
            invalid="invalid",
            trust_env=True,
            httpx_module=httpx_module,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(httpx_module.client_kwargs, {"timeout": 15.0, "trust_env": True})
        self.assertEqual(httpx_module.client.calls, [("get", ("http://backend/ready",), {})])

    async def test_structured_proxy_keeps_backend_status_and_invalid_json_envelope(self) -> None:
        httpx_module = _FakeHttpx(_FakeResponse(502, ValueError("invalid json")))

        response = await proxy_helpers.proxy_structured_api(
            "POST",
            "/cards/compare",
            structured_api_base_url="http://backend/api",
            payload={"card_ids": ["fireball"]},
            dataset_scope="7d_all",
            trust_env=False,
            httpx_module=httpx_module,
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.body.decode("utf-8"), '{"error":{"code":"INVALID_BACKEND_RESPONSE","message":"Backend returned invalid JSON.","details":{}}}')
        method, args, kwargs = httpx_module.client.calls[0]
        self.assertEqual((method, args), ("request", ("POST", "http://backend/api/cards/compare")))
        self.assertEqual(kwargs["params"], {"dataset_scope": "7d_all"})

    async def test_json_proxy_keeps_http_error_mapping(self) -> None:
        httpx_module = _FakeHttpx(_FakeResponse(503, {"detail": "backend says no"}))

        with self.assertRaises(HTTPException) as error:
            await proxy_helpers.proxy_backend_json(
                "http://backend/ready",
                unavailable="unavailable",
                failed="failed",
                invalid="invalid",
                trust_env=False,
                httpx_module=httpx_module,
            )

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.detail, "backend says no")

    async def test_request_json_proxy_preserves_method_and_payload(self) -> None:
        httpx_module = _FakeHttpx(_FakeResponse(200, {"saved": True}))

        result = await proxy_helpers.proxy_backend_request_json(
            "PUT",
            "http://backend/settings/live-sample",
            payload={"target_battles": 200000},
            unavailable="unavailable",
            failed="failed",
            invalid="invalid",
            trust_env=False,
            httpx_module=httpx_module,
        )

        self.assertEqual(result, {"saved": True})
        self.assertEqual(
            httpx_module.client.calls,
            [("request", ("PUT", "http://backend/settings/live-sample"), {"json": {"target_battles": 200000}})],
        )


class WebSseProxyTests(unittest.TestCase):
    def test_sse_payload_and_backend_payload_keep_the_existing_contract(self) -> None:
        payload = sse_proxy.build_backend_payload(
            message="compare fireball",
            session_id="session-1",
            user_id="web-user-1",
            intent_hint="meta_analysis_query",
            dataset_scope="7d_all",
            deck_mode="base8",
            entity_mode="base8",
        )

        self.assertEqual(sse_proxy.sse_data({"object": "delta", "delta": "ok"}), 'data: {"object": "delta", "delta": "ok"}\n\n')
        self.assertEqual(payload["session_id"], "session-1")
        self.assertEqual(payload["input"][0]["content"][0]["text"], "compare fireball")
        self.assertEqual(payload["deck_mode"], "base8")
        self.assertEqual(payload["entity_mode"], "base8")
