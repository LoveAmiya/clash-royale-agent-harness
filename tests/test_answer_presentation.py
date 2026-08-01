import json
import unittest
from pathlib import Path

from answer_presentation import localize_card_names, normalize_answer_text
from query_parser import CARD_ALIAS_OVERRIDES, resolve_card_name


ROOT = Path(__file__).resolve().parents[1]
ALIASES_PATH = ROOT / "data" / "card_aliases.zh-CN.json"


class CardNamePresentationTests(unittest.TestCase):
    def test_requested_names_are_the_primary_chinese_display_names(self):
        expected = {
            "Bowler": "巨石投手",
            "Giant Snowball": "大雪球",
            "The Log": "复仇滚木",
            "Tombstone": "骷髅墓碑",
            "Valkyrie": "瓦基丽武神",
            "X-Bow": "X连弩",
        }

        for canonical, display_name in expected.items():
            with self.subTest(card=canonical):
                self.assertEqual(CARD_ALIAS_OVERRIDES[canonical][0], display_name)

    def test_legacy_names_remain_valid_free_question_aliases(self):
        aliases = {
            "保龄球手": "Bowler",
            "巨型雪球": "Giant Snowball",
            "滚木": "The Log",
            "墓碑": "Tombstone",
            "女武神": "Valkyrie",
            "连弩": "X-Bow",
        }
        cards_meta = [{"card_name": canonical} for canonical in aliases.values()]

        for alias, canonical in aliases.items():
            with self.subTest(alias=alias):
                self.assertEqual(resolve_card_name(f"{alias}胜率", cards_meta), canonical)

    def test_alias_table_is_available_as_an_editable_data_file(self):
        payload = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertGreaterEqual(len(payload["cards"]), 122)
        self.assertEqual(payload["cards"]["Bowler"]["display_name"], "巨石投手")
        self.assertIn("保龄球手", payload["cards"]["Bowler"]["aliases"])

    def test_english_card_names_are_localized_with_longest_name_first(self):
        answer = "Bowler, Giant Snowball, The Log, Tombstone, Valkyrie and X-Bow."

        localized = localize_card_names(answer)

        self.assertEqual(
            localized,
            "巨石投手, 大雪球, 复仇滚木, 骷髅墓碑, 瓦基丽武神 and X连弩.",
        )

    def test_markdown_artifacts_and_english_card_names_are_removed(self):
        answer = (
            "## conclusion\n"
            "当前样本内，**Bowler** 与 **The Log** 表现突出。\n\n"
            "## data evidence\n"
            "- **X-Bow** 样本充足。\n\n"
            "## data boundaries\n"
            "不能预测未来。"
        )

        normalized = normalize_answer_text(answer)

        self.assertIn("结论", normalized)
        self.assertIn("数据依据", normalized)
        self.assertIn("数据边界", normalized)
        self.assertIn("巨石投手", normalized)
        self.assertIn("复仇滚木", normalized)
        self.assertIn("X连弩", normalized)
        self.assertNotIn("**", normalized)
        self.assertNotIn("##", normalized)
        self.assertNotIn("conclusion", normalized.lower())
        self.assertNotIn("data evidence", normalized.lower())
        self.assertNotIn("data boundaries", normalized.lower())

    def test_legacy_chinese_names_in_model_output_are_normalized(self):
        answer = "攻城槌、巨型骷髅、掘地矿工、雪球、滚木、连弩"

        normalized = normalize_answer_text(answer)

        self.assertEqual(
            normalized,
            "野蛮人攻城锤、骷髅巨人、矿工、大雪球、复仇滚木、X连弩",
        )


if __name__ == "__main__":
    unittest.main()
