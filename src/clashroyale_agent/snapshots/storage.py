"""Atomic storage primitives used by snapshot publication."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable


def write_streaming_snapshot_json(handle: Any, payload: dict, *, record_type: type) -> None:
    public_items = [(key, value) for key, value in payload.items() if not str(key).startswith("_")]
    handle.write("{")
    for index, (key, value) in enumerate(public_items):
        if index:
            handle.write(",")
        handle.write("\n  ")
        json.dump(key, handle, ensure_ascii=False)
        handle.write(": ")
        if isinstance(value, record_type):
            handle.write("[")
            actual_count = 0
            for record in value:
                if actual_count:
                    handle.write(",")
                handle.write("\n    ")
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
                actual_count += 1
            if actual_count:
                handle.write("\n  ")
            handle.write("]")
            if actual_count != len(value):
                raise ValueError(f"streamed raw battle count mismatch: expected={len(value)} actual={actual_count}")
        else:
            json.dump(value, handle, ensure_ascii=False, indent=2)
    handle.write("\n}\n")


def atomic_write_json(path: Path, payload: object, *, record_type: type) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            if isinstance(payload, dict) and isinstance(payload.get("raw_battles"), record_type):
                write_streaming_snapshot_json(handle, payload, record_type=record_type)
            else:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp_name)
        with open(temp_name, "rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def archive_published_snapshot(
    data_dir: Path,
    snapshot: dict,
    documents: list[dict],
    *,
    archive_dir_name: str,
    atomic_write: Callable[[Path, object], None],
    atomic_copy: Callable[[Path, Path], None],
    collector_summary: Callable[[dict], dict],
) -> None:
    snapshot_id = str(snapshot["snapshot_id"])
    archive_dir = data_dir / archive_dir_name / snapshot_id
    atomic_write(archive_dir / "snapshot.json", snapshot)
    atomic_write(archive_dir / "rag_documents.json", documents)
    aggregate_source = snapshot.get("_aggregate_store_path")
    if isinstance(aggregate_source, str) and Path(aggregate_source).is_file():
        atomic_copy(Path(aggregate_source), archive_dir / "aggregates.sqlite")
    atomic_write(archive_dir / "collector_snapshot.json", collector_summary(snapshot))
    atomic_write(
        archive_dir / "manifest.json",
        {
            "schema_version": 1,
            "snapshot_id": snapshot_id,
            "published_at": snapshot.get("published_at"),
            "fetched_at": snapshot.get("fetched_at"),
            "sample_battles": snapshot.get("sample_battles"),
            "rag_docs_fingerprint": snapshot.get("rag_docs_fingerprint"),
            "complete": True,
        },
    )
