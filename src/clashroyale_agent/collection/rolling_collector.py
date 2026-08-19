"""Collect one strict Path of Legend batch into the rolling fact store."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_config import (
    SUPERCELL_API_TIMEOUT_SECONDS,
    SUPERCELL_BATTLES_PER_PLAYER,
    SUPERCELL_FETCH_CONCURRENCY,
    SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
    SUPERCELL_HIGH_VOLUME_MAX_RETRIES,
    SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
    SUPERCELL_LEADERBOARD_PLAYERS,
)
from clashroyale_agent.collection.rolling_corpus import (
    BatchValidationPolicy,
    CorpusError,
    CorpusWriterBusyError,
    CorpusWriterLock,
    RollingCorpusStore,
)
from clashroyale_agent.collection.rolling_materializer import build_snapshot_group
from clashroyale_agent.collection.publication_queue import (
    enqueue as enqueue_pending_publication,
    pending as pending_publications,
    remove as remove_pending_publication,
)
from clashroyale_agent.collection.collector_status import (
    CollectionStatusReporter,
    batch_baseline as _batch_baseline,
    bounded_fetch_concurrency as _bounded_fetch_concurrency,
    collection_status_payload as _collection_status_payload,
    elapsed_seconds as _elapsed_seconds,
    STATUS_HEARTBEAT_SECONDS as _STATUS_HEARTBEAT_SECONDS,
)
from clashroyale_agent.collection.collector_staging import (
    directory_size_bytes as _directory_size_bytes,
    discard_lane_stage as _discard_lane_stage_orchestrated,
    lane_paths as _lane_paths,
    prepare_lane_stage as _prepare_lane_stage_orchestrated,
    staging_limit_bytes as _staging_limit_bytes_orchestrated,
)
from clashroyale_agent.collection.collector_tokens import (
    parse_api_tokens as _parse_api_tokens_orchestrated,
    resolve_api_token as _resolve_api_token_orchestrated,
)
from supercell_live import SupercellAPIClient


SHANGHAI = ZoneInfo("Asia/Shanghai")

_TOKEN_SLOT_BY_MODE = {"daily_ranked": 0, "weekly_expanded": 1}
_STAGING_LIMIT_BYTES_BY_MODE = {
    "daily_ranked": 512 * 1024**2,
    "weekly_expanded": 4 * 1024**3,
}
_TOTAL_STAGING_LIMIT_BYTES = 5 * 1024**3
_MERGE_LOCK_WAIT_SECONDS = 2 * 60 * 60
def _resolve_api_token(mode: str) -> str:
    return _resolve_api_token_orchestrated(
        mode,
        token_slot_by_mode=_TOKEN_SLOT_BY_MODE,
        error_type=CorpusError,
    )


def _parse_api_tokens(raw: str) -> tuple[str, ...]:
    return _parse_api_tokens_orchestrated(raw, CorpusError)


def _staging_limit_bytes(mode: str) -> int:
    return _staging_limit_bytes_orchestrated(mode, _STAGING_LIMIT_BYTES_BY_MODE)


def _prepare_lane_stage(
    data_dir: Path, mode: str, preferred_batch_id: str, now: datetime
) -> tuple[str, Path, dict, bool]:
    return _prepare_lane_stage_orchestrated(
        data_dir, mode, preferred_batch_id, now,
        total_staging_limit=_TOTAL_STAGING_LIMIT_BYTES,
        atomic_json=_atomic_json,
        error_type=CorpusError,
    )


def _discard_lane_stage(data_dir: Path, mode: str) -> None:
    _discard_lane_stage_orchestrated(data_dir, mode)


def _status_path(data_dir: Path, mode: str) -> Path:
    return Path(data_dir) / "corpus" / f"collection_status.{mode}.json"


def _validation_policy(mode: str, metrics: dict) -> BatchValidationPolicy:
    default = BatchValidationPolicy()
    if mode != "daily_ranked":
        return default
    try:
        available_players = min(default.ranked_player_target, int(metrics.get("seed_players") or 0))
    except (TypeError, ValueError):
        available_players = 0
    if available_players <= 0:
        return default
    return BatchValidationPolicy(
        required_top_rank=min(default.required_top_rank, available_players),
        ranked_player_target=available_players,
        minimum_coverage=default.minimum_coverage,
        weekly_target_battles=default.weekly_target_battles,
    )


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _read_json_object(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"status file is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise CorpusError(f"status file is not an object: {path.name}")
    return value


def _batch_id(mode: str, now: datetime) -> str:
    local = now.astimezone(SHANGHAI)
    return f"{mode}-{local:%Y%m%d}"


def _publish_snapshot_if_accepted(
    store: RollingCorpusStore,
    *,
    data_dir: Path,
    now: datetime,
    validation_report: dict,
) -> tuple[dict | None, dict | None]:
    if validation_report.get("passed") is not True:
        return None, None
    try:
        manifest = build_snapshot_group(store, data_dir=data_dir, now=now)
        return (
            {
                "status": "published",
                "snapshot_group_id": manifest["snapshot_group_id"],
                "dataset_count": len(manifest["datasets"]),
                "fully_aligned": manifest.get("fully_aligned") is True,
                "publication_timings_seconds": manifest.get("publication_timings_seconds", {}),
            },
            None,
        )
    except Exception as exc:
        return None, {"error_type": type(exc).__name__, "message": str(exc)}


def collect(mode: str, *, data_dir: Path, batch_id: str | None = None) -> dict:
    if mode not in {"daily_ranked", "weekly_expanded"}:
        raise CorpusError("mode must be daily_ranked or weekly_expanded")

    data_dir = Path(data_dir)
    corpus_dir = data_dir / "corpus"
    status_path = _status_path(data_dir, mode)
    now = datetime.now(timezone.utc)
    preferred_batch_id = batch_id or _batch_id(mode, now)
    target_battles = 200_000 if mode == "weekly_expanded" else 25_000
    player_limit = SUPERCELL_LEADERBOARD_PLAYERS if mode == "weekly_expanded" else 1000
    expand_opponents = mode == "weekly_expanded"
    fetch_concurrency = _bounded_fetch_concurrency(SUPERCELL_FETCH_CONCURRENCY)
    token = _resolve_api_token(mode)
    stage_batch_id, work_root, stage_state, stage_resumed = _prepare_lane_stage(
        data_dir, mode, preferred_batch_id, now
    )
    stage_limit_bytes = _staging_limit_bytes(mode)
    if _directory_size_bytes(work_root) > stage_limit_bytes:
        raise CorpusError("active staging workspace exceeds its configured storage limit")
    collect_started_at = time.monotonic()
    performance: dict[str, float | int] = {"fetch_concurrency": fetch_concurrency}

    def performance_snapshot() -> dict:
        return {
            **performance,
            "elapsed_seconds": _elapsed_seconds(collect_started_at),
        }

    def progress_callback(progress: dict) -> None:
        _atomic_json(
            status_path,
            _collection_status_payload(
                mode=mode,
                trigger_batch_id=preferred_batch_id,
                effective_batch_id=stage_batch_id,
                resumed=stage_resumed,
                stage="fetching",
                staging={
                    "status": "fetching",
                    "workspace_bytes": _directory_size_bytes(work_root),
                    "workspace_limit_bytes": stage_limit_bytes,
                    "total_limit_bytes": _TOTAL_STAGING_LIMIT_BYTES,
                },
                performance=performance_snapshot(),
                **progress,
            ),
        )

    def stage_heartbeat(stage: str, *, status: str, **fields):
        reporter = CollectionStatusReporter(
            status_path,
            base_fields={
                "schema_version": 1,
                "batch_id": stage_batch_id,
                "trigger_batch_id": preferred_batch_id,
                "collection_mode": mode,
                "resumed": stage_resumed,
            },
        )
        return reporter.stage(stage, status=status, **fields)

    progress_callback(
        {
            "status": "starting",
            "target_battles": target_battles,
            "usable_battles": 0,
            "updated_at": now.isoformat(),
        }
    )
    client = SupercellAPIClient(
        token,
        timeout_seconds=SUPERCELL_API_TIMEOUT_SECONDS,
        max_retries=max(2, SUPERCELL_HIGH_VOLUME_MAX_RETRIES),
        requests_per_second=SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
    )
    fetch_started_at = time.monotonic()
    snapshot = client.fetch_snapshot(
        target_battles=target_battles,
        player_limit=player_limit,
        seed_player_limit=1000,
        battles_per_player=SUPERCELL_BATTLES_PER_PLAYER,
        concurrency=fetch_concurrency,
        max_duration_seconds=SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
        progress_callback=progress_callback,
        progress_interval_seconds=_STATUS_HEARTBEAT_SECONDS,
        spool_dir=work_root,
        collection_mode=mode,
        expand_opponents=expand_opponents,
        strict_battle_contract=True,
        ranked_tail_retry_rounds=1,
        max_workspace_bytes=stage_limit_bytes,
        export_raw_battles=False,
    )
    performance["fetch_seconds"] = _elapsed_seconds(fetch_started_at)
    if (
        snapshot.get("collection_scope") != "path_of_legend"
        or snapshot.get("scope_contract") != "path_of_legend_only_v1"
        or snapshot.get("scope_verified") is not True
    ):
        raise CorpusError("collector did not satisfy the strict Path of Legend scope contract")

    completed_at = datetime.now(timezone.utc)
    wait_lock_started_at = time.monotonic()
    lock_deadline = time.monotonic() + _MERGE_LOCK_WAIT_SECONDS
    while True:
        try:
            writer_lock = CorpusWriterLock(corpus_dir / "writer.lock")
            writer_lock.__enter__()
            break
        except CorpusWriterBusyError:
            if time.monotonic() >= lock_deadline:
                raise
            _atomic_json(
                status_path,
                _collection_status_payload(
                    mode=mode,
                    trigger_batch_id=preferred_batch_id,
                    effective_batch_id=stage_batch_id,
                    resumed=stage_resumed,
                    stage="waiting_for_merge",
                    status="staged_waiting_for_merge",
                    staging={
                        "workspace_bytes": _directory_size_bytes(work_root),
                        "workspace_limit_bytes": stage_limit_bytes,
                    },
                    performance=performance_snapshot(),
                ),
            )
            time.sleep(5)
    performance["wait_writer_lock_seconds"] = _elapsed_seconds(wait_lock_started_at)

    try:
        store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
        try:
            capacity = store.assert_disk_capacity()
            resolved_batch_id = store.unique_batch_id(stage_batch_id)
            import_started_at = time.monotonic()
            with stage_heartbeat(
                "importing",
                status="processing",
                resolved_batch_id=resolved_batch_id,
                performance=performance_snapshot(),
            ):
                imported = store.import_workspace_batch(
                    Path(snapshot["_aggregate_store_path"]),
                    batch_id=resolved_batch_id,
                    batch_type=mode,
                    started_at=stage_state["started_at"],
                    leaderboard_frozen_at=stage_state["started_at"],
                    observed_at=completed_at,
                )
            performance["import_seconds"] = _elapsed_seconds(import_started_at)
            metrics = snapshot.get("collection_metrics", {})
            validating_started_at = time.monotonic()
            with stage_heartbeat(
                "validating",
                status="processing",
                resolved_batch_id=resolved_batch_id,
                performance=performance_snapshot(),
            ):
                report = store.finalize_batch(
                    resolved_batch_id,
                    completed_at=completed_at,
                    policy=_validation_policy(mode, metrics),
                    request_count=int(metrics.get("request_count") or 0),
                    rate_limited=int(metrics.get("rate_limited") or 0),
                    refresh_budget_exhausted=bool(metrics.get("refresh_budget_exhausted")),
                    source_exhausted=bool(metrics.get("source_exhausted")),
                )
            performance["finalize_seconds"] = _elapsed_seconds(validating_started_at)
            retention_started_at = time.monotonic()
            with stage_heartbeat(
                "retention",
                status="processing",
                resolved_batch_id=resolved_batch_id,
                performance=performance_snapshot(),
            ):
                retention = store.expire_and_prune(now=completed_at)
            performance["retention_seconds"] = _elapsed_seconds(retention_started_at)
            _atomic_json(
                status_path,
                {
                    "schema_version": 1,
                    "status": "processing",
                    "batch_id": resolved_batch_id,
                    "collection_mode": mode,
                    "completed_at": completed_at.isoformat(),
                    "validation_passed": report["passed"],
                    "cost_boundaries": {
                        "cloud_llm_calls": 0,
                        "cloud_embedding_calls": 0,
                    },
                    "performance": performance_snapshot(),
                },
            )
            publishing_started_at = time.monotonic()
            with stage_heartbeat(
                "publishing",
                status="processing",
                resolved_batch_id=resolved_batch_id,
                performance=performance_snapshot(),
            ):
                publication, publication_error = _publish_snapshot_if_accepted(
                    store,
                    data_dir=data_dir,
                    now=completed_at,
                    validation_report=report,
                )
            if publication_error is not None and report["passed"]:
                enqueue_pending_publication(
                    data_dir,
                    mode=mode,
                    batch_id=resolved_batch_id,
                    queued_at=completed_at,
                )
            performance["publishing_seconds"] = _elapsed_seconds(publishing_started_at)
            performance["total_seconds"] = _elapsed_seconds(collect_started_at)
            staging = {
                "workspace_bytes": _directory_size_bytes(work_root),
                "workspace_limit_bytes": stage_limit_bytes,
                "total_limit_bytes": _TOTAL_STAGING_LIMIT_BYTES,
                "resumed": stage_resumed,
            }
            result = {
                "schema_version": 1,
                "status": (
                    "accepted"
                    if report["passed"] and publication is not None
                    else "accepted_publication_failed"
                    if report["passed"]
                    else "rejected"
                ),
                "batch_id": resolved_batch_id,
                "trigger_batch_id": preferred_batch_id,
                "collection_mode": mode,
                "completed_at": completed_at.isoformat(),
                "disk": capacity,
                "import": imported,
                "validation": report,
                "retention": retention,
                "publication": publication,
                "publication_error": publication_error,
                "cost_boundaries": {
                    "cloud_llm_calls": 0,
                    "cloud_embedding_calls": 0,
                    "local_embedding_index_builds": 1 if publication is not None else 0,
                },
                "performance": performance_snapshot(),
                "staging": staging,
                "batch_baseline": _batch_baseline(
                    snapshot=snapshot,
                    imported=imported,
                    performance=performance,
                    staging=staging,
                ),
                "resumed": stage_resumed,
            }
            _atomic_json(status_path, result)
            _atomic_json(corpus_dir / "collection_status.json", result)
            _discard_lane_stage(data_dir, mode)
            return result
        finally:
            store.close()
    finally:
        writer_lock.__exit__(None, None, None)


def maintain_windows_after_failed_collection(*, data_dir: Path) -> dict:
    """Expire real-time windows and attempt publication even after collection fails."""
    data_dir = Path(data_dir)
    corpus_dir = data_dir / "corpus"
    now = datetime.now(timezone.utc)
    with CorpusWriterLock(corpus_dir / "writer.lock"):
        store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
        try:
            retention = store.expire_and_prune(now=now)
            try:
                manifest = build_snapshot_group(store, data_dir=data_dir, now=now)
                publication = {
                    "status": "published",
                    "snapshot_group_id": manifest["snapshot_group_id"],
                }
            except Exception as exc:
                publication = {
                    "status": "not_published",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            return {"retention": retention, "publication": publication}
        finally:
            store.close()


def retry_failed_publication(*, data_dir: Path) -> dict:
    """Publish already accepted facts without contacting Supercell or collecting again."""
    data_dir = Path(data_dir)
    corpus_dir = data_dir / "corpus"
    status_path = corpus_dir / "collection_status.json"
    initial = _read_json_object(status_path)
    if initial is None or initial.get("status") != "accepted_publication_failed":
        return {"status": "not_needed"}

    with CorpusWriterLock(corpus_dir / "writer.lock"):
        current = _read_json_object(status_path)
        if current is None or current.get("status") != "accepted_publication_failed":
            return {"status": "not_needed"}
        store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
        try:
            now = datetime.now(timezone.utc)
            retention = store.expire_and_prune(now=now)
            manifest = build_snapshot_group(store, data_dir=data_dir, now=now)
        finally:
            store.close()

        publication = {
            "status": "published",
            "snapshot_group_id": manifest["snapshot_group_id"],
            "dataset_count": len(manifest["datasets"]),
            "fully_aligned": manifest.get("fully_aligned") is True,
            "publication_timings_seconds": manifest.get("publication_timings_seconds", {}),
        }
        repaired = {
            **current,
            "status": "accepted",
            "retention": retention,
            "publication": publication,
            "publication_error": None,
            "publication_repaired_at": now.isoformat(),
        }
        costs = dict(current.get("cost_boundaries") or {})
        costs["cloud_llm_calls"] = 0
        costs["cloud_embedding_calls"] = 0
        costs["local_embedding_index_builds"] = 1
        repaired["cost_boundaries"] = costs
        _atomic_json(status_path, repaired)
        remove_pending_publication(
            data_dir,
            mode=str(current.get("collection_mode") or ""),
            batch_id=str(current.get("batch_id") or ""),
        )

        mode = str(current.get("collection_mode") or "")
        if mode in _TOKEN_SLOT_BY_MODE:
            lane_status_path = _status_path(data_dir, mode)
            lane_status = _read_json_object(lane_status_path)
            if (
                lane_status is not None
                and lane_status.get("status") == "accepted_publication_failed"
                and lane_status.get("batch_id") == current.get("batch_id")
            ):
                _atomic_json(lane_status_path, repaired)

        return {
            "status": "published",
            "batch_id": current.get("batch_id"),
            "collection_mode": current.get("collection_mode"),
            "publication": publication,
        }


def process_publication_queue(*, data_dir: Path) -> dict:
    """Consume the oldest pending publication without contacting Supercell."""
    data_dir = Path(data_dir)
    entries = pending_publications(data_dir)
    if not entries:
        return {"status": "empty", "processed": 0}
    entry = entries[0]
    corpus_dir = data_dir / "corpus"
    with CorpusWriterLock(corpus_dir / "writer.lock"):
        store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
        try:
            now = datetime.now(timezone.utc)
            store.expire_and_prune(now=now)
            manifest = build_snapshot_group(store, data_dir=data_dir, now=now)
        finally:
            store.close()
    remove_pending_publication(
        data_dir,
        mode=str(entry.get("mode") or ""),
        batch_id=str(entry.get("batch_id") or ""),
    )
    return {
        "status": "published",
        "processed": 1,
        "batch_id": entry.get("batch_id"),
        "snapshot_group_id": manifest["snapshot_group_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily_ranked", "weekly_expanded"), required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--batch-id")
    parser.add_argument("--retry-publication-only", action="store_true")
    args = parser.parse_args()
    if args.retry_publication_only:
        try:
            repair = retry_failed_publication(data_dir=args.data_dir)
        except CorpusWriterBusyError as exc:
            print(
                json.dumps(
                    {"status": "publication_repair_deferred", "error_type": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                )
            )
            return 4
        except Exception as exc:
            print(
                json.dumps(
                    {"status": "publication_repair_failed", "error_type": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                )
            )
            return 3
        print(json.dumps(repair, ensure_ascii=False))
        return 0
    try:
        result = collect(args.mode, data_dir=args.data_dir, batch_id=args.batch_id)
    except CorpusWriterBusyError as exc:
        print(
            json.dumps(
                {
                    "status": "staged_pending_merge",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "cost_boundaries": {"cloud_llm_calls": 0, "cloud_embedding_calls": 0},
                },
                ensure_ascii=False,
            )
        )
        return 4
    except Exception as exc:
        if args.mode == "daily_ranked":
            try:
                maintenance = maintain_windows_after_failed_collection(data_dir=args.data_dir)
            except Exception as maintenance_exc:
                maintenance = {
                    "status": "failed",
                    "error_type": type(maintenance_exc).__name__,
                    "message": str(maintenance_exc),
                }
        else:
            maintenance = {"status": "not_run_for_expansion_lane"}
        error = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "maintenance": maintenance,
            "cost_boundaries": {"cloud_llm_calls": 0, "cloud_embedding_calls": 0},
        }
        error["collection_mode"] = args.mode
        _atomic_json(_status_path(args.data_dir, args.mode), error)
        print(json.dumps(error, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "accepted" else 2 if result["status"] == "rejected" else 3


if __name__ == "__main__":
    raise SystemExit(main())
