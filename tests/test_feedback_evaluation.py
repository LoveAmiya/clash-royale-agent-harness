import json
import tempfile
import unittest
from pathlib import Path

from evaluation.export_feedback_cases import export_candidates
from evaluation.run_eval import load_approved_feedback_cases
from feedback_store import FeedbackStore


class FeedbackEvaluationTests(unittest.TestCase):
    def test_export_is_pending_until_explicit_review(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "feedback.sqlite3"
            output = Path(directory) / "candidates.jsonl"
            store = FeedbackStore(database)
            answer = {
                "request_id": "req-1",
                "question": "雷电巨人使用率",
                "answer": "answer",
                "snapshot_id": "snapshot-1",
                "parsed": {"intent": "card_query", "card_name": "Electro Giant", "metrics": ["usage_rate"]},
            }
            store.submit(answer=answer, rating="negative", correction="应明确样本边界")
            self.assertEqual(export_candidates(database, output), 1)
            self.assertEqual(load_approved_feedback_cases(output), [])

            candidate = json.loads(output.read_text(encoding="utf-8"))
            candidate["review_status"] = "approved"
            output.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            approved = load_approved_feedback_cases(output)
            self.assertEqual(approved[0]["expected_intent"], "card_query")
            self.assertEqual(approved[0]["expected_fields"]["card_name"], "Electro Giant")

            candidate["answer_contains"] = ["样本边界"]
            candidate["reviewer_note"] = "稳定断言，不包含会随快照变化的具体数值"
            output.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual(export_candidates(database, output), 1)
            reexported = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(reexported["review_status"], "approved")
            self.assertEqual(reexported["answer_contains"], ["样本边界"])
            self.assertEqual(reexported["reviewer_note"], candidate["reviewer_note"])


if __name__ == "__main__":
    unittest.main()
