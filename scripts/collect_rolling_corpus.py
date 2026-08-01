"""Collect one strict Path of Legend batch into the rolling fact store."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_config import (
    SUPERCELL_API_TIMEOUT_SECONDS,
    SUPERCELL_API_TOKEN,
    SUPERCELL_BATTLES_PER_PLAYER,
    SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
    SUPERCELL_HIGH_VOLUME_MAX_RETRIES,
    SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
    SUPERCELL_LEADERBOARD_PLAYERS,
)
from rolling_corpus import (
    BatchValidationPolicy,
    CorpusError,
    CorpusWriterBusyError,
    CorpusWriterLock,
    RollingCorpusStore,
)
from rolling_materializer import build_snapshot_group
from supercell_live import SupercellAPIClient


SHANGHAI = ZoneInfo("Asia/Shanghai")


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


def _batch_id(mode: str, now: datetime) -> str:
    local = now.astimezone(SHANGHAI)
    return f"{mode}-{local:%Y%m%d}"


def collect(mode: str, *, data_dir: Path, batch_id: str | None = None) -> dict:
    if mode not in {"daily_ranked", "weekly_expanded"}:
        raise CorpusError("mode must be daily_ranked or weekly_expanded")
    if not SUPERCELL_API_TOKEN:
        raise CorpusError("SUPERCELL_API_TOKEN is not configured")

    data_dir = Path(data_dir)
    corpus_dir = data_dir / "corpus"
    status_path = corpus_dir / "collection_status.json"
    now = datetime.now(timezone.utc)
    preferred_batch_id = batch_id or _batch_id(mode, now)
    target_battles = 200_000 if mode == "weekly_expanded" else 25_000
    player_limit = SUPERCELL_LEADERBOARD_PLAYERS if mode == "weekly_expanded" else 1000
    expand_opponents = mode == "weekly_expanded"

    with CorpusWriterLock(corpus_dir / "writer.lock"):
        store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
        try:
            capacity = store.assert_disk_capacity()
            resolved_batch_id = store.unique_batch_id(preferred_batch_id)
            work_root = data_dir / "rolling_work" / resolved_batch_id

            def progress_callback(progress: dict) -> None:
                _atomic_json(
                    status_path,
                    {
                        "schema_version": 1,
                        "batch_id": resolved_batch_id,
                        "collection_mode": mode,
                        **progress,
                    },
                )

            progress_callback(
                {
                    "status": "starting",
                    "target_battles": target_battles,
                    "usable_battles": 0,
                    "updated_at": now.isoformat(),
                }
            )
            client = SupercellAPIClient(
                SUPERCELL_API_TOKEN,
                timeout_seconds=SUPERCELL_API_TIMEOUT_SECONDS,
                max_retries=max(2, SUPERCELL_HIGH_VOLUME_MAX_RETRIES),
                requests_per_second=SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND,
            )
            snapshot = client.fetch_snapshot(
                target_battles=target_battles,
                player_limit=player_limit,
                seed_player_limit=1000,
                battles_per_player=SUPERCELL_BATTLES_PER_PLAYER,
                concurrency=1,
                max_duration_seconds=SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS,
                progress_callback=progress_callback,
                progress_interval_seconds=3600,
                spool_dir=work_root,
                collection_mode=mode,
                expand_opponents=expand_opponents,
                strict_battle_contract=True,
                ranked_tail_retry_rounds=1,
            )
            if (
                snapshot.get("collection_scope") != "path_of_legend"
                or snapshot.get("scope_contract") != "path_of_legend_only_v1"
                or snapshot.get("scope_verified") is not True
            ):
                raise CorpusError("collector did not satisfy the strict Path of Legend scope contract")
            completed_at = datetime.now(timezone.utc)
            imported = store.import_workspace_batch(
                Path(snapshot["_aggregate_store_path"]),
                batch_id=resolved_batch_id,
                batch_type=mode,
                started_at=now,
                leaderboard_frozen_at=now,
                observed_at=completed_at,
            )
            metrics = snapshot.get("collection_metrics", {})
            report = store.finalize_batch(
                resolved_batch_id,
                completed_at=completed_at,
                policy=BatchValidationPolicy(),
                request_count=int(metrics.get("request_count") or 0),
                rate_limited=int(metrics.get("rate_limited") or 0),
                refresh_budget_exhausted=bool(metrics.get("refresh_budget_exhausted")),
                source_exhausted=bool(metrics.get("source_exhausted")),
            )
            retention = store.expire_and_prune(now=completed_at)
            publication = None
            publication_error = None
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
                },
            )
            try:
                manifest = build_snapshot_group(store, data_dir=data_dir, now=completed_at)
                publication = {
                    "status": "published",
                    "snapshot_group_id": manifest["snapshot_group_id"],
                    "dataset_count": len(manifest["datasets"]),
                    "fully_aligned": manifest.get("fully_aligned") is True,
                }
            except Exception as exc:
                publication_error = {"error_type": type(exc).__name__, "message": str(exc)}
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
            }
            _atomic_json(status_path, result)
            return result
        finally:
            store.close()


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily_ranked", "weekly_expanded"), required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--batch-id")
    args = parser.parse_args()
    try:
        result = collect(args.mode, data_dir=args.data_dir, batch_id=args.batch_id)
    except CorpusWriterBusyError as exc:
        print(
            json.dumps(
                {
                    "status": "skipped_already_running",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "cost_boundaries": {"cloud_llm_calls": 0, "cloud_embedding_calls": 0},
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        try:
            maintenance = maintain_windows_after_failed_collection(data_dir=args.data_dir)
        except Exception as maintenance_exc:
            maintenance = {
                "status": "failed",
                "error_type": type(maintenance_exc).__name__,
                "message": str(maintenance_exc),
            }
        error = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "maintenance": maintenance,
            "cost_boundaries": {"cloud_llm_calls": 0, "cloud_embedding_calls": 0},
        }
        _atomic_json(args.data_dir / "corpus" / "collection_status.json", error)
        print(json.dumps(error, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "accepted" else 2 if result["status"] == "rejected" else 3


if __name__ == "__main__":
    raise SystemExit(main())
