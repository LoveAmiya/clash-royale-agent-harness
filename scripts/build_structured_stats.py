"""Build the active snapshot's exact-8-card two-sided structured index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from structured_stats import StructuredStatsError, build_structured_stats


def _active_snapshot_id(data_dir: Path) -> str:
    pointer = json.loads((data_dir / "official_snapshot_pointer.json").read_text(encoding="utf-8"))
    snapshot_id = str(pointer.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise StructuredStatsError("active snapshot pointer has no snapshot_id")
    return snapshot_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()
    try:
        snapshot_id = args.snapshot_id or _active_snapshot_id(args.data_dir)
        manifest = build_structured_stats(args.data_dir, snapshot_id)
    except (OSError, json.JSONDecodeError, StructuredStatsError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "status": "ready",
                "snapshot_id": snapshot_id,
                "index_dir": str(args.data_dir / "structured_stats" / snapshot_id),
                "counts": manifest["counts"],
                "filters": manifest["filters"],
                "cost_boundaries": manifest["cost_boundaries"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
