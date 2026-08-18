"""Runtime dependency wiring for health and observability routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StatusRouteDependencies:
    """Providers and builders used by the public status route group."""

    register_status_routes: Callable[..., None]
    build_health_payload: Callable[..., dict]
    get_readiness_status: Callable[[Any], dict]
    build_model_status_payload: Callable[[Callable[[], dict]], dict]
    get_model_provider_status: Callable[[], dict]
    build_metrics_body: Callable[..., str]
    runtime_metrics_factory: Callable[[], Any]
    render_model_provider_metrics: Callable[[], str]
    runtime_contract_version: Callable[[], str]
    runtime_file: Callable[[], str]
    runtime_role: Callable[[], str]
    supercell_live_data_enabled: Callable[[], bool]
    supercell_api_token: Callable[[], str | None]
    snapshot_auto_follow_enabled: Callable[[], bool]
    external_api_required: Callable[[], bool]
    model_api_configured: Callable[[], bool]
    live_sample_target_battles: Callable[[Any], int]
    process_quota_backend: Callable[[], str]


def register_runtime_status_routes(
    app: Any,
    *,
    dependencies: StatusRouteDependencies,
) -> None:
    """Register status routes while preserving deferred runtime configuration reads."""
    dependencies.register_status_routes(
        app,
        get_health_payload=lambda: dependencies.build_health_payload(
            app,
            runtime_contract_version=dependencies.runtime_contract_version(),
            runtime_file=dependencies.runtime_file(),
            runtime_role=dependencies.runtime_role(),
            supercell_live_data_enabled=dependencies.supercell_live_data_enabled(),
            supercell_api_token=dependencies.supercell_api_token(),
            snapshot_auto_follow_enabled=dependencies.snapshot_auto_follow_enabled(),
            external_api_required=dependencies.external_api_required(),
            model_api_configured=dependencies.model_api_configured(),
            live_sample_target_battles=dependencies.live_sample_target_battles(app),
            process_quota_backend=dependencies.process_quota_backend(),
        ),
        get_readiness_status=lambda: dependencies.get_readiness_status(app),
        get_model_status_payload=lambda: dependencies.build_model_status_payload(
            dependencies.get_model_provider_status
        ),
        get_metrics_body=lambda: dependencies.build_metrics_body(
            app,
            runtime_metrics_factory=dependencies.runtime_metrics_factory,
            render_model_provider_metrics=dependencies.render_model_provider_metrics,
        ),
    )


__all__ = ["StatusRouteDependencies", "register_runtime_status_routes"]
