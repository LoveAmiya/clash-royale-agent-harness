"""Snapshot status route registration helpers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI

from clashroyale_agent.api.admin import build_admin_dependency


def register_snapshot_status_routes(
    app: FastAPI,
    *,
    get_snapshot_status_payload: Callable[[], dict],
    admin_api_key: str | None | Callable[[], str | None] = None,
    authorize_admin: Callable[[str | None, str | None], bool] | None = None,
) -> None:
    """Register snapshot status routes on an app."""
    require_admin = build_admin_dependency(
        admin_api_key=admin_api_key,
        authorize_admin=authorize_admin,
    )

    @app.get("/snapshot/status")
    async def get_snapshot_status_endpoint(_admin: None = Depends(require_admin)):
        return get_snapshot_status_payload()


__all__ = ["register_snapshot_status_routes"]
