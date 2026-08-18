"""HTTP proxy primitives shared by browser UI routes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse


async def proxy_backend_json(
    url: str,
    *,
    unavailable: str,
    failed: str,
    invalid: str,
    trust_env: bool,
    httpx_module: Any,
) -> dict:
    try:
        async with httpx_module.AsyncClient(timeout=15.0, trust_env=trust_env) as client:
            response = await client.get(url)
    except httpx_module.ConnectError as exc:
        raise HTTPException(status_code=503, detail=unavailable) from exc
    except httpx_module.HTTPError as exc:
        raise HTTPException(status_code=502, detail=failed) from exc

    try:
        body = response.json()
    except ValueError:
        body = {"detail": invalid}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("detail", failed))
    return body


async def proxy_backend_text(
    url: str,
    *,
    unavailable: str,
    failed: str,
    trust_env: bool,
    httpx_module: Any,
) -> str:
    try:
        async with httpx_module.AsyncClient(timeout=15.0, trust_env=trust_env) as client:
            response = await client.get(url)
    except httpx_module.ConnectError as exc:
        raise HTTPException(status_code=503, detail=unavailable) from exc
    except httpx_module.HTTPError as exc:
        raise HTTPException(status_code=502, detail=failed) from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text or failed)
    return response.text


async def proxy_backend_request_json(
    method: str,
    url: str,
    *,
    payload: dict | None,
    unavailable: str,
    failed: str,
    invalid: str,
    trust_env: bool,
    httpx_module: Any,
) -> dict:
    try:
        async with httpx_module.AsyncClient(timeout=15.0, trust_env=trust_env) as client:
            response = await client.request(method, url, json=payload)
    except httpx_module.ConnectError as exc:
        raise HTTPException(status_code=503, detail=unavailable) from exc
    except httpx_module.HTTPError as exc:
        raise HTTPException(status_code=502, detail=failed) from exc

    try:
        body = response.json()
    except ValueError:
        body = {"detail": invalid}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("detail", failed))
    return body


async def proxy_structured_api(
    method: str,
    path: str,
    *,
    structured_api_base_url: str,
    payload: dict | None = None,
    dataset_scope: str | None = None,
    query_params: dict | None = None,
    trust_env: bool,
    httpx_module: Any,
) -> JSONResponse:
    """Preserve backend structured status codes and error envelopes verbatim."""
    params = dict(query_params or {})
    if dataset_scope is not None:
        params["dataset_scope"] = dataset_scope
    try:
        async with httpx_module.AsyncClient(timeout=15.0, trust_env=trust_env) as client:
            response = await client.request(
                method,
                f"{structured_api_base_url}{path}",
                json=payload,
                params=params or None,
            )
    except httpx_module.ConnectError as exc:
        raise HTTPException(status_code=503, detail="backend structured statistics service is unavailable") from exc
    except httpx_module.HTTPError as exc:
        raise HTTPException(status_code=502, detail="backend structured statistics request failed") from exc
    try:
        body = response.json()
    except ValueError:
        body = {
            "error": {
                "code": "INVALID_BACKEND_RESPONSE",
                "message": "Backend returned invalid JSON.",
                "details": {},
            }
        }
    return JSONResponse(status_code=response.status_code, content=body)


__all__ = [
    "proxy_backend_json",
    "proxy_backend_text",
    "proxy_backend_request_json",
    "proxy_structured_api",
]
