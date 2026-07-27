import json
import tempfile
import unittest
from pathlib import Path

from alert_receiver import normalize_alert_payload, persist_alert, summarize_alert_store


class AlertReceiverTests(unittest.TestCase):
    def test_persisted_alert_is_bounded_and_redacts_sensitive_fields(self):
        payload = {
            "status": "firing",
            "receiver": "operations-webhook",
            "groupKey": "group",
            "commonLabels": {"service": "clash-agent", "api_key": "secret"},
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "TestAlert", "token": "secret"},
                    "annotations": {"summary": "test"},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.jsonl"
            record = persist_alert(payload, path)
            persisted = json.loads(path.read_text(encoding="utf-8"))
            persisted_summary = summarize_alert_store(path)

        self.assertEqual(record["alerts"][0]["labels"]["token"], "[redacted]")
        self.assertEqual(persisted["commonLabels"]["api_key"], "[redacted]")
        self.assertNotIn("secret", json.dumps(persisted))
        self.assertEqual(persisted_summary, (1, persisted["received_at"]))

    def test_invalid_alertmanager_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_alert_payload({"status": "firing"})


if __name__ == "__main__":
    unittest.main()
