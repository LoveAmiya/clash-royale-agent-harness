"""Administrator access helpers for operational API routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Header, HTTPException


def _resolve_config(value: Any) -> Any:
    return value() if callable(value) else value


def build_admin_dependency(
    *,
    admin_api_key: str | None | Callable[[], str | None] = None,
    authorize_admin: Callable[[str | None, str | None], bool] | None = None,
) -> Callable[..., Any]:
    """Build a FastAPI dependency that requires X-Admin-Key when configured."""

    async def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        expected_key = _resolve_config(admin_api_key)
        if not expected_key:
            return None
        checker = authorize_admin or (lambda expected, provided: expected == provided)
        if not checker(expected_key, x_admin_key):
            raise HTTPException(status_code=401, detail="administrator credentials required")
        return None

    return require_admin


__all__ = ["build_admin_dependency"]
