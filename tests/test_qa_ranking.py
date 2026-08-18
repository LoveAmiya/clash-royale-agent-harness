import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.

import query_parser
from clashroyale_agent.qa.ranking import (
    CHINESE_NUM_MAP,
    coerce_rank_value,
    coerce_top_n_value,
    extract_cn_number,
    extract_rank_target,
    extract_top_n,
)


class QARankingParserTests(unittest.TestCase):
    def test_extract_cn_number_preserves_chinese_numeric_contract(self):
        self.assertEqual(extract_cn_number("两"), 2)
        self.assertEqual(extract_cn_number("三十"), 30)
        self.assertIsNone(extract_cn_number("三十一"))
        self.assertEqual(query_parser.CHINESE_NUM_MAP, CHINESE_NUM_MAP)

    def test_rank_targets_are_clamped_and_ignore_rounds(self):
        self.assertEqual(extract_rank_target("使用率第三名的卡牌"), 3)
        self.assertEqual(extract_rank_target("排名 100 的卡牌", max_n=30), 30)
        self.assertIsNone(extract_rank_target("我们第五轮打谁"))
        self.assertEqual(coerce_rank_value("第十二名"), 12)
        self.assertEqual(coerce_rank_value(0), 1)

    def test_top_n_values_preserve_defaults_and_clamps(self):
        self.assertEqual(extract_top_n("热门卡组前20个"), 20)
        self.assertEqual(extract_top_n("top 99 decks", max_n=30), 30)
        self.assertEqual(extract_top_n("给我看几个热门卡组"), 5)
        self.assertEqual(coerce_top_n_value("前30"), 30)
        self.assertIsNone(coerce_top_n_value("很多"))

    def test_query_parser_keeps_legacy_ranking_entry_points(self):
        self.assertIs(query_parser.extract_cn_number, extract_cn_number)
        self.assertIs(query_parser.coerce_rank_value, coerce_rank_value)
        self.assertIs(query_parser.coerce_top_n_value, coerce_top_n_value)
        self.assertIs(query_parser.extract_rank_target, extract_rank_target)
        self.assertIs(query_parser.extract_top_n, extract_top_n)


if __name__ == "__main__":
    unittest.main()
