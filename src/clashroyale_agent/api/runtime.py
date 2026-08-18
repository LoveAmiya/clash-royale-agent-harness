"""Application assembly for the compatibility runtime entry point."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from clashroyale_agent.api.app import create_runtime_app
from clashroyale_agent.api.feedback_routes import register_feedback_routes
from clashroyale_agent.api.settings_routes import register_live_sample_settings_routes
from clashroyale_agent.api.snapshot_routes import register_snapshot_status_routes
from clashroyale_agent.api.status_runtime import register_runtime_status_routes
from clashroyale_agent.api.structured_routes import register_structured_api_routes


@dataclass(frozen=True)
class RuntimeAppDependencies:
    """Runtime-owned configuration and callbacks used to assemble the API app."""

    title: str
    lifespan: Callable[..., Any]
    allowed_origins: Any
    request_body_limit_middleware_class: type
    max_request_body_bytes: int
    normalize_request_id: Callable[[object], str]
    structured_error_type: type[Exception]
    status_dependencies: Any
    settings_route_options: Callable[[Any], Mapping[str, Any]]
    snapshot_route_options: Callable[[Any], Mapping[str, Any]]
    structured_route_options: Callable[[Any], Mapping[str, Any]]
    feedback_route_options: Callable[[Any], Mapping[str, Any]]


def create_registered_runtime_app(*, dependencies: RuntimeAppDependencies) -> Any:
    """Create the FastAPI app and register all non-process runtime route groups."""
    app = create_runtime_app(
        title=dependencies.title,
        lifespan=dependencies.lifespan,
        allowed_origins=dependencies.allowed_origins,
        request_body_limit_middleware_class=dependencies.request_body_limit_middleware_class,
        max_request_body_bytes=dependencies.max_request_body_bytes,
        normalize_request_id=dependencies.normalize_request_id,
        structured_error_type=dependencies.structured_error_type,
    )
    register_runtime_status_routes(app, dependencies=dependencies.status_dependencies)
    register_live_sample_settings_routes(app, **dependencies.settings_route_options(app))
    register_snapshot_status_routes(app, **dependencies.snapshot_route_options(app))
    register_structured_api_routes(app, **dependencies.structured_route_options(app))
    register_feedback_routes(app, **dependencies.feedback_route_options(app))
    return app


__all__ = ["RuntimeAppDependencies", "create_registered_runtime_app"]
