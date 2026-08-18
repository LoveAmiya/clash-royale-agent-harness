from __future__ import annotations

from pathlib import Path
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.web import template_loader
import web_ui_template


ROOT = Path(__file__).resolve().parents[1]


class WebTemplateMigrationTests(unittest.TestCase):
    def test_legacy_web_ui_template_is_packaged_loader_alias(self) -> None:
        self.assertIs(web_ui_template, template_loader)
        self.assertIs(web_ui_template.HTML_PAGE, template_loader.HTML_PAGE)

    def test_packaged_template_file_owns_rendered_html(self) -> None:
        template_path = ROOT / "src" / "clashroyale_agent" / "web" / "templates" / "index.html"

        self.assertTrue(template_path.is_file())
        self.assertEqual(template_loader.HTML_PAGE, template_path.read_text(encoding="utf-8"))
        self.assertIn('id="qualityGateViz"', template_loader.HTML_PAGE)
        self.assertIn("function renderVisualizationDashboard", template_loader.HTML_PAGE)

    def test_legacy_module_no_longer_embeds_the_full_html_literal(self) -> None:
        source = (ROOT / "web_ui_template.py").read_text(encoding="utf-8")

        self.assertIn("clashroyale_agent.web.template_loader", source)
        self.assertNotIn('HTML_PAGE = r"""', source)

    def test_chat_references_have_readable_summary_and_collapsed_technical_details(self) -> None:
        html = template_loader.HTML_PAGE

        self.assertIn("function splitAnswerReferences", html)
        self.assertIn("function renderAnswerReferences", html)
        self.assertIn("数据依据", html)
        self.assertIn("查看技术来源", html)
        self.assertIn("查看数据口径", html)
        self.assertIn("answer.writer.value()", html)


if __name__ == "__main__":
    unittest.main()
