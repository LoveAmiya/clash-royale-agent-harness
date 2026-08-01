"""Export one published rolling scope as JSONL plus a SHA-256 manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rolling_corpus import CorpusWriterLock, DATASET_SCOPES, RollingCorpusStore


def export_scope(data_dir: Path, scope: str, output_dir: Path) -> dict:
    data_dir = Path(data_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now(timezone.utc)
    corpus_dir = data_dir / "corpus"
    output_path = output_dir / f"{scope}.jsonl"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{scope}.", suffix=".jsonl.tmp", dir=output_dir)
    digest = hashlib.sha256()
    count = 0
    try:
        with CorpusWriterLock(corpus_dir / "writer.lock"):
            store = RollingCorpusStore(corpus_dir / "corpus.sqlite")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    for record in store.iter_scope_battles(scope, now=exported_at):
                        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                        handle.write(line)
                        digest.update(line)
                        count += 1
                    handle.flush()
                    os.fsync(handle.fileno())
                summary = store.dataset_summary(scope, now=exported_at)
            finally:
                store.close()
        os.replace(temporary_name, output_path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    manifest = {
        "schema_version": 1,
        "dataset_scope": scope,
        "exported_at": exported_at.isoformat(),
        "record_count": count,
        "sha256": digest.hexdigest(),
        "file": output_path.name,
        "dataset": summary,
    }
    manifest_path = output_dir / f"{scope}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=DATASET_SCOPES, required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "rolling_data_exports")
    args = parser.parse_args()
    try:
        manifest = export_scope(args.data_dir, args.scope, args.output_dir)
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "exported", **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
