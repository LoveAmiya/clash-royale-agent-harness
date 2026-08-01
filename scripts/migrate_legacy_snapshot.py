"""Import the frozen legacy official snapshot as an all-scope-only batch."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rolling_corpus import CorpusWriterLock, RollingCorpusStore


def migrate(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    pointer = json.loads((data_dir / "official_snapshot_pointer.json").read_text(encoding="utf-8"))
    snapshot_id = str(pointer.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise ValueError("legacy snapshot pointer has no snapshot_id")
    aggregate = data_dir / "snapshot_archives" / snapshot_id / "aggregates.sqlite"
    completed_at = pointer.get("published_at") or datetime.now(timezone.utc).isoformat()
    batch_id = f"legacy-{snapshot_id}"
    corpus_dir = data_dir / "corpus"
    with CorpusWriterLock(corpus_dir / "writer.lock"):
        store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
        try:
            existing = store.batch_status(batch_id)
            if existing == "collecting":
                observation_count = int(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM battle_observations WHERE batch_id=?",
                        (batch_id,),
                    ).fetchone()[0]
                )
                if observation_count == 0:
                    with store.connection:
                        store.connection.execute(
                            "DELETE FROM collection_batches WHERE batch_id=?",
                            (batch_id,),
                        )
                    existing = None
            if existing is not None:
                return {"status": "already_imported", "batch_id": batch_id, "batch_status": existing}
            report = store.import_legacy_archive(
                aggregate,
                batch_id=batch_id,
                completed_at=completed_at,
            )
            retention = store.expire_and_prune(now=datetime.now(timezone.utc))
            return {
                "status": "accepted" if report["passed"] else "rejected",
                "batch_id": batch_id,
                "snapshot_id": snapshot_id,
                "validation": report,
                "retention": retention,
            }
        finally:
            store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    try:
        result = migrate(args.data_dir)
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"accepted", "already_imported"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
