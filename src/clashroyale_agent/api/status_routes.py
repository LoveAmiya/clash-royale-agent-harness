"""Status and observability route registration helpers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from clashroyale_agent.api.admin import build_admin_dependency
from clashroyale_agent.api.status import build_readiness_response


def register_status_routes(
    app: FastAPI,
    *,
    get_health_payload: Callable[[], dict],
    get_readiness_status: Callable[[], dict],
    get_model_status_payload: Callable[[], dict],
    get_metrics_body: Callable[[], str],
    admin_api_key: str | None | Callable[[], str | None] = None,
    authorize_admin: Callable[[str | None, str | None], bool] | None = None,
) -> None:
    """Register health, readiness, model status, and metrics routes on an app."""
    require_admin = build_admin_dependency(
        admin_api_key=admin_api_key,
        authorize_admin=authorize_admin,
    )

    @app.get("/health")
    async def health():
        return get_health_payload()

    @app.get("/ready")
    async def ready(_admin: None = Depends(require_admin)):
        status_code, payload = build_readiness_response(get_readiness_status())
        return JSONResponse(status_code=status_code, content=payload)

    @app.get("/model/status")
    async def model_status(_admin: None = Depends(require_admin)):
        """Expose sanitized provider health and detected capabilities."""
        return get_model_status_payload()

    @app.get("/metrics")
    async def metrics(_admin: None = Depends(require_admin)):
        return PlainTextResponse(
            get_metrics_body(),
            media_type="text/plain; version=0.0.4",
        )


__all__ = ["register_status_routes"]
