"""Collect one strict Path of Legend batch into the rolling fact store."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import shutil
import sys
import tempfile
import threading
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
from supercell_live import SupercellAPIClient


SHANGHAI = ZoneInfo("Asia/Shanghai")

_TOKEN_SLOT_BY_MODE = {"daily_ranked": 0, "weekly_expanded": 1}
_STAGING_LIMIT_BYTES_BY_MODE = {
    "daily_ranked": 512 * 1024**2,
    "weekly_expanded": 4 * 1024**3,
}
_TOTAL_STAGING_LIMIT_BYTES = 5 * 1024**3
_MERGE_LOCK_WAIT_SECONDS = 2 * 60 * 60
_STATUS_HEARTBEAT_SECONDS = 60.0


def _bounded_fetch_concurrency(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else 1
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, 4))


def _elapsed_seconds(started_at: float) -> float:
    return round(max(0.0, time.monotonic() - started_at), 3)


def _collection_status_payload(
    *,
    mode: str,
    trigger_batch_id: str,
    effective_batch_id: str,
    resumed: bool,
    stage: str,
    status: str,
    **fields,
) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "stage": stage,
        "batch_id": effective_batch_id,
        "trigger_batch_id": trigger_batch_id,
        "collection_mode": mode,
        "resumed": resumed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }


def _batch_baseline(
    *,
    snapshot: dict,
    imported: dict,
    performance: dict,
    staging: dict,
) -> dict:
    """Expose existing batch measurements under a stable aggregate contract."""
    metrics = snapshot.get("collection_metrics") or {}
    return {
        "batch_duration_seconds": float(performance.get("total_seconds") or 0.0),
        "dedupe": {
            "pre_dedupe_records": int(metrics.get("raw_battle_records") or 0),
            "post_dedupe_battles": int(snapshot.get("usable_battles") or 0),
            "duplicates_skipped": int(metrics.get("duplicates_skipped") or 0),
            "facts_inserted": int(imported.get("facts_inserted") or 0),
            "observations_imported": int(imported.get("observations_imported") or 0),
        },
        "staging_size_bytes": int(staging.get("workspace_bytes") or 0),
        "staging_limit_bytes": int(staging.get("workspace_limit_bytes") or 0),
    }


class CollectionStatusReporter:
    """Atomically refresh one lane status while a long synchronous stage runs."""

    def __init__(
        self,
        path: Path,
        *,
        base_fields: dict,
        heartbeat_interval_seconds: float = _STATUS_HEARTBEAT_SECONDS,
    ) -> None:
        self.path = Path(path)
        self.base_fields = dict(base_fields)
        self.heartbeat_interval_seconds = max(0.01, float(heartbeat_interval_seconds))
        self._fields: dict = {}
        self._stage_started_at: float | None = None
        self._sequence = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def update(self, **fields) -> None:
        with self._lock:
            self._fields.update(fields)
            self._write_locked()

    def _write_locked(self) -> None:
        self._sequence += 1
        stage_elapsed = (
            {"stage_elapsed_seconds": _elapsed_seconds(self._stage_started_at)}
            if self._stage_started_at is not None
            else {}
        )
        _atomic_json(
            self.path,
            {
                **self.base_fields,
                **self._fields,
                **stage_elapsed,
                "heartbeat_sequence": self._sequence,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            with self._lock:
                self._write_locked()

    def start(self, **fields) -> None:
        self.update(**fields)
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._heartbeat,
                name="collection-status-heartbeat",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 1.0))
            self._thread = None

    @contextmanager
    def stage(self, stage: str, *, status: str, **fields):
        self._stage_started_at = time.monotonic()
        self.start(stage=stage, status=status, **fields)
        try:
            yield self
        finally:
            with self._lock:
                self._fields["stage_elapsed_seconds"] = _elapsed_seconds(self._stage_started_at)
                self._write_locked()
            self.stop()
            self._stage_started_at = None


def _parse_api_tokens(raw: str) -> tuple[str, ...]:
    value = str(raw or "").strip()
    if not value:
        return ()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CorpusError("SUPERCELL_API_TOKENS must be valid JSON or separated text") from exc
        if not isinstance(parsed, list):
            raise CorpusError("SUPERCELL_API_TOKENS JSON value must be an array")
        tokens = tuple(str(item).strip() for item in parsed if str(item).strip())
    else:
        tokens = tuple(item.strip() for item in re.split(r"[,;\r\n]+", value) if item.strip())
    return tokens


def _resolve_api_token(mode: str) -> str:
    index = _TOKEN_SLOT_BY_MODE[mode]
    tokens = _parse_api_tokens(os.getenv("SUPERCELL_API_TOKENS", ""))
    legacy = os.getenv("SUPERCELL_API_TOKEN", "").strip()
    if len(tokens) == 1 and legacy and tokens[0] != legacy:
        tokens = (legacy, tokens[0])
    if not tokens and mode == "daily_ranked":
        if legacy:
            return legacy
    if len(tokens) <= index:
        if mode == "weekly_expanded":
            raise CorpusError("SUPERCELL_API_TOKENS requires a second token for weekly_expanded")
        raise CorpusError("SUPERCELL_API_TOKENS requires a first token for daily_ranked")
    return tokens[index]


def _lane_paths(data_dir: Path, mode: str) -> tuple[Path, Path, Path]:
    lane_root = Path(data_dir) / "rolling_lanes" / mode
    return lane_root, lane_root / "active", lane_root / "active_batch.json"


def _directory_size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _staging_limit_bytes(mode: str) -> int:
    configured = os.getenv(f"SUPERCELL_{mode.upper()}_STAGING_MAX_BYTES", "").strip()
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            pass
    return _STAGING_LIMIT_BYTES_BY_MODE[mode]


def _prepare_lane_stage(
    data_dir: Path, mode: str, preferred_batch_id: str, now: datetime
) -> tuple[str, Path, dict, bool]:
    lane_root, work_root, state_path = _lane_paths(data_dir, mode)
    lane_root.mkdir(parents=True, exist_ok=True)
    if _directory_size_bytes(Path(data_dir) / "rolling_lanes") > _TOTAL_STAGING_LIMIT_BYTES:
        raise CorpusError("total staging storage limit exceeded")
    state = None
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpusError("active staging state is unreadable") from exc
    if isinstance(state, dict) and state.get("collection_mode") == mode and state.get("batch_id"):
        return str(state["batch_id"]), work_root, state, True
    if work_root.exists() and any(work_root.iterdir()):
        raise CorpusError("untracked active staging workspace requires inspection")
    state = {
        "schema_version": 1,
        "batch_id": preferred_batch_id,
        "collection_mode": mode,
        "started_at": now.isoformat(),
    }
    _atomic_json(state_path, state)
    return preferred_batch_id, work_root, state, False


def _discard_lane_stage(data_dir: Path, mode: str) -> None:
    lane_root, work_root, state_path = _lane_paths(data_dir, mode)
    if work_root.exists() and work_root.parent.resolve() == lane_root.resolve() and work_root.name == "active":
        shutil.rmtree(work_root)
    state_path.unlink(missing_ok=True)


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
