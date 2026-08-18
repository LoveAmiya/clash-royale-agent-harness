import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.

import query_parser
from clashroyale_agent.qa.metrics import extract_metrics, get_metric, normalize_metrics


class QAMetricParsingTests(unittest.TestCase):
    def test_get_metric_preserves_primary_metric_priority(self):
        self.assertEqual(get_metric("火球的净胜率是多少"), "clean_win_rate")
        self.assertEqual(get_metric("fireball clean win rate"), "clean_win_rate")
        self.assertEqual(get_metric("火球的胜率是多少"), "win_rate")
        self.assertEqual(get_metric("fireball win rate"), "win_rate")
        self.assertEqual(get_metric("火球怎么样"), "usage_rate")

    def test_extract_metrics_preserves_stable_display_order(self):
        self.assertEqual(
            extract_metrics("火球的使用率、胜率和净胜率"),
            ["usage_rate", "win_rate", "clean_win_rate"],
        )
        self.assertEqual(
            extract_metrics("usage rate and clean win rate"),
            ["usage_rate", "win_rate", "clean_win_rate"],
        )

    def test_normalize_metrics_filters_invalid_values_and_deduplicates(self):
        self.assertEqual(
            normalize_metrics(
                ["win_rate", "damage", "usage_rate", "win_rate"],
                "火球怎么样",
                "card_query",
            ),
            ["win_rate", "usage_rate"],
        )
        self.assertIsNone(normalize_metrics(["win_rate"], "热门卡组", "deck_query"))

    def test_query_parser_keeps_legacy_metric_entry_points(self):
        self.assertIs(query_parser.get_metric, get_metric)
        self.assertIs(query_parser.extract_metrics, extract_metrics)
        self.assertIs(query_parser.normalize_metrics, normalize_metrics)


if __name__ == "__main__":
    unittest.main()
