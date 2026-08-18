"""Status and observability route registration helpers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from clashroyale_agent.api.status import build_readiness_response


def register_status_routes(
    app: FastAPI,
    *,
    get_health_payload: Callable[[], dict],
    get_readiness_status: Callable[[], dict],
    get_model_status_payload: Callable[[], dict],
    get_metrics_body: Callable[[], str],
) -> None:
    """Register health, readiness, model status, and metrics routes on an app."""

    @app.get("/health")
    async def health():
        return get_health_payload()

    @app.get("/ready")
    async def ready():
        status_code, payload = build_readiness_response(get_readiness_status())
        return JSONResponse(status_code=status_code, content=payload)

    @app.get("/model/status")
    async def model_status():
        """Expose sanitized provider health and detected capabilities."""
        return get_model_status_payload()

    @app.get("/metrics")
    async def metrics():
        return PlainTextResponse(
            get_metrics_body(),
            media_type="text/plain; version=0.0.4",
        )


__all__ = ["register_status_routes"]
