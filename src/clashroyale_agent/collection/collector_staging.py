"""Rolling collector staging-lane filesystem helpers."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable


def lane_paths(data_dir: Path, mode: str) -> tuple[Path, Path, Path]:
    lane_root = Path(data_dir) / "rolling_lanes" / mode
    return lane_root, lane_root / "active", lane_root / "active_batch.json"


def directory_size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def staging_limit_bytes(mode: str, configured_limits: dict[str, int]) -> int:
    configured = os.getenv(f"SUPERCELL_{mode.upper()}_STAGING_MAX_BYTES", "").strip()
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            pass
    return configured_limits[mode]


def prepare_lane_stage(
    data_dir: Path,
    mode: str,
    preferred_batch_id: str,
    now: datetime,
    *,
    total_staging_limit: int,
    atomic_json: Callable[[Path, dict], None],
    error_type: type[ValueError] = ValueError,
) -> tuple[str, Path, dict, bool]:
    lane_root, work_root, state_path = lane_paths(data_dir, mode)
    lane_root.mkdir(parents=True, exist_ok=True)
    if directory_size_bytes(Path(data_dir) / "rolling_lanes") > total_staging_limit:
        raise error_type("total staging storage limit exceeded")
    state = None
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise error_type("active staging state is unreadable") from exc
    if isinstance(state, dict) and state.get("collection_mode") == mode and state.get("batch_id"):
        return str(state["batch_id"]), work_root, state, True
    if work_root.exists() and any(work_root.iterdir()):
        raise error_type("untracked active staging workspace requires inspection")
    state = {"schema_version": 1, "batch_id": preferred_batch_id, "collection_mode": mode, "started_at": now.isoformat()}
    atomic_json(state_path, state)
    return preferred_batch_id, work_root, state, False


def discard_lane_stage(data_dir: Path, mode: str) -> None:
    lane_root, work_root, state_path = lane_paths(data_dir, mode)
    if work_root.exists() and work_root.parent.resolve() == lane_root.resolve() and work_root.name == "active":
        shutil.rmtree(work_root)
    state_path.unlink(missing_ok=True)


__all__ = ["directory_size_bytes", "discard_lane_stage", "lane_paths", "prepare_lane_stage", "staging_limit_bytes"]
