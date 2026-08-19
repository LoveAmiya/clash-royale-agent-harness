"""Retention policy for published snapshot packages."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


def _complete_archive_manifests(
    data_dir: Path,
    *,
    archive_dir_name: str,
    parse_timestamp: Callable[[object], datetime | None],
) -> list[dict]:
    archive_root = data_dir / archive_dir_name
    if not archive_root.exists():
        return []
    manifests: list[dict] = []
    for archive_dir in archive_root.iterdir():
        if not archive_dir.is_dir():
            continue
        try:
            manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot_id = str(manifest.get("snapshot_id") or "").strip()
        if snapshot_id != archive_dir.name or manifest.get("complete") is not True:
            continue
        published_at = parse_timestamp(manifest.get("published_at") or manifest.get("fetched_at"))
        if published_at is None:
            continue
        manifests.append({**manifest, "snapshot_id": snapshot_id, "_published_at": published_at})
    return manifests


def cleanup_snapshot_retention(
    data_dir: Path,
    *,
    active_snapshot_id: str,
    retention_days: int,
    retention_max_complete: int,
    archive_dir_name: str,
    storage_roots: tuple[str, ...],
    parse_timestamp: Callable[[object], datetime | None],
    now: datetime | None = None,
) -> dict:
    """Keep the active and newest previous complete snapshot package only."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    manifests = sorted(
        _complete_archive_manifests(
            data_dir,
            archive_dir_name=archive_dir_name,
            parse_timestamp=parse_timestamp,
        ),
        key=lambda item: item["_published_at"],
        reverse=True,
    )
    active = next((item for item in manifests if item["snapshot_id"] == active_snapshot_id), None)
    previous = next((item for item in manifests if item["snapshot_id"] != active_snapshot_id), None)
    retained = [item for item in (active, previous) if item is not None]
    retained_ids = {item["snapshot_id"] for item in retained}
    removed_ids: list[str] = []
    for manifest in manifests:
        snapshot_id = manifest["snapshot_id"]
        age = current - manifest["_published_at"]
        over_age = age >= timedelta(days=retention_days)
        over_count = snapshot_id not in retained_ids
        if snapshot_id == active_snapshot_id or not (over_age or over_count):
            continue
        if previous is not None and snapshot_id == previous["snapshot_id"]:
            continue
        for root_name in storage_roots:
            target = data_dir / root_name / snapshot_id
            if target.is_dir():
                shutil.rmtree(target)
        removed_ids.append(snapshot_id)
    return {
        "retention_days": retention_days,
        "max_complete_snapshots": retention_max_complete,
        "retained_snapshot_ids": [item["snapshot_id"] for item in retained],
        "removed_snapshot_ids": removed_ids,
    }


__all__ = ["cleanup_snapshot_retention"]
