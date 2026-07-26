"""Export reviewed-input candidates from durable feedback without mutating cases.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from feedback_store import FeedbackStore


_REVIEW_FIELDS = {
    "review_status",
    "reviewer_note",
    "expected_intent",
    "expected_skill",
    "expected_fields",
    "expected_subqueries",
    "answer_contains",
}


def _load_existing_reviews(output: Path) -> dict[str, dict]:
    if not output.exists():
        return {}
    reviewed: dict[str, dict] = {}
    for line_number, line in enumerate(output.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid existing feedback candidate on line {line_number}") from exc
        candidate_id = str(item.get("id", ""))
        if candidate_id:
            review_status = str(item.get("review_status", "pending"))
            if review_status in {"approved", "rejected"}:
                reviewed[candidate_id] = {key: item[key] for key in _REVIEW_FIELDS if key in item}
    return reviewed


def export_candidates(database: Path, output: Path, *, limit: int = 1000) -> int:
    records = FeedbackStore(database).list_correction_candidates(limit=limit)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing_reviews = _load_existing_reviews(output)
    lines = []
    for record in records:
        parsed = record["parsed"] if isinstance(record.get("parsed"), dict) else {}
        expected_fields = {
            key: parsed[key]
            for key in (
                "metric", "metrics", "compare_metric", "rank", "top_n", "card_name",
                "card_names", "round", "date", "ask_players",
            )
            if key in parsed and parsed[key] is not None
        }
        expected_subqueries = []
        if parsed.get("intent") == "multi_intent":
            expected_subqueries = [
                {
                    "intent": item.get("intent"),
                    "card_name": item.get("card_name"),
                    "metrics": item.get("metrics"),
                }
                for item in parsed.get("subqueries", [])
                if isinstance(item, dict)
            ]
        candidate_id = f"feedback-{record['feedback_id']}"
        candidate = {
                    "id": candidate_id,
                    "category": "real_user_feedback",
                    "question": record["question"],
                    "expected_intent": parsed.get("intent"),
                    "expected_fields": expected_fields,
                    **({"expected_subqueries": expected_subqueries} if expected_subqueries else {}),
                    "answer_contains": [],
                    "observed_answer": record["answer"],
                    "expected_correction": record["correction"],
                    "observed_parse": parsed,
                    "snapshot_id": record["snapshot_id"],
                    "request_id": record["request_id"],
                    "review_status": "pending",
                }
        candidate.update(existing_reviews.get(candidate_id, {}))
        lines.append(json.dumps(candidate, ensure_ascii=False, sort_keys=True))
    output.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    return len(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "feedback.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation" / "feedback_candidates.jsonl")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    count = export_candidates(args.database, args.output, limit=args.limit)
    print(json.dumps({"exported": count, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
