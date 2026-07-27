import json
import unittest
from pathlib import Path

from support import install_test_stubs

install_test_stubs()

from query_parser import (
    build_card_aliases,
    fallback_parse_query,
    normalize_card_alias,
    resolve_card_name,
)


CARDS = json.loads((Path("data") / "cards_meta.json").read_text(encoding="utf-8"))


class CardAliasCoverageTests(unittest.TestCase):
    def test_every_repository_card_resolves_from_its_standard_english_name(self):
        for card in CARDS:
            canonical = card["card_name"]
            with self.subTest(card=canonical):
                self.assertEqual(resolve_card_name(canonical, CARDS), canonical)

    def test_every_repository_card_resolves_from_a_chinese_or_derived_chinese_alias(self):
        aliases = build_card_aliases(CARDS)
        for card in CARDS:
            canonical = card["card_name"]
            chinese_aliases = [
                alias for alias in aliases[canonical] if any("\u4e00" <= char <= "\u9fff" for char in alias)
            ]
            with self.subTest(card=canonical):
                self.assertTrue(chinese_aliases)
                self.assertEqual(resolve_card_name(chinese_aliases[0], CARDS), canonical)

    def test_every_repository_card_has_three_noncanonical_aliases(self):
        aliases = build_card_aliases(CARDS)
        for card in CARDS:
            canonical = card["card_name"]
            noncanonical = [
                alias for alias in aliases[canonical]
                if alias != normalize_card_alias(canonical)
            ]
            with self.subTest(card=canonical):
                self.assertGreaterEqual(len(noncanonical), 3)

    def test_no_normalized_alias_is_shared_by_different_cards(self):
        owners = {}
        for card_name, aliases in build_card_aliases(CARDS).items():
            for alias in aliases:
                with self.subTest(alias=alias, card=card_name):
                    self.assertNotIn(alias, owners)
                    owners[alias] = card_name

    def test_chinese_community_aliases_resolve_to_canonical_cards(self):
        cases = {
            "\u91ce\u732a\u80dc\u7387": "Hog Rider",
            "\u5c0f\u76ae\u5361\u4f7f\u7528\u7387": "Mini P.E.K.K.A",
            "\u7535\u6cd5\u80dc\u7387": "Electro Wizard",
            "\u8d85\u9a91\u80dc\u7387": "Mega Knight",
            "\u5c0f\u7535\u80dc\u7387": "Zap",
            "\u5c0f\u7535\u7cbe\u7075\u80dc\u7387": "Electro Spirit",
            "\u98de\u6876\u80dc\u7387": "Goblin Barrel",
            "\u9ed1\u738b\u80dc\u7387": "Dark Prince",
            "\u516c\u4e3b\u5854\u4f7f\u7528\u7387": "Tower Princess",
            "\u5973\u67aa\u80dc\u7387": "Musketeer",
            "\u8001\u9ad8\u4f7f\u7528\u7387": "Magic Archer",
            "\u7164\u6c14\u9f99\u80dc\u7387": "Inferno Dragon",
            "\u86ee\u9524\u4f7f\u7528\u7387": "Battle Ram",
            "\u9ab7\u9ac5\u6d77\u80dc\u7387": "Skeleton Army",
            "\u5c0f\u95ea\u4f7f\u7528\u7387": "Zap",
            "\u98de\u673a\u80dc\u7387": "Flying Machine",
            "\u706b\u7bad\u4f7f\u7528\u7387": "Rocket",
            "\u5c0f\u70ae\u80dc\u7387": "Cannon",
            "\u5927\u96ea\u7403\u4f7f\u7528\u7387": "Giant Snowball",
            "\u5c0f\u738b\u4f7f\u7528\u7387": "Little Prince",
            "\u89c9\u9192\u5f13\u7bad\u624b\u80dc\u7387": "Archers Evolution",
            "\u8fdb\u5316\u8d85\u9a91\u80dc\u7387": "Mega Knight Evolution",
            "\u8d85\u9a91\u89c9\u9192\u80dc\u7387": "Mega Knight Evolution",
            "evo-mk usage": "Mega Knight Evolution",
            "evo bomber usage": "Bomber Evolution",
            "hero knight usage": "Hero Knight",
            "\u82f1\u96c4\u706b\u67aa\u624b\u80dc\u7387": "Hero Musketeer",
            "\u5c0f\u76ae\u5361\u82f1\u96c4\u80dc\u7387": "Hero Mini P.E.K.K.A",
            "Mini-PEKKA win rate": "Mini P.E.K.K.A",
            "X Bow usage": "X-Bow",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(resolve_card_name(question, CARDS), expected)

    def test_alias_question_keeps_named_card_query_routing(self):
        parsed = fallback_parse_query("\u5c0f\u76ae\u5361\u7684\u80dc\u7387\u662f\u591a\u5c11", CARDS)

        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["card_name"], "Mini P.E.K.K.A")

    def test_common_skeletons_shorthand_keeps_metrics_on_structured_card_route(self):
        parsed = fallback_parse_query(
            "\u9ab7\u9ac5\u7684\u4f7f\u7528\u7387\u80dc\u7387\u5462\uff1f",
            CARDS,
        )

        self.assertEqual(parsed["intent"], "card_query")
        self.assertEqual(parsed["card_name"], "Skeletons")
        self.assertEqual(parsed["metrics"], ["usage_rate", "win_rate"])


if __name__ == "__main__":
    unittest.main()
