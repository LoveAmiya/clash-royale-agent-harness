import unittest
import re
import shutil
import subprocess

from support import install_test_stubs

install_test_stubs()

import web_app


class StructuredFrontendContractTests(unittest.TestCase):
    def test_frontend_has_all_structured_views_and_keeps_free_form_qa(self):
        html = web_app.HTML_PAGE

        for view in ("home", "qa", "rankings", "card", "compare", "deck", "matchup", "meta"):
            self.assertIn(f'data-view="{view}"', html)
            self.assertIn(f'id="view-{view}"', html)
        self.assertIn('id="chatBox"', html)
        self.assertIn('id="inputBox"', html)
        self.assertIn('fetch("/chat"', html)
        self.assertIn('id="executionPanel"', html)

    def test_card_picker_uses_catalog_ids_and_exact_eight_card_validation(self):
        html = web_app.HTML_PAGE

        self.assertIn('requestJSON("/api/cards/catalog")', html)
        self.assertIn('class="card-picker"', html)
        self.assertIn('data-picker="deck-profile"', html)
        self.assertIn('data-picker="matchup-a"', html)
        self.assertIn('data-picker="matchup-b"', html)
        self.assertIn('selection.length !== 8', html)
        self.assertIn('恰好选择 8 张不同卡牌', html)

    def test_web_app_exposes_structured_api_proxies(self):
        paths = {route.path for route in web_app.app.routes}

        for path in (
            "/api/datasets",
            "/api/cards/catalog",
            "/api/cards/rankings",
            "/api/cards/{card_id}/stats",
            "/api/entities/catalog",
            "/api/entities/rankings",
            "/api/entities/{entity_id}/stats",
            "/api/entities/compare",
            "/api/loadouts/catalog",
            "/api/cards/compare",
            "/api/decks/profile",
            "/api/decks/matchup",
            "/api/meta/archetypes",
        ):
            self.assertIn(path, paths)

    def test_frontend_uses_explicit_window_and_rank_scope_controls(self):
        html = web_app.HTML_PAGE

        self.assertIn('data-window="7"', html)
        self.assertIn('data-window="35"', html)
        for window in ("d7_14", "d14_21", "d21_28", "d28_35"):
            self.assertIn(f'data-window="{window}"', html)
        for level in ("top_100", "top_200", "top_500", "top_1000", "all"):
            self.assertIn(f'data-level="{level}"', html)
        self.assertIn('button.disabled = dataset ? dataset.ready === false', html)
        self.assertIn('dataset_scope: state.datasetScope', html)
        self.assertIn('requestJSON("/api/datasets")', html)

    def test_card_rankings_page_has_metric_controls_search_and_complete_table(self):
        html = web_app.HTML_PAGE

        self.assertIn('data-view="rankings"', html)
        self.assertIn('id="view-rankings"', html)
        self.assertIn('data-ranking-metric="usage_rate"', html)
        self.assertIn('data-ranking-metric="clean_win_rate"', html)
        self.assertIn('data-ranking-metric="rating"', html)
        self.assertIn('id="rankingSearch"', html)
        self.assertIn('/api/cards/rankings', html)
        self.assertIn('/api/entities/rankings', html)
        for heading in ("排名", "卡牌", "使用率", "胜率", "评分", "出场"):
            self.assertIn(heading, html)

    def test_structured_views_call_deterministic_endpoints_and_show_provenance(self):
        html = web_app.HTML_PAGE

        for endpoint in (
            "/api/cards/compare",
            "/api/decks/profile",
            "/api/decks/matchup",
            "/api/meta/archetypes",
        ):
            self.assertIn(endpoint, html)
        self.assertIn('matched_sample_count', html)
        self.assertIn('LOW_SAMPLE_WARNING', html)
        self.assertIn('renderProvenance', html)
        self.assertIn('匹配样本：0 场', html)
        self.assertIn('这两套完整八卡卡组之间没有找到任何对局', html)

    def test_frontend_exposes_base8_and_full_loadout_mode_controls(self):
        html = web_app.HTML_PAGE

        self.assertIn('data-mode="base8"', html)
        self.assertIn('data-mode="loadout_entity"', html)
        self.assertIn('id="dataModeSegments"', html)
        self.assertIn('id="deckLoadoutDetails"', html)
        self.assertIn('id="matchupALoadoutDetails"', html)
        self.assertIn('id="matchupBLoadoutDetails"', html)
        self.assertIn('requestJSON("/api/loadouts/catalog")', html)
        self.assertIn('requestJSON("/api/entities/catalog")', html)
        self.assertIn('tower_id', html)
        self.assertIn('evolution_level', html)
        self.assertIn('deck_mode: state.deckMode', html)
        self.assertIn('entity_mode: state.entityMode', html)

    def test_free_answer_and_environment_analysis_share_selected_deck_mode(self):
        html = web_app.HTML_PAGE

        self.assertIn('deck_mode: state.deckMode', html)
        self.assertIn('entity_mode: state.entityMode', html)
        self.assertIn('function applyDataMode()', html)
        self.assertIn('function submitMetaAnalysis()', html)

    def test_frontend_uses_entity_endpoints_for_full_configuration_card_surfaces(self):
        html = web_app.HTML_PAGE

        for endpoint in (
            "/api/entities/catalog",
            "/api/entities/rankings",
            "/api/entities/compare",
        ):
            self.assertIn(endpoint, html)
        self.assertIn('function selectEntityMode(', html)
        self.assertIn('function submitEntityCard(', html)
        self.assertIn('function submitEntityCompare(', html)
        self.assertIn('complete_loadout_ready', html)
        self.assertIn('entity_stats_ready', html)

    def test_home_dashboard_uses_dataset_catalog_not_legacy_snapshot_counts(self):
        html = web_app.HTML_PAGE

        self.assertIn('renderDatasetOverview', html)
        self.assertIn('state.datasets.get(state.datasetScope)', html)
        self.assertIn('full_loadout_side_records', html)

    def test_web_proxy_models_preserve_explicit_full_loadout_mode(self):
        request = web_app.ChatRequest(message="分析这套完整配置", deck_mode="full_loadout")
        profile = web_app.DeckProfileProxyRequest(
            deck_mode="full_loadout",
            loadout={"tower_id": "tower", "cards": []},
        )

        self.assertEqual(request.deck_mode, "full_loadout")
        self.assertEqual(profile.deck_mode, "full_loadout")
        self.assertEqual(profile.loadout["tower_id"], "tower")

    def test_meta_page_keeps_structured_table_and_offers_user_triggered_rag_synthesis(self):
        html = web_app.HTML_PAGE

        self.assertIn('id="metaResult"', html)
        self.assertIn('id="metaAnalyze"', html)
        self.assertIn('id="metaAnalysisResult"', html)
        self.assertIn('function submitMetaAnalysis()', html)
        self.assertIn('当前环境以哪些卡组体系为主', html)
        self.assertIn('intent_hint: intentHint', html)
        self.assertIn('streamAnswer(message, bubble, statusTarget, false, "meta_analysis_query")', html)
        self.assertIn('metaAnalyze.addEventListener("click", submitMetaAnalysis)', html)

    def test_free_answer_stream_has_plain_text_markdown_defense(self):
        html = web_app.HTML_PAGE

        self.assertIn("function normalizeVisibleAnswerText(text)", html)
        self.assertIn("bubble.dataset.rawAnswer", html)
        self.assertIn('"conclusion": "结论"', html)
        self.assertIn('.replaceAll("*", "")', html)

    def test_frontend_uses_clean_chinese_and_responsive_layout(self):
        html = web_app.HTML_PAGE

        self.assertIn("皇室战争数据分析", html)
        self.assertIn("自由问答", html)
        self.assertIn("卡组对阵", html)
        self.assertIn("@media (max-width: 760px)", html)
        for broken in ("鐨", "鏁版", "鍗＄", "璇锋"):
            self.assertNotIn(broken, html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JavaScript syntax validation")
    def test_embedded_javascript_is_syntactically_valid(self):
        script = re.search(r"<script>([\s\S]*?)</script>", web_app.HTML_PAGE).group(1)

        result = subprocess.run(
            ["node", "--check"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
