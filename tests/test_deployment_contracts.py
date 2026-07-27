import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LoadTestContractTests(unittest.TestCase):
    def test_load_topology_exercises_two_apis_through_shared_redis(self):
        compose = (ROOT / "compose.loadtest.yml").read_text(encoding="utf-8")

        self.assertIn("api-1:", compose)
        self.assertIn("api-2:", compose)
        self.assertIn("PROCESS_QUOTA_BACKEND: redis", compose)
        self.assertIn("REDIS_URL: redis://redis:6379/0", compose)
        self.assertIn('"127.0.0.1:18091:80"', compose)

    def test_every_k6_profile_has_failure_and_latency_thresholds(self):
        for name in ("smoke", "load", "soak"):
            script = (ROOT / "load" / "k6" / f"{name}.js").read_text(encoding="utf-8")
            with self.subTest(profile=name):
                self.assertIn("thresholds", script)
                self.assertIn("process_failures", script)
                self.assertIn("process_duration", script)
                self.assertIn("process_ttfb", script)
                self.assertIn("handleSummary", script)

    def test_load_driver_preserves_a_timestamped_report_on_threshold_failure(self):
        driver = (ROOT / "run_load_test.ps1").read_text(encoding="utf-8")

        self.assertIn('Get-Date -Format "yyyyMMdd-HHmmss"', driver)
        self.assertIn("report retained", driver)
        self.assertIn("SUMMARY_PATH", driver)


class AlertingContractTests(unittest.TestCase):
    def test_prometheus_routes_alerts_to_persistent_alertmanager_webhook(self):
        prometheus = (ROOT / "deploy" / "prometheus.yml").read_text(encoding="utf-8")
        alertmanager = (ROOT / "deploy" / "alertmanager.yml").read_text(encoding="utf-8")
        compose = (ROOT / "compose.production.yml").read_text(encoding="utf-8")

        self.assertIn('targets: ["alertmanager:9093"]', prometheus)
        self.assertIn("url: http://alert-webhook:8094/alerts", alertmanager)
        self.assertIn("send_resolved: true", alertmanager)
        self.assertIn("alertmanager_data:/alertmanager", compose)
        self.assertIn("alert_notifications:/var/lib/alert-receiver", compose)

    def test_alert_drill_has_a_bounded_delivery_deadline(self):
        script = (ROOT / "test_alert_pipeline.ps1").read_text(encoding="utf-8")
        drill_rules = (ROOT / "deploy" / "prometheus-alerts-drill.yml").read_text(encoding="utf-8")
        drill_config = (ROOT / "deploy" / "prometheus.drill.yml").read_text(encoding="utf-8")

        self.assertIn("compose.alert-drill.yml", script)
        self.assertIn("prometheus", script)
        self.assertIn("AddSeconds(90)", script)
        self.assertIn("received_total", script)
        self.assertIn("down --volumes", script)
        self.assertIn("expr: vector(1)", drill_rules)
        self.assertIn("evaluation_interval: 1s", drill_config)


if __name__ == "__main__":
    unittest.main()
