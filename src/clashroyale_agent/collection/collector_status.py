"""Status and heartbeat helpers for the rolling collector."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


STATUS_HEARTBEAT_SECONDS = 60.0


def bounded_fetch_concurrency(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else 1
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, 4))


def elapsed_seconds(started_at: float) -> float:
    return round(max(0.0, time.monotonic() - started_at), 3)


def collection_status_payload(*, mode: str, trigger_batch_id: str, effective_batch_id: str, resumed: bool, stage: str, status: str, **fields) -> dict:
    return {
        "schema_version": 1, "status": status, "stage": stage, "batch_id": effective_batch_id,
        "trigger_batch_id": trigger_batch_id, "collection_mode": mode, "resumed": resumed,
        "updated_at": datetime.now(timezone.utc).isoformat(), **fields,
    }


def batch_baseline(*, snapshot: dict, imported: dict, performance: dict, staging: dict) -> dict:
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


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                Path(temporary_name).replace(path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (attempt + 1))
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


class CollectionStatusReporter:
    def __init__(self, path: Path, *, base_fields: dict, heartbeat_interval_seconds: float = STATUS_HEARTBEAT_SECONDS) -> None:
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
        stage_elapsed = {"stage_elapsed_seconds": elapsed_seconds(self._stage_started_at)} if self._stage_started_at is not None else {}
        _atomic_json(self.path, {**self.base_fields, **self._fields, **stage_elapsed, "heartbeat_sequence": self._sequence, "updated_at": datetime.now(timezone.utc).isoformat()})

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_seconds):
            with self._lock:
                self._write_locked()

    def start(self, **fields) -> None:
        self.update(**fields)
        if self._thread is None:
            self._thread = threading.Thread(target=self._heartbeat, name="collection-status-heartbeat", daemon=True)
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
                self._fields["stage_elapsed_seconds"] = elapsed_seconds(self._stage_started_at)
                self._write_locked()
            self.stop()
            self._stage_started_at = None


__all__ = ["CollectionStatusReporter", "batch_baseline", "bounded_fetch_concurrency", "collection_status_payload", "elapsed_seconds"]
