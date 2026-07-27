import unittest

from support import install_test_stubs

install_test_stubs()

import web_app


class WebVisualizationDashboardTests(unittest.TestCase):
    def test_frontend_exposes_data_quality_and_ops_visualizations(self):
        html = web_app.HTML_PAGE

        self.assertIn('aria-label="数据血缘与快照对齐"', html)
        self.assertIn('aria-label="RAG 质量门槛"', html)
        self.assertIn('aria-label="运行与模型状态"', html)
        self.assertIn('id="dataLineageViz"', html)
        self.assertIn('id="qualityGateViz"', html)
        self.assertIn('id="opsViz"', html)
        self.assertIn('function renderVisualizationDashboard', html)

    def test_frontend_uses_read_only_status_sources_for_visualizations(self):
        html = web_app.HTML_PAGE

        self.assertIn('fetch("/ready")', html)
        self.assertIn('fetch("/model/status")', html)
        self.assertIn('fetch("/metrics")', html)
        self.assertIn('fetch("/feedback/stats")', html)
        self.assertTrue(web_app.READY_STATUS_URL.endswith("/ready"))
        self.assertTrue(web_app.MODEL_STATUS_URL.endswith("/model/status"))
        self.assertTrue(web_app.FEEDBACK_STATS_URL.endswith("/feedback/stats"))


if __name__ == "__main__":
    unittest.main()
