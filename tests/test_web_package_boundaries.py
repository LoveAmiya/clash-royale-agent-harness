from __future__ import annotations

import unittest
from pathlib import Path

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.web import runtime, schemas
import web_app


ROOT = Path(__file__).resolve().parents[1]


class WebPackageBoundaryTests(unittest.TestCase):
    def test_root_web_module_does_not_keep_legacy_template_or_model_copies(self) -> None:
        source = (ROOT / "web_app.py").read_text(encoding="utf-8")

        self.assertNotIn('HTML_PAGE = """', source)
        self.assertNotIn("class ChatRequest(BaseModel):", source)
        self.assertIn("from clashroyale_agent.web.schemas import", source)

    def test_web_request_models_are_owned_by_the_package(self) -> None:
        self.assertIs(web_app.ChatRequest, schemas.ChatRequest)
        self.assertIs(web_app.LiveSampleSettingsRequest, schemas.LiveSampleSettingsRequest)
        self.assertIs(web_app.FeedbackProxyRequest, schemas.FeedbackProxyRequest)
        self.assertIs(web_app.CardCompareProxyRequest, schemas.CardCompareProxyRequest)
        self.assertIs(web_app.EntityCompareProxyRequest, schemas.EntityCompareProxyRequest)
        self.assertIs(web_app.DeckProfileProxyRequest, schemas.DeckProfileProxyRequest)
        self.assertIs(web_app.DeckMatchupProxyRequest, schemas.DeckMatchupProxyRequest)

    def test_chat_request_defaults_remain_compatible(self) -> None:
        request = schemas.ChatRequest(message="test")

        self.assertEqual(request.dataset_scope, web_app.DEFAULT_DATASET_SCOPE)
        self.assertEqual(request.deck_mode, "base8")
        self.assertEqual(request.entity_mode, "base8")

    def test_root_app_uses_packaged_runtime_factory(self) -> None:
        self.assertEqual(web_app.app.title, "CR Agent Web UI")
        self.assertIn("create_web_app", (ROOT / "web_app.py").read_text(encoding="utf-8"))
        self.assertEqual(runtime.create_web_app().title, web_app.app.title)
