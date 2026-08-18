"""FastAPI application factory for the runtime boundary.

The active entry point still lives in runtime_multi.py during the migration.
This module owns the shared app-level wiring so route, startup, and SSE splits
can keep using one behavior-preserving app factory.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def create_runtime_app(
    *,
    title: str,
    lifespan: Callable | None,
    allowed_origins: tuple[str, ...] | list[str],
    request_body_limit_middleware_class: type,
    max_request_body_bytes: int,
    normalize_request_id: Callable[[object], str],
    structured_error_type: type[Exception] | None = None,
    structured_error_response: Callable[[Exception], Any] | None = None,
) -> FastAPI:
    """Create the API app with the runtime's shared HTTP boundaries installed."""
    app = FastAPI(title=title, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-Request-ID", "X-Admin-Key"],
    )

    @app.middleware("http")
    async def runtime_protection_middleware(request: Request, call_next):
        """Attach correlation, metrics, and browser security headers."""
        request_id = getattr(request.state, "request_id", None) or normalize_request_id(
            request.headers.get("X-Request-ID")
        )
        request.state.request_id = request_id
        metrics = getattr(app.state, "runtime_metrics", None)
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            if metrics is not None:
                metrics.record_http(
                    route=request.url.path,
                    status_code=500,
                    duration_seconds=time.perf_counter() - started_at,
                )
            raise
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if metrics is not None:
            metrics.record_http(
                route=request.url.path,
                status_code=response.status_code,
                duration_seconds=time.perf_counter() - started_at,
            )
        return response

    # Add after the BaseHTTP middleware so raw ASGI body bytes are bounded
    # before Starlette or Pydantic consume them, including requests without
    # Content-Length.
    app.add_middleware(request_body_limit_middleware_class, max_body_bytes=max_request_body_bytes)

    if structured_error_type is not None:

        @app.exception_handler(structured_error_type)
        async def structured_query_error_handler(_request: Request, exc: Exception):
            if structured_error_response is not None:
                return structured_error_response(exc)
            return JSONResponse(
                status_code=getattr(exc, "status_code", 500),
                content=exc.response(),
            )

    return app


__all__ = ["create_runtime_app"]
