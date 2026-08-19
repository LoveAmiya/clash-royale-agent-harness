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
    admin_api_key: Callable[[], str | None]
    authorize_admin: Callable[[str | None, str | None], bool]


def build_status_route_dependencies(dependencies_cls: Any, runtime: dict[str, Any]) -> Any:
    """Bind status-route providers from the runtime compatibility namespace."""
    return dependencies_cls(
        register_status_routes=runtime["register_status_routes"],
        build_health_payload=runtime["build_health_payload"],
        get_readiness_status=runtime["get_readiness_status"],
        build_model_status_payload=runtime["build_model_status_payload"],
        get_model_provider_status=runtime["get_model_provider_status"],
        build_metrics_body=runtime["build_metrics_body"],
        runtime_metrics_factory=runtime["RuntimeMetrics"],
        render_model_provider_metrics=runtime["render_model_provider_metrics"],
        runtime_contract_version=lambda: runtime["RUNTIME_CONTRACT_VERSION"],
        runtime_file=lambda: runtime["__file__"],
        runtime_role=lambda: runtime["RUNTIME_ROLE"],
        supercell_live_data_enabled=lambda: runtime["SUPERCELL_LIVE_DATA_ENABLED"],
        supercell_api_token=lambda: runtime["SUPERCELL_API_TOKEN"],
        snapshot_auto_follow_enabled=lambda: runtime["SNAPSHOT_AUTO_FOLLOW_ENABLED"],
        external_api_required=lambda: runtime["EXTERNAL_API_REQUIRED"],
        model_api_configured=lambda: bool(runtime["os"].getenv("OPENAI_API_KEY")),
        live_sample_target_battles=runtime["get_live_sample_target"],
        process_quota_backend=lambda: runtime["PROCESS_QUOTA_BACKEND"],
        admin_api_key=lambda: runtime["ADMIN_API_KEY"],
        authorize_admin=runtime["authorize_admin"],
    )


def build_live_snapshot_status_from_runtime(app: Any, runtime: dict[str, Any]) -> dict:
    """Build display-safe live snapshot status from runtime-owned providers."""
    snapshot = getattr(app.state, "live_snapshot", None)
    runtime_state = runtime["build_live_snapshot_runtime_state"](app, now_monotonic=runtime["time"].monotonic())
    return runtime["build_live_snapshot_status_payload"](
        app,
        snapshot,
        runtime_state=runtime_state,
        rag_alignment=runtime["_rag_alignment_state"](app),
        live_data_enabled=runtime["SUPERCELL_LIVE_DATA_ENABLED"],
        daily_target_battles=runtime["DAILY_TARGET_BATTLES"],
        pol_seed_players=runtime["SUPERCELL_POL_SEED_PLAYERS"],
        leaderboard_players=runtime["SUPERCELL_LEADERBOARD_PLAYERS"],
        refresh_interval_seconds=int(runtime["DAILY_REFRESH_INTERVAL"].total_seconds()),
        retention_days=runtime["SNAPSHOT_RETENTION_DAYS"],
        retention_max_complete=runtime["SNAPSHOT_RETENTION_MAX_COMPLETE"],
        data_dir=runtime["DATA_DIR"],
        snapshot_age_seconds=runtime["snapshot_age_seconds"],
        is_scope_verified=runtime["is_path_of_legend_snapshot"],
    )


def build_readiness_status_from_runtime(
    app: Any,
    runtime: dict[str, Any],
    *,
    external_api_required: bool | None = None,
    model_api_configured: bool | None = None,
) -> dict:
    """Build readiness status while deferring mutable runtime configuration reads."""
    strict = runtime["EXTERNAL_API_REQUIRED"] if external_api_required is None else bool(external_api_required)
    model_configured = (
        bool(runtime["os"].getenv("OPENAI_API_KEY"))
        if model_api_configured is None
        else bool(model_api_configured)
    )
    return runtime["build_readiness_status"](
        app,
        strict=strict,
        model_configured=model_configured,
        is_snapshot_usable=runtime["is_complete_daily_snapshot"],
        process_quota_backend=runtime["PROCESS_QUOTA_BACKEND"],
        process_quota_fail_mode=runtime["PROCESS_QUOTA_FAIL_MODE"],
        get_model_provider_status=runtime["get_model_provider_status"],
    )


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
        admin_api_key=dependencies.admin_api_key,
        authorize_admin=dependencies.authorize_admin,
    )


__all__ = [
    "StatusRouteDependencies",
    "build_live_snapshot_status_from_runtime",
    "build_readiness_status_from_runtime",
    "build_status_route_dependencies",
    "register_runtime_status_routes",
]
