import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clashroyale_agent.api.feedback_routes import register_feedback_routes


class _FeedbackStore:
    def __init__(self):
        self.answers = {"stored-1": {"request_id": "stored-1", "answer": "stored"}}
        self.submissions = []

    def get_answer(self, request_id):
        if request_id not in self.answers:
            raise LookupError("answer not found")
        return self.answers[request_id]

    def submit(self, *, answer, rating, correction):
        record = {"answer": answer, "rating": rating, "correction": correction}
        self.submissions.append(record)
        return {"feedback_id": "fb-1", "rating": rating}

    def stats(self):
        return {"total": len(self.submissions)}


class FeedbackRouteRegistrationTests(unittest.TestCase):
    def test_register_feedback_routes_records_feedback_and_exposes_stats(self):
        app = FastAPI()
        store = _FeedbackStore()
        recent_answers = {"recent-1": {"request_id": "recent-1", "answer": "recent"}}

        register_feedback_routes(
            app,
            get_recent_answers=lambda: recent_answers,
            get_feedback_store=lambda: store,
        )

        client = TestClient(app)
        try:
            recorded = client.post(
                "/feedback",
                json={"request_id": "recent-1", "rating": "positive", "correction": "ok"},
            )
            stats = client.get("/feedback/stats")
        finally:
            client.close()

        self.assertEqual(recorded.status_code, 200)
        self.assertEqual(recorded.json(), {"status": "recorded", "feedback_id": "fb-1", "rating": "positive"})
        self.assertEqual(store.submissions[0]["answer"], recent_answers["recent-1"])
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json(), {"total": 1})

    def test_register_feedback_routes_keep_submission_public_but_stats_admin_only(self):
        app = FastAPI()
        store = _FeedbackStore()

        register_feedback_routes(
            app,
            get_recent_answers=lambda: {},
            get_feedback_store=lambda: store,
            admin_api_key=lambda: "secret",
            authorize_admin=lambda expected, actual: expected == "secret" and actual == "secret",
        )

        client = TestClient(app)
        try:
            recorded = client.post("/feedback", json={"request_id": "stored-1", "rating": "positive"})
            denied = client.get("/feedback/stats")
            accepted = client.get("/feedback/stats", headers={"X-Admin-Key": "secret"})
        finally:
            client.close()

        self.assertEqual(recorded.status_code, 200)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["detail"], "administrator credentials required")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json(), {"total": 1})

    def test_register_feedback_routes_reports_missing_store_as_unavailable(self):
        app = FastAPI()
        register_feedback_routes(
            app,
            get_recent_answers=lambda: {},
            get_feedback_store=lambda: None,
        )

        client = TestClient(app)
        try:
            response = client.post("/feedback", json={"request_id": "req-1", "rating": "positive"})
        finally:
            client.close()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "feedback service is initializing")


if __name__ == "__main__":
    unittest.main()
