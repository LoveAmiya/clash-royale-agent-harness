"""Filesystem and identity primitives shared by rolling materialization."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def docs_fingerprint(documents: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(documents, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, value: object) -> None:
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


def generation_id(store: Any, now: datetime) -> str:
    summaries = store.dataset_summaries(now=now)
    fingerprint = hashlib.sha256(
        json.dumps(summaries, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"pol-{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{fingerprint}"


def prune_group_versions(data_dir: Path, active_group_id: str, keep: int = 2) -> list[str]:
    groups_root = Path(data_dir) / "snapshot_groups"
    published: list[tuple[str, str, Path]] = []
    for directory in groups_root.iterdir() if groups_root.is_dir() else ():
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        group_id = str(manifest.get("snapshot_group_id") or "")
        if group_id != directory.name or manifest.get("fully_aligned") is not True:
            continue
        published.append((str(manifest.get("published_at") or ""), group_id, directory))
    published.sort(reverse=True)
    retained = {active_group_id}
    for _, group_id, _ in published:
        if len(retained) >= max(1, keep):
            break
        retained.add(group_id)
    removed = []
    for _, group_id, directory in published:
        if group_id in retained:
            continue
        try:
            shutil.rmtree(directory)
        except OSError:
            continue
        removed.append(group_id)
    return removed


__all__ = [
    "atomic_json",
    "docs_fingerprint",
    "generation_id",
    "iso",
    "prune_group_versions",
    "sha256_file",
]
