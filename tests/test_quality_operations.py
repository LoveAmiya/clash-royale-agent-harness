import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import install_test_stubs

install_test_stubs()

from fastapi.testclient import TestClient

from feedback_store import FeedbackStore, RecentAnswerCache
from logging_config import SecretRedactionFilter
import runtime_multi


class QualityOperationsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(runtime_multi.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_feedback_api_accepts_only_server_owned_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_multi.app.state.recent_answers = RecentAnswerCache()
            runtime_multi.app.state.feedback_store = FeedbackStore(Path(directory) / "feedback.sqlite3")
            runtime_multi.app.state.feedback_store.register_answer({
                "request_id": "req-owned",
                "question": "q",
                "answer": "a",
                "snapshot_id": "snapshot-1",
                "parsed": {"intent": "card_query"},
                "selected_skill": "CardMetaSkill",
            })
            accepted = self.client.post("/feedback", json={"request_id": "req-owned", "rating": "positive"})
            rejected = self.client.post("/feedback", json={"request_id": "forged", "rating": "positive"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(rejected.status_code, 404)

    def test_logging_filter_redacts_provider_credentials(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sensitive-key", "SUPERCELL_API_TOKENS": "core-secret;expanded-secret"},
        ):
            redactor = SecretRedactionFilter()
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "failed sensitive-key core-secret;expanded-secret", (), None
        )
        self.assertTrue(redactor.filter(record))
        self.assertEqual(record.getMessage(), "failed [REDACTED] [REDACTED]")

    def test_model_status_and_metrics_do_not_expose_provider_url(self):
        status = self.client.get("/model/status")
        metrics = self.client.get("/metrics")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["provider_id"].startswith("provider-"))
        self.assertNotIn("http", status.text.lower())
        self.assertIn("cr_agent_model_provider_circuit", metrics.text)


if __name__ == "__main__":
    unittest.main()
