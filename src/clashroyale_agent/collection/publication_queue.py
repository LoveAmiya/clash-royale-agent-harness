"""Durable, single-consumer queue for accepted batches awaiting publication."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def queue_path(data_dir: Path) -> Path:
    return Path(data_dir) / "corpus" / "pending_publications.json"


def _read(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _write(path: Path, value: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def enqueue(data_dir: Path, *, mode: str, batch_id: str, queued_at: datetime | None = None) -> dict:
    path = queue_path(data_dir)
    entries = _read(path)
    for entry in entries:
        if entry.get("mode") == mode and entry.get("batch_id") == batch_id:
            return entry
    current = queued_at or datetime.now(timezone.utc)
    entry = {
        "schema_version": 1,
        "mode": str(mode),
        "batch_id": str(batch_id),
        "queued_at": current.astimezone(timezone.utc).isoformat(),
    }
    _write(path, [*entries, entry])
    return entry


def pending(data_dir: Path) -> list[dict]:
    return _read(queue_path(data_dir))


def remove(data_dir: Path, *, mode: str, batch_id: str) -> None:
    path = queue_path(data_dir)
    remaining = [
        entry
        for entry in _read(path)
        if not (entry.get("mode") == mode and entry.get("batch_id") == batch_id)
    ]
    if remaining:
        _write(path, remaining)
    else:
        path.unlink(missing_ok=True)


__all__ = ["enqueue", "pending", "queue_path", "remove"]
