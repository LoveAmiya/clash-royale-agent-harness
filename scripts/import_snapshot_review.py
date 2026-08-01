"""Validate and stage ChatGPT-web reviewed RAG documents locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snapshot_audit import (
    ExternalReviewValidationError,
    SnapshotAuditError,
    import_reviewed_rag_documents,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_id")
    parser.add_argument("reviewed_documents", type=Path)
    parser.add_argument("--review-notes", type=Path)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    try:
        report = import_reviewed_rag_documents(
            args.data_dir,
            args.snapshot_id,
            args.reviewed_documents,
            review_notes_path=args.review_notes,
        )
    except ExternalReviewValidationError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        return 2
    except (OSError, json.JSONDecodeError, SnapshotAuditError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
