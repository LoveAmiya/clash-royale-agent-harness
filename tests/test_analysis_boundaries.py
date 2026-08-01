import types
import unittest
from unittest.mock import AsyncMock, patch

from analysis_boundaries import detect_unsupported_analysis_request
from answer_builder import build_card_ranking_answer
from query_answering import AnswerResult


class AnalysisBoundaryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def build_app():
        return types.SimpleNamespace(
            state=types.SimpleNamespace(
                cards_meta_data=[],
                bootstrap_cards_meta_data=[],
                schedule_data=[],
                top_decks_data=[],
                card_deck_stats_data={},
                retriever=None,
                live_snapshot=None,
                live_snapshot_at=0.0,
                live_error=None,
                rag_status="not_required",
                rag_snapshot_id=None,
                rag_docs_fingerprint=None,
            )
        )

    async def test_future_forecast_is_rejected_before_model_parsing(self):
        import runtime_multi

        wrong_result = AnswerResult(
            answer="Barbarian Barrel 30.6%",
            trace_id="wrong",
            parsed={"intent": "card_rank_lookup_query"},
            plan=None,
            selected_skill="CardRankLookupSkill",
            mode="direct",
            metadata={},
        )
        query = "\u9884\u6d4b\u4e0b\u5468\u4f7f\u7528\u7387\u6700\u9ad8\u7684\u5361\u724c\uff0c\u5e76\u7ed9\u51fa\u7cbe\u786e\u6982\u7387\u3002"

        with patch.dict(runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}), patch.object(
            runtime_multi,
            "parse_user_query",
            AsyncMock(return_value={"intent": "card_rank_lookup_query", "parse_source": "llm_parser"}),
        ) as parser, patch.object(
            runtime_multi,
            "answer_query",
            AsyncMock(return_value=wrong_result),
        ) as answer_query:
            result = await runtime_multi.build_answer(query, self.build_app())

        parser.assert_not_awaited()
        answer_query.assert_not_awaited()
        self.assertEqual(result.mode, "boundary_reject")
        self.assertEqual(result.parsed["boundary_code"], "future_forecast")
        self.assertIn("\u65e0\u6cd5", result.answer)
        self.assertIn("\u7cbe\u786e\u6982\u7387", result.answer)
        self.assertNotIn("Barbarian Barrel", result.answer)

    async def test_similar_unsupported_analysis_requests_are_rejected(self):
        import runtime_multi

        cases = (
            ("\u4e0b\u4e2a\u7248\u672c\u6bd2\u836f\u7684\u80dc\u7387\u4f1a\u662f\u591a\u5c11\uff1f", "future_forecast"),
            ("\u706b\u7403\u80fd\u8ba9\u8fd9\u5957\u5361\u7ec4\u80dc\u7387\u63d0\u9ad8\u591a\u5c11\uff1f", "causal_effect"),
            ("\u8fc7\u53bb\u56db\u5468\u91ce\u732a\u9a91\u58eb\u4f7f\u7528\u7387\u8d8b\u52bf\u5982\u4f55\uff1f", "historical_trend"),
        )

        for query, expected_code in cases:
            with self.subTest(query=query), patch.dict(
                runtime_multi.os.environ, {"OPENAI_API_KEY": "test-key"}
            ), patch.object(runtime_multi, "parse_user_query", AsyncMock()) as parser, patch.object(
                runtime_multi, "answer_query", AsyncMock()
            ) as answer_query:
                result = await runtime_multi.build_answer(query, self.build_app())

            parser.assert_not_awaited()
            answer_query.assert_not_awaited()
            self.assertEqual(result.mode, "boundary_reject")
            self.assertEqual(result.parsed["boundary_code"], expected_code)

    def test_card_rank_is_labeled_as_sample_rank_not_global_rank(self):
        answer = build_card_ranking_answer(
            {"intent": "card_rank_lookup_query", "metric": "usage_rate", "rank": 1},
            [
                {
                    "card_name": "Barbarian Barrel",
                    "rank": 1,
                    "rating": 0,
                    "usage_rate": 30.6,
                    "win_rate": 61.8,
                    "clean_win_rate": 61.8,
                    "source": "Supercell API live sample",
                    "sample_battles": 200000,
                }
            ],
        )

        self.assertIn("\u6837\u672c\u5185\u6392\u540d", answer)
        self.assertNotIn("\u5168\u5c40\u6392\u540d", answer)

    def test_supported_observational_queries_are_not_blocked(self):
        queries = (
            "\u5f53\u524d\u4f7f\u7528\u7387\u6700\u9ad8\u7684\u5361\u724c\u662f\u4ec0\u4e48\uff1f",
            "\u6bd4\u8f83\u706b\u7403\u548c\u6bd2\u836f\u7684\u6837\u672c\u5185\u80dc\u7387",
            "\u8fd9\u4e24\u5957\u5361\u7ec4\u7684\u89c2\u6d4b\u5bf9\u5c40\u80dc\u7387\u662f\u591a\u5c11\uff1f",
            "\u91c7\u96c6\u5b8c\u6210\u4e4b\u540e\u600e\u6837\u6838\u67e5\u5feb\u7167\uff1f",
        )

        for query in queries:
            with self.subTest(query=query):
                self.assertIsNone(detect_unsupported_analysis_request(query))


if __name__ == "__main__":
    unittest.main()
