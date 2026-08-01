import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.export_archetype_naming_package import export_archetype_naming_package


class ArchetypeNamingExportTests(unittest.TestCase):
    def test_export_contains_review_fields_chinese_cards_and_classifier_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "stats.sqlite"
            output_path = root / "archetypes.json"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE archetype_stats(
                    archetype TEXT PRIMARY KEY, games INTEGER, wins INTEGER, losses INTEGER,
                    draws INTEGER, usage_rate REAL, clean_win_rate REAL, net_win_rate REAL,
                    classification TEXT, confidence_note TEXT
                );
                CREATE TABLE archetype_decks(
                    archetype TEXT, deck_signature TEXT, games INTEGER, wins INTEGER,
                    losses INTEGER, draws INTEGER, PRIMARY KEY(archetype, deck_signature)
                );
                CREATE TABLE deck_stats(
                    deck_signature TEXT PRIMARY KEY, deck_json TEXT, archetype TEXT,
                    games INTEGER, wins INTEGER, losses INTEGER, draws INTEGER, crowns INTEGER,
                    usage_rate REAL, clean_win_rate REAL, net_win_rate REAL
                );
                """
            )
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                [("snapshot_id", json.dumps("snapshot-test")), ("side_records", "20")],
            )
            connection.execute(
                "INSERT INTO archetype_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("X连弩自闭", 10, 6, 4, 0, 50.0, 60.0, 10.0, "feature-weighted-v2/建筑自闭", "test"),
            )
            deck = ["Archers", "Electro Spirit", "Fireball", "Knight", "Skeletons", "Tesla", "The Log", "X-Bow"]
            signature = json.dumps(sorted(deck), ensure_ascii=False, separators=(",", ":"))
            connection.execute(
                "INSERT INTO deck_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (signature, json.dumps(sorted(deck)), "X连弩自闭", 10, 6, 4, 0, 20, 50.0, 60.0, 10.0),
            )
            connection.execute(
                "INSERT INTO archetype_decks VALUES (?, ?, ?, ?, ?, ?)",
                ("X连弩自闭", signature, 10, 6, 4, 0),
            )
            connection.commit()
            connection.close()

            payload = export_archetype_naming_package(database_path, output_path)

            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["snapshot_id"], "snapshot-test")
            self.assertEqual(payload["archetype_count"], 1)
            archetype = payload["archetypes"][0]
            self.assertEqual(archetype["current_name"], "X连弩自闭")
            self.assertEqual(archetype["family"], "建筑自闭")
            self.assertEqual(archetype["reviewed_name"], "")
            self.assertEqual(archetype["review_notes"], "")
            self.assertEqual(archetype["classification_rule"]["anchor_cards_zh"], ["X连弩"])
            representative = archetype["representative_decks"][0]
            self.assertIn("X连弩", representative["cards_zh"])
            self.assertIn("复仇滚木", representative["cards_zh"])
            self.assertGreater(representative["classification_confidence"], 0.0)
            self.assertTrue(representative["matched_signals"])
            self.assertEqual(archetype["core_cards"][0]["deck_games"], 10)
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
