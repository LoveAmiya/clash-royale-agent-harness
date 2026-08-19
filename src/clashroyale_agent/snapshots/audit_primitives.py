"""Filesystem and identity primitives for local snapshot audit exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path


SAFE_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def validated_snapshot_id(snapshot_id: str, error_type: type[ValueError]) -> str:
    value = str(snapshot_id or "").strip()
    if not value or not SAFE_SNAPSHOT_ID.fullmatch(value):
        raise error_type("invalid snapshot_id")
    return value


def read_json(path: Path, error_type: type[ValueError]) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type(f"cannot read JSON file: {path.name}") from exc


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish_directory(source: Path, destination: Path) -> None:
    """Atomically publish a new directory, tolerating brief Windows file locks."""
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 5 or destination.exists():
                raise
            time.sleep(0.05 * (attempt + 1))


__all__ = ["SAFE_SNAPSHOT_ID", "publish_directory", "read_json", "sha256", "validated_snapshot_id", "write_json"]
