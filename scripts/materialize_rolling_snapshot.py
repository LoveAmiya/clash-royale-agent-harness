"""Build and atomically publish all ten rolling dataset scopes."""

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
from rolling_materializer import build_snapshot_group


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    corpus_dir = args.data_dir / "corpus"
    try:
        with CorpusWriterLock(corpus_dir / "writer.lock"):
            store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
            try:
                store.expire_and_prune(now=datetime.now(timezone.utc))
                manifest = build_snapshot_group(store, data_dir=args.data_dir)
            finally:
                store.close()
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "published", "snapshot_group_id": manifest["snapshot_group_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
