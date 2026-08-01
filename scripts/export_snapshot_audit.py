"""Export the active snapshot audit package without network or model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snapshot_audit import DEFAULT_PARTITION_SIZE, SnapshotAuditError, export_snapshot_audit


def _active_snapshot_id(data_dir: Path) -> str:
    pointer = json.loads((data_dir / "official_snapshot_pointer.json").read_text(encoding="utf-8"))
    value = str(pointer.get("snapshot_id") or "").strip()
    if not value:
        raise SnapshotAuditError("active snapshot pointer has no snapshot_id")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--partition-size", type=int, default=DEFAULT_PARTITION_SIZE)
    args = parser.parse_args()
    try:
        snapshot_id = args.snapshot_id or _active_snapshot_id(args.data_dir)
        manifest = export_snapshot_audit(
            args.data_dir,
            snapshot_id,
            partition_size=args.partition_size,
        )
    except (OSError, json.JSONDecodeError, SnapshotAuditError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "status": "ready",
                "snapshot_id": manifest["snapshot_id"],
                "export_dir": str(args.data_dir / "audit_exports" / manifest["snapshot_id"]),
                "counts": manifest["counts"],
                "cost_boundaries": manifest["cost_boundaries"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
