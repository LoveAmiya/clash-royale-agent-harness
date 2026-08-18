import tempfile
import unittest
from pathlib import Path

from feedback_store import FeedbackStore, RecentAnswerCache
from clashroyale_agent.ops.feedback_store import (
    FeedbackStore as PackagedFeedbackStore,
    RecentAnswerCache as PackagedRecentAnswerCache,
)


class FeedbackStoreTests(unittest.TestCase):
    def test_feedback_store_wrapper_reexports_packaged_implementation(self):
        self.assertIs(FeedbackStore, PackagedFeedbackStore)
        self.assertIs(RecentAnswerCache, PackagedRecentAnswerCache)

    def test_feedback_resolves_server_owned_answer_and_persists(self):
        cache = RecentAnswerCache(max_items=2, ttl_seconds=60)
        cache.put(
            request_id="req-1",
            question="雷电巨人的使用率？",
            answer="4.0%",
            snapshot_id="snapshot-1",
            parsed={"intent": "card_query"},
        )
        with tempfile.TemporaryDirectory() as directory:
            store = FeedbackStore(Path(directory) / "feedback.sqlite3")
            record = store.submit(
                answer=cache.get("req-1"),
                rating="negative",
                correction="该答案应明确样本边界。",
            )
            self.assertEqual(record["request_id"], "req-1")
            self.assertEqual(record["snapshot_id"], "snapshot-1")
            self.assertEqual(store.stats()["negative"], 1)
            candidates = store.list_correction_candidates(limit=10)
            self.assertEqual(candidates[0]["question"], "雷电巨人的使用率？")
            self.assertNotIn("api_key", str(candidates[0]).lower())

    def test_cache_is_bounded_and_rejects_unknown_request(self):
        cache = RecentAnswerCache(max_items=1, ttl_seconds=60)
        cache.put(request_id="req-1", question="q1", answer="a1")
        cache.put(request_id="req-2", question="q2", answer="a2")
        self.assertIsNone(cache.get("req-1"))
        self.assertEqual(cache.get("req-2")["answer"], "a2")

    def test_feedback_validates_rating_and_correction_length(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FeedbackStore(Path(directory) / "feedback.sqlite3", max_correction_chars=5)
            answer = {"request_id": "req", "question": "q", "answer": "a"}
            with self.assertRaises(ValueError):
                store.submit(answer=answer, rating="maybe")
            with self.assertRaises(ValueError):
                store.submit(answer=answer, rating="negative", correction="123456")

    def test_recent_answer_is_shared_through_sqlite_for_multiple_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feedback.sqlite3"
            writer = FeedbackStore(path)
            reader = FeedbackStore(path)
            writer.register_answer({
                "request_id": "req-shared",
                "question": "q",
                "answer": "a",
                "snapshot_id": "snapshot-1",
                "parsed": {"intent": "card_query"},
                "selected_skill": "CardMetaSkill",
            })
            self.assertEqual(reader.get_answer("req-shared")["selected_skill"], "CardMetaSkill")


if __name__ == "__main__":
    unittest.main()
