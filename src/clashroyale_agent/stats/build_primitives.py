"""Filesystem and identity primitives used by structured-stat publishing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path


SAFE_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path, error_type: type[ValueError]) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type(f"cannot read JSON file: {path.name}") from exc
    if not isinstance(value, dict):
        raise error_type(f"JSON file must contain an object: {path.name}")
    return value


def snapshot_id(value: str, error_type: type[ValueError]) -> str:
    normalized = str(value or "").strip()
    if not normalized or not SAFE_SNAPSHOT_ID.fullmatch(normalized):
        raise error_type("invalid snapshot_id")
    return normalized


def publish_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        backup = destination.with_name(f".{destination.name}.previous-{time.time_ns()}")
        os.replace(destination, backup)
        try:
            os.replace(source, destination)
        except Exception:
            os.replace(backup, destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 5 or destination.exists():
                raise
            time.sleep(0.05 * (attempt + 1))


__all__ = ["SAFE_SNAPSHOT_ID", "publish_directory", "read_json", "sha256", "snapshot_id", "write_json"]
