"""Snapshot status route registration helpers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI


def register_snapshot_status_routes(
    app: FastAPI,
    *,
    get_snapshot_status_payload: Callable[[], dict],
) -> None:
    """Register snapshot status routes on an app."""

    @app.get("/snapshot/status")
    async def get_snapshot_status_endpoint():
        return get_snapshot_status_payload()


__all__ = ["register_snapshot_status_routes"]
