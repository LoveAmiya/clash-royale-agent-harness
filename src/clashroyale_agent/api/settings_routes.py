"""Live-sample settings route registration helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException

from clashroyale_agent.api.schemas import LiveSampleSettingsRequest


def _resolve_config(value):
    return value() if callable(value) else value


def register_live_sample_settings_routes(
    app: FastAPI,
    *,
    get_settings_payload: Callable[[], dict],
    configure_target: Callable[[int], dict],
    refresh_live_snapshot_once: Callable[[], Awaitable[object]],
    live_sample_settings_admin_enabled: bool | Callable[[], bool],
    admin_api_key: str | None | Callable[[], str | None],
    authorize_admin: Callable[[str | None, str | None], bool],
) -> None:
    """Register the live-sample settings routes on an app."""

    @app.get("/settings/live-sample")
    async def get_live_sample_settings_endpoint():
        return get_settings_payload()

    @app.put("/settings/live-sample")
    async def update_live_sample_settings(
        request: LiveSampleSettingsRequest,
        x_admin_key: str | None = Header(default=None),
    ):
        admin_enabled = bool(_resolve_config(live_sample_settings_admin_enabled))
        expected_admin_key = _resolve_config(admin_api_key)
        if not admin_enabled:
            raise HTTPException(
                status_code=403,
                detail="live sample target updates are restricted to administrators",
            )
        if not authorize_admin(expected_admin_key, x_admin_key):
            raise HTTPException(
                status_code=401 if expected_admin_key else 403,
                detail="administrator credentials required",
            )
        settings = configure_target(request.target_battles)
        asyncio.create_task(refresh_live_snapshot_once())
        return settings


__all__ = ["register_live_sample_settings_routes"]
