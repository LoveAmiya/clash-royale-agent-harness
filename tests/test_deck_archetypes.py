import unittest

from deck_archetypes import ARCHETYPE_CATALOG, classify_deck


class DeckArchetypeClassificationTests(unittest.TestCase):
    def assert_archetype(self, cards, name, family):
        result = classify_deck(tuple(cards))
        self.assertEqual(result.name, name)
        self.assertEqual(result.family, family)
        self.assertGreater(result.confidence, 0.0)
        self.assertTrue(result.reason)

    def test_catalog_stays_near_twenty_reviewable_categories(self):
        names = {item.name for item in ARCHETYPE_CATALOG}
        self.assertGreaterEqual(len(names), 18)
        self.assertLessEqual(len(names), 24)
        self.assertIn("其他卡组", names)
        self.assertNotIn("Unclassified deck family", names)

    def test_hog_cycle_and_hog_earthquake_are_distinct(self):
        self.assert_archetype(
            ["Hog Rider", "Musketeer", "Cannon", "Fireball", "The Log", "Skeletons", "Ice Spirit", "Ice Golem"],
            "野猪速转",
            "小费轮转",
        )
        self.assert_archetype(
            ["Hog Rider", "Earthquake", "The Log", "Skeletons", "Ice Spirit", "Knight", "Cannon", "Firecracker"],
            "野猪地震",
            "小费轮转",
        )

    def test_feature_rules_cover_bridge_and_heavy_push_variants(self):
        self.assert_archetype(
            ["P.E.K.K.A", "Battle Ram", "Bandit", "Royal Ghost", "Magic Archer", "Electro Wizard", "Zap", "Poison"],
            "皮卡桥头冲锋",
            "桥头冲锋",
        )
        self.assert_archetype(
            ["Golem", "Night Witch", "Baby Dragon", "Tornado", "Lightning", "Lumberjack", "Barbarian Barrel", "Mega Minion"],
            "戈仑推进",
            "重型推进",
        )
        self.assert_archetype(
            ["Miner", "Wall Breakers", "Bomb Tower", "Magic Archer", "Tornado", "The Log", "Bats", "Knight"],
            "矿工消耗",
            "消耗控制",
        )
        self.assert_archetype(
            ["Elixir Golem", "Night Witch", "Rage", "Skeleton King", "Skeleton Army", "Void", "Arrows", "Wizard"],
            "圣水戈仑推进",
            "重型推进",
        )

    def test_feature_topology_covers_bait_without_goblin_barrel_and_three_musketeers_bridge(self):
        self.assert_archetype(
            ["Dart Goblin", "Goblins", "Rascals", "Rocket", "Royal Ghost", "Skeleton Barrel", "Suspicious Bush", "The Log"],
            "诱导消耗",
            "消耗控制",
        )
        self.assert_archetype(
            ["Bandit", "Barbarian Barrel", "Battle Ram", "Elixir Collector", "Hunter", "Ice Golem", "Royal Ghost", "Three Musketeers"],
            "三枪分路",
            "分路压制",
        )

    def test_synergy_precedence_avoids_double_core_misclassification(self):
        self.assert_archetype(
            ["Lava Hound", "Balloon", "Tombstone", "Skeleton Dragons", "Mega Minion", "Fireball", "Arrows", "Barbarians"],
            "天狗空军推进",
            "空中压制",
        )
        self.assert_archetype(
            ["Royal Recruits", "Royal Hogs", "Flying Machine", "Zappies", "Goblin Cage", "Fireball", "Arrows", "Barbarian Barrel"],
            "皇家卫队分路",
            "分路压制",
        )

    def test_siege_variants_use_supporting_features_not_exact_templates(self):
        self.assert_archetype(
            ["X-Bow", "Tesla", "Archers", "Knight", "Skeletons", "Electro Spirit", "Fireball", "The Log"],
            "X连弩自闭",
            "建筑自闭",
        )
        self.assert_archetype(
            ["Mortar", "Skeleton Barrel", "Cannon Cart", "Rascals", "Goblins", "Minions", "Fireball", "Barbarian Barrel"],
            "迫击炮消耗",
            "建筑自闭",
        )

    def test_conflicting_inventor_deck_falls_back_to_other(self):
        result = classify_deck(
            ("Golem", "Hog Rider", "X-Bow", "Goblin Barrel", "Balloon", "Royal Giant", "P.E.K.K.A", "Three Musketeers")
        )
        self.assertEqual(result.name, "其他卡组")
        self.assertEqual(result.family, "其他")
        self.assertIn("冲突", result.reason)


if __name__ == "__main__":
    unittest.main()
