"""Status payload helpers for runtime health and observability routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def build_health_payload(
    app: Any,
    *,
    runtime_contract_version: str,
    runtime_file: str | Path,
    runtime_role: str,
    supercell_live_data_enabled: bool,
    supercell_api_token: str | None,
    snapshot_auto_follow_enabled: bool,
    external_api_required: bool,
    model_api_configured: bool,
    live_sample_target_battles: int,
    process_quota_backend: str,
) -> dict:
    """Build the public /health payload without binding it to FastAPI routes."""
    quota = getattr(app.state, "process_quota", None)
    return {
        "status": "healthy",
        "runtime_contract_version": runtime_contract_version,
        "runtime_file": str(Path(runtime_file).resolve()),
        "runtime_role": runtime_role,
        "live_data_enabled": (
            runtime_role in {"all", "collector"}
            and supercell_live_data_enabled
            and bool(supercell_api_token)
        ),
        "official_collection_enabled": runtime_role in {"all", "collector"},
        "snapshot_auto_follow_enabled": runtime_role == "api" and snapshot_auto_follow_enabled,
        "external_api_required": external_api_required,
        "model_api_configured": model_api_configured,
        "live_sample_target_battles": live_sample_target_battles,
        "quota": (
            quota.status()
            if quota is not None
            else {"backend": process_quota_backend, "available": False}
        ),
        "performance_baseline": {
            "api_startup": getattr(app.state, "api_startup_baseline", None),
            "rag_preheat": getattr(app.state, "rag_preheat_baseline", None),
        },
    }


def build_model_status_payload(get_model_provider_status: Callable[[], dict]) -> dict:
    """Return sanitized model provider status from the configured provider guard."""
    return get_model_provider_status()


def build_readiness_response(readiness: dict) -> tuple[int, dict]:
    """Split the internal readiness status into HTTP status and public payload."""
    return readiness["http_status"], {
        key: value for key, value in readiness.items() if key != "http_status"
    }


def build_readiness_decision(
    *,
    initialized: bool,
    quota_available: bool,
    quota_fail_mode: str,
    strict: bool,
    model_configured: bool,
    model_provider: dict,
    snapshot_usable: bool,
    snapshot_status: str,
    rag_status: str,
    snapshot_id: str | None,
    rag_snapshot_id: str | None,
    rag_alignment: dict,
) -> dict:
    """Return readiness status, HTTP status, blockers, and degraded reasons."""
    blockers: list[str] = []
    degraded_reasons: list[str] = []
    if not initialized:
        blockers.append("runtime_initializing")
    if not quota_available and quota_fail_mode == "closed":
        blockers.append("process_quota_unavailable")
    if strict and not model_configured:
        blockers.append("model_api_unconfigured")
    if strict and model_provider.get("circuit_state") == "open":
        blockers.append("model_provider_circuit_open")
    if strict and not snapshot_usable:
        blockers.append("official_snapshot_unavailable")
    if snapshot_usable and snapshot_status in {"refreshing", "cooldown", "stale"}:
        degraded_reasons.append(f"snapshot_{snapshot_status}")
    if rag_status not in {"ready", "bm25_only", "not_required"}:
        degraded_reasons.append(f"rag_{rag_status}")
    if snapshot_usable and rag_status in {"ready", "bm25_only"} and snapshot_id != rag_snapshot_id:
        degraded_reasons.append("snapshot_rag_misaligned")
    if snapshot_usable and rag_status in {"ready", "bm25_only"} and not rag_alignment["fingerprint_aligned"]:
        degraded_reasons.append("snapshot_rag_fingerprint_misaligned")

    if blockers:
        status = "unavailable"
        http_status = 503
    elif degraded_reasons:
        status = "degraded"
        http_status = 200
    else:
        status = "ready"
        http_status = 200

    return {
        "status": status,
        "http_status": http_status,
        "blockers": blockers,
        "degraded_reasons": degraded_reasons,
    }


def build_readiness_payload(
    *,
    initialized: bool,
    model_configured: bool,
    model_provider: dict,
    quota_status: dict,
    process_quota_fail_mode: str,
    strict: bool,
    snapshot_status: str,
    snapshot_usable: bool,
    snapshot_id: str | None,
    rag_status: str,
    rag_snapshot_id: str | None,
    rag_alignment: dict,
    rag_document_validation: object,
) -> dict:
    """Build the full internal /ready payload while keeping route reads outside."""
    decision = build_readiness_decision(
        initialized=initialized,
        quota_available=bool(quota_status.get("available", False)),
        quota_fail_mode=process_quota_fail_mode,
        strict=strict,
        model_configured=model_configured,
        model_provider=model_provider,
        snapshot_usable=snapshot_usable,
        snapshot_status=snapshot_status,
        rag_status=rag_status,
        snapshot_id=snapshot_id,
        rag_snapshot_id=rag_snapshot_id,
        rag_alignment=rag_alignment,
    )
    return {
        "status": decision["status"],
        "http_status": decision["http_status"],
        "initialized": initialized,
        "model_api_configured": model_configured,
        "model_provider": model_provider,
        "quota": quota_status,
        "external_api_required": strict,
        "snapshot_status": snapshot_status,
        "snapshot_usable": snapshot_usable,
        "snapshot_id": snapshot_id,
        "rag_status": rag_status,
        "rag_snapshot_id": rag_snapshot_id,
        "snapshot_rag_aligned": bool(snapshot_id and snapshot_id == rag_snapshot_id),
        "snapshot_rag_fingerprint_aligned": bool(rag_alignment["fingerprint_aligned"]),
        "snapshot_docs_fingerprint": rag_alignment["snapshot_docs_fingerprint"],
        "active_rag_docs_fingerprint": rag_alignment["active_docs_fingerprint"],
        "index_docs_fingerprint": rag_alignment["index_docs_fingerprint"],
        "rag_document_validation": public_rag_validation(rag_document_validation),
        "blockers": decision["blockers"],
        "degraded_reasons": decision["degraded_reasons"],
    }


def build_readiness_status(
    app: Any,
    *,
    strict: bool,
    model_configured: bool,
    is_snapshot_usable: Callable[[object], bool],
    process_quota_backend: str,
    process_quota_fail_mode: str,
    get_model_provider_status: Callable[[], dict],
) -> dict:
    """Read runtime state and build the internal /ready contract."""
    snapshot = getattr(app.state, "live_snapshot", None)
    quota = getattr(app.state, "process_quota", None)
    quota_status = quota.status() if quota is not None else {
        "backend": process_quota_backend,
        "available": process_quota_backend == "memory",
    }
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    return build_readiness_payload(
        initialized=bool(getattr(app.state, "initialized", False)),
        model_configured=model_configured,
        model_provider=get_model_provider_status(),
        quota_status=quota_status,
        process_quota_fail_mode=process_quota_fail_mode,
        strict=strict,
        snapshot_status=getattr(app.state, "live_refresh_status", "missing"),
        snapshot_usable=is_snapshot_usable(snapshot),
        snapshot_id=snapshot_id,
        rag_status=getattr(app.state, "rag_status", "not_required"),
        rag_snapshot_id=getattr(app.state, "rag_snapshot_id", None),
        rag_alignment=build_rag_alignment_state(app),
        rag_document_validation=getattr(app.state, "rag_document_validation", None),
    )


def build_rag_alignment_state(app: Any) -> dict:
    """Return the public RAG/snapshot alignment state used by status views."""
    snapshot = getattr(app.state, "live_snapshot", None)
    retriever = getattr(app.state, "retriever", None)
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    rag_snapshot_id = getattr(app.state, "rag_snapshot_id", None)
    snapshot_fingerprint = snapshot.get("rag_docs_fingerprint") if isinstance(snapshot, dict) else None
    active_fingerprint = getattr(app.state, "rag_docs_fingerprint", None)
    index_fingerprint = getattr(retriever, "docs_fingerprint", None)
    snapshot_aligned = bool(snapshot_id and snapshot_id == rag_snapshot_id)
    fingerprint_aligned = bool(
        snapshot_fingerprint
        and snapshot_fingerprint == active_fingerprint
        and snapshot_fingerprint == index_fingerprint
    )
    return {
        "snapshot_id": rag_snapshot_id,
        "snapshot_docs_fingerprint": snapshot_fingerprint,
        "active_docs_fingerprint": active_fingerprint,
        "index_docs_fingerprint": index_fingerprint,
        "snapshot_aligned": snapshot_aligned,
        "fingerprint_aligned": fingerprint_aligned,
        "fully_aligned": snapshot_aligned and fingerprint_aligned,
    }


def public_rag_validation(report: object) -> dict | None:
    """Return a display-safe RAG validation summary without full invalid ID lists."""
    if not isinstance(report, dict):
        return None
    invalid_ids = report.get("invalid_doc_ids") if isinstance(report.get("invalid_doc_ids"), list) else []
    return {
        key: report.get(key)
        for key in (
            "schema_version",
            "snapshot_id",
            "docs_fingerprint",
            "document_count",
            "source_counts",
            "card_documents_checked",
            "deck_documents_checked",
            "matchup_documents_checked",
            "passed",
            "failures",
        )
    } | {
        "invalid_document_count": len(invalid_ids),
        "invalid_doc_ids_sample": invalid_ids[:20],
    }


def get_snapshot_artifact_status(data_dir: Path, snapshot_id: str | None) -> dict:
    """Report compact local artifact readiness without hashing or heavy initialization."""

    def manifest_status(root: str) -> dict:
        if not snapshot_id:
            return {"status": "unavailable", "snapshot_id": None, "counts": {}}
        path = Path(data_dir) / root / snapshot_id / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "unavailable", "snapshot_id": snapshot_id, "counts": {}}
        aligned = isinstance(manifest, dict) and manifest.get("snapshot_id") == snapshot_id
        return {
            "status": "ready" if aligned else "misaligned",
            "snapshot_id": manifest.get("snapshot_id") if isinstance(manifest, dict) else None,
            "counts": manifest.get("counts", {}) if isinstance(manifest, dict) else {},
        }

    review = {"status": "not_imported", "snapshot_id": snapshot_id}
    if snapshot_id:
        report_path = Path(data_dir) / "external_reviews" / snapshot_id / "validation_report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = None
        if isinstance(report, dict):
            review = {
                "status": "validated" if report.get("passed") is True else "rejected",
                "snapshot_id": report.get("snapshot_id"),
                "document_count": report.get("document_count"),
                "activation": report.get("activation"),
            }
    return {
        "audit_export": manifest_status("audit_exports"),
        "structured_stats": manifest_status("structured_stats"),
        "external_review": review,
    }


def build_live_sample_settings_payload(
    app: Any,
    *,
    target_battles: int,
    min_target_battles: int,
    max_target_battles: int,
    can_update_target: bool,
    refresh_status: str = "ready",
) -> dict:
    """Build the public live-sample settings payload for operator UIs."""
    return {
        "target_battles": target_battles,
        "min_target_battles": min_target_battles,
        "max_target_battles": max_target_battles,
        "refresh_status": getattr(app.state, "live_refresh_status", refresh_status),
        "can_update_target": can_update_target,
        "cooldown_until": getattr(app.state, "live_cooldown_until", 0.0),
    }


def build_live_snapshot_runtime_state(app: Any, *, now_monotonic: float) -> dict:
    """Return volatile live-snapshot runtime fields for public status payloads."""
    cooldown_remaining = max(
        0.0,
        getattr(app.state, "live_cooldown_until", 0.0) - now_monotonic,
    )
    return {
        "refresh_status": getattr(app.state, "live_refresh_status", "unavailable"),
        "collection_progress": getattr(app.state, "live_collection_progress", None),
        "last_refresh_attempt": getattr(app.state, "live_last_refresh_attempt", None),
        "cooldown_remaining_seconds": round(cooldown_remaining, 1),
        "error": getattr(app.state, "live_error", None),
    }


def build_live_snapshot_leaderboard_payload(
    snapshot: dict | None,
    *,
    default_candidate_limit: int,
    default_queue_capacity: int,
) -> dict:
    """Return display-safe leaderboard coverage fields for /snapshot/status."""
    if not isinstance(snapshot, dict):
        return {
            "candidate_limit": default_candidate_limit,
            "queue_capacity": default_queue_capacity,
            "rank_start": 1,
            "scanned_rank_end": None,
            "ranked_players_returned": 0,
            "sampled_players": 0,
            "failed_players": 0,
        }
    fetched_players = int(snapshot.get("fetched_players", 0) or 0)
    return {
        "candidate_limit": snapshot.get("leaderboard_candidate_limit", default_candidate_limit),
        "queue_capacity": snapshot.get("collection_metrics", {}).get(
            "player_queue_capacity", default_queue_capacity
        ),
        "rank_start": snapshot.get("leaderboard_start_rank", 1),
        "scanned_rank_end": snapshot.get("leaderboard_last_scanned_rank", fetched_players or None),
        "ranked_players_returned": snapshot.get("ranked_players", 0),
        "sampled_players": snapshot.get("sampled_players", 0),
        "failed_players": snapshot.get("failed_players", 0),
    }


def build_live_snapshot_rag_payload(
    app: Any,
    snapshot: dict | None,
    *,
    live_data_enabled: bool,
    rag_alignment: dict,
) -> dict:
    """Return the RAG portion of /snapshot/status without exposing full validation details."""
    if not isinstance(snapshot, dict):
        return {
            "status": "not_required" if not live_data_enabled else getattr(app.state, "rag_status", "not_ready"),
            "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
            "document_counts": {},
            "quality": getattr(app.state, "rag_quality_report", None),
            **rag_alignment,
            "validation": public_rag_validation(getattr(app.state, "rag_document_validation", None)),
            "candidate_status": getattr(app.state, "rag_candidate_status", "not_ready"),
            "candidate_error": getattr(app.state, "rag_candidate_error", None),
            "candidate_validation": public_rag_validation(getattr(app.state, "rag_candidate_validation", None)),
        }
    return {
        "status": getattr(app.state, "rag_status", "not_ready"),
        "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
        "document_counts": snapshot.get("rag_document_counts", {}),
        "quality": getattr(app.state, "rag_quality_report", None),
        **rag_alignment,
        "validation": public_rag_validation(
            getattr(app.state, "rag_document_validation", snapshot.get("rag_document_validation"))
        ),
        "candidate_status": getattr(app.state, "rag_candidate_status", "not_ready"),
        "candidate_error": getattr(app.state, "rag_candidate_error", None),
        "candidate_validation": public_rag_validation(getattr(app.state, "rag_candidate_validation", None)),
    }


def build_live_snapshot_retention_payload(*, days: int, max_complete_snapshots: int) -> dict:
    """Return the public retention policy fields for /snapshot/status."""
    return {"days": days, "max_complete_snapshots": max_complete_snapshots}


def build_live_snapshot_data_sources_payload(*, snapshot_available: bool) -> dict:
    """Return the public data-source provenance fields for /snapshot/status."""
    if snapshot_available:
        return {
            "schedule": "disabled_clan_war_feature",
            "cards": "official_weekly_snapshot",
            "decks": "official_weekly_snapshot",
            "rag_documents": "official_weekly_snapshot",
        }
    return {
        "schedule": "disabled_clan_war_feature",
        "cards": "not_available",
        "decks": "not_available",
        "rag_documents": "not_available",
    }


def build_live_snapshot_status_payload(
    app: Any,
    snapshot: dict | None,
    *,
    runtime_state: dict,
    rag_alignment: dict,
    live_data_enabled: bool,
    daily_target_battles: int,
    pol_seed_players: int,
    leaderboard_players: int,
    refresh_interval_seconds: int,
    retention_days: int,
    retention_max_complete: int,
    data_dir: Path,
    snapshot_age_seconds: Callable[[dict], float | None],
    is_scope_verified: Callable[[object], bool],
) -> dict:
    """Build the full display-safe /snapshot/status payload."""
    refresh_status = runtime_state["refresh_status"]
    retention = build_live_snapshot_retention_payload(
        days=retention_days,
        max_complete_snapshots=retention_max_complete,
    )
    if not isinstance(snapshot, dict):
        return {
            "source": "Supercell Official API",
            "source_type": "weekly leaderboard battle-log snapshot",
            "status": refresh_status,
            "snapshot_status": refresh_status,
            "snapshot_id": None,
            "fetched_at": None,
            "published_at": None,
            "sample_battles": 0,
            "target_battles": daily_target_battles,
            "shortfall_battles": daily_target_battles,
            "collection_scope": None,
            "scope_verified": False,
            "leaderboard": build_live_snapshot_leaderboard_payload(
                None,
                default_candidate_limit=pol_seed_players,
                default_queue_capacity=leaderboard_players,
            ),
            "collection_metrics": {},
            "collection_progress": runtime_state["collection_progress"],
            "special_fields_probe": None,
            "refresh_interval_seconds": refresh_interval_seconds,
            "retention": retention,
            "artifacts": get_snapshot_artifact_status(data_dir, None),
            "runtime": build_runtime_summary(app),
            "rag": build_live_snapshot_rag_payload(
                app,
                None,
                live_data_enabled=live_data_enabled,
                rag_alignment=rag_alignment,
            ),
            "rag_status": "not_required" if not live_data_enabled else getattr(app.state, "rag_status", "not_ready"),
            "data_sources": build_live_snapshot_data_sources_payload(snapshot_available=False),
            "last_refresh_attempt": runtime_state["last_refresh_attempt"],
            "cooldown_remaining_seconds": runtime_state["cooldown_remaining_seconds"],
            "error": runtime_state["error"],
        }

    return {
        "source": "Supercell Official API",
        "source_type": "weekly leaderboard battle-log snapshot",
        "status": refresh_status,
        "snapshot_status": refresh_status,
        "snapshot_id": snapshot.get("snapshot_id"),
        "fetched_at": snapshot.get("fetched_at"),
        "published_at": snapshot.get("published_at"),
        "age_seconds": snapshot_age_seconds(snapshot),
        "sample_battles": snapshot.get("sample_battles", 0),
        "target_battles": snapshot.get("target_battles", daily_target_battles),
        "shortfall_battles": snapshot.get("shortfall_battles", daily_target_battles),
        "collection_scope": snapshot.get("collection_scope", "legacy_mixed_or_unverified"),
        "scope_verified": is_scope_verified(snapshot),
        "leaderboard": build_live_snapshot_leaderboard_payload(
            snapshot,
            default_candidate_limit=leaderboard_players,
            default_queue_capacity=leaderboard_players,
        ),
        "collection_metrics": snapshot.get("collection_metrics", {}),
        "collection_progress": runtime_state["collection_progress"],
        "special_fields_probe": snapshot.get("special_fields_probe"),
        "refresh_interval_seconds": refresh_interval_seconds,
        "retention": retention,
        "artifacts": get_snapshot_artifact_status(data_dir, str(snapshot.get("snapshot_id") or "") or None),
        "runtime": build_runtime_summary(app),
        "rag": build_live_snapshot_rag_payload(
            app,
            snapshot,
            live_data_enabled=live_data_enabled,
            rag_alignment=rag_alignment,
        ),
        "rag_status": getattr(app.state, "rag_status", "not_ready"),
        "data_sources": build_live_snapshot_data_sources_payload(snapshot_available=True),
        "last_refresh_attempt": runtime_state["last_refresh_attempt"],
        "cooldown_remaining_seconds": runtime_state["cooldown_remaining_seconds"],
        "error": runtime_state["error"],
    }


def build_runtime_summary(app: Any) -> dict:
    """Return the compact public runtime counter summary used by status views."""
    metrics = getattr(app.state, "runtime_metrics", None)
    if metrics is None:
        return {
            "process_requests": 0,
            "successes": 0,
            "failures": 0,
            "cancelled": 0,
            "rate_limited": 0,
            "process_p95_ms": 0.0,
            "sample_size": 0,
        }
    return metrics.public_summary()


def build_metrics_body(
    app: Any,
    *,
    runtime_metrics_factory: Callable[[], Any],
    render_model_provider_metrics: Callable[[], str],
) -> str:
    """Build the Prometheus text body for /metrics."""
    snapshot_status = getattr(app.state, "live_refresh_status", "missing")
    rag_status = getattr(app.state, "rag_status", "not_required")
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    metrics_registry = getattr(app.state, "runtime_metrics", None) or runtime_metrics_factory()
    body = metrics_registry.render_prometheus(
        snapshot_status=snapshot_status,
        rag_status=rag_status,
        snapshot_aligned=bool(
            snapshot_id and snapshot_id == getattr(app.state, "rag_snapshot_id", None)
        ),
    )
    return body + render_model_provider_metrics()


__all__ = [
    "build_health_payload",
    "build_live_sample_settings_payload",
    "build_live_snapshot_data_sources_payload",
    "build_live_snapshot_leaderboard_payload",
    "build_live_snapshot_rag_payload",
    "build_live_snapshot_retention_payload",
    "build_live_snapshot_runtime_state",
    "build_live_snapshot_status_payload",
    "build_metrics_body",
    "build_model_status_payload",
    "build_rag_alignment_state",
    "build_readiness_decision",
    "build_readiness_payload",
    "build_readiness_response",
    "build_readiness_status",
    "build_runtime_summary",
    "get_snapshot_artifact_status",
    "public_rag_validation",
]
