import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import install_test_stubs

install_test_stubs()

from snapshot_store import (
    build_snapshot_rag_documents,
    is_complete_daily_snapshot,
    load_published_snapshot,
    publish_daily_snapshot,
    snapshot_refresh_due,
)


def complete_snapshot(*, fetched_at=None):
    return {
        "snapshot_id": "official-20260725-a",
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "sample_battles": 20000,
        "target_battles": 20000,
        "shortfall_battles": 0,
        "cards_meta": [
            {
                "rank": 1,
                "card_name": "Electro Giant",
                "usage_rate": 8.1,
                "win_rate": 54.2,
                "source": "Supercell API live sample",
            }
        ],
        "top_decks": [
            {
                "rank": 1,
                "deck_name": "Electro Giant / Tornado",
                "cards": ["Electro Giant", "Tornado"],
                "battles": 25,
                "sample_win_rate": 56.0,
                "source": "Supercell API live sample",
            }
        ],
        "deck_matchups": [
            {
                "deck_name": "Electro Giant / Tornado",
                "opponent_deck_name": "Hog Rider / Fireball",
                "games": 4,
                "wins": 3,
                "win_rate": 75.0,
                "source": "Supercell API live sample",
            }
        ],
        "raw_battles": [{"battle_id": "battle-1"}],
        "collection_metrics": {"refresh_budget_exhausted": False, "rate_limited": 0},
    }


class DailySnapshotStoreTests(unittest.TestCase):
    def test_complete_snapshot_is_the_only_publishable_snapshot(self):
        self.assertTrue(is_complete_daily_snapshot(complete_snapshot()))

        partial = complete_snapshot()
        partial["sample_battles"] = 19999
        partial["shortfall_battles"] = 1
        self.assertFalse(is_complete_daily_snapshot(partial))

    def test_publish_writes_the_canonical_snapshot_and_derived_json_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            snapshot = complete_snapshot()

            publish_daily_snapshot(snapshot, data_dir)

            self.assertEqual(load_published_snapshot(data_dir)["snapshot_id"], snapshot["snapshot_id"])
            self.assertEqual(json.loads((data_dir / "cards_meta.json").read_text(encoding="utf-8"))[0]["card_name"], "Electro Giant")
            self.assertEqual(json.loads((data_dir / "top_decks.json").read_text(encoding="utf-8"))[0]["deck_name"], "Electro Giant / Tornado")
            documents = json.loads((data_dir / "rag_documents.json").read_text(encoding="utf-8"))
            self.assertTrue(documents)
            self.assertTrue(all(doc["metadata"]["snapshot_id"] == snapshot["snapshot_id"] for doc in documents))
            self.assertTrue(all(doc["metadata"]["source"] == "Supercell API live sample" for doc in documents))

    def test_loading_an_old_snapshot_backfills_card_filtered_deck_stats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            snapshot = complete_snapshot()
            snapshot["raw_battles"] = [
                {
                    "battle_id": "battle-1",
                    "team_deck": ["Electro Giant", "Tornado"],
                    "opponent_deck": ["Hog Rider", "Fireball"],
                    "won": True,
                }
            ]
            publish_daily_snapshot(snapshot, data_dir)
            canonical_path = data_dir / "official_daily_snapshot.json"
            legacy = json.loads(canonical_path.read_text(encoding="utf-8"))
            legacy.pop("card_deck_stats")
            canonical_path.write_text(json.dumps(legacy), encoding="utf-8")

            loaded = load_published_snapshot(data_dir)

            self.assertEqual(loaded["card_deck_stats"]["Electro Giant"][0]["deck_name"], "Electro Giant / Tornado")
            persisted = json.loads(canonical_path.read_text(encoding="utf-8"))
            self.assertIn("card_deck_stats", persisted)

    def test_publish_rejects_a_partial_snapshot_without_overwriting_the_previous_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            published = complete_snapshot()
            publish_daily_snapshot(published, data_dir)
            partial = complete_snapshot()
            partial["snapshot_id"] = "partial"
            partial["sample_battles"] = 100
            partial["shortfall_battles"] = 19900

            with self.assertRaises(ValueError):
                publish_daily_snapshot(partial, data_dir)

            self.assertEqual(load_published_snapshot(data_dir)["snapshot_id"], published["snapshot_id"])

    def test_snapshot_is_due_only_after_twenty_four_hours(self):
        now = datetime(2026, 7, 25, tzinfo=timezone.utc)
        fresh = complete_snapshot(fetched_at=(now - timedelta(hours=23, minutes=59)).isoformat())
        stale = complete_snapshot(fetched_at=(now - timedelta(hours=24)).isoformat())

        self.assertFalse(snapshot_refresh_due(fresh, now=now))
        self.assertTrue(snapshot_refresh_due(stale, now=now))

    def test_rag_documents_are_derived_from_the_daily_snapshot_not_static_strategy_text(self):
        documents = build_snapshot_rag_documents(complete_snapshot())

        self.assertEqual({doc["source_type"] for doc in documents}, {"snapshot", "card", "deck", "matchup"})
        self.assertNotIn("strategy", {doc["source_type"] for doc in documents})

    def test_rag_documents_include_aggregated_card_archetype_pair_and_counter_evidence(self):
        snapshot = complete_snapshot()
        snapshot["raw_battles"] = [
            {
                "battle_id": f"battle-{index}",
                "team_deck": ["Electro Giant", "Tornado", "Zap"],
                "opponent_deck": ["P.E.K.K.A", "Electro Wizard", "Bandit"],
                "won": index % 2 == 0,
            }
            for index in range(20)
        ]

        documents = build_snapshot_rag_documents(snapshot)
        by_type = {doc["source_type"]: [] for doc in documents}
        for document in documents:
            by_type.setdefault(document["source_type"], []).append(document)

        self.assertTrue(by_type["card_profile"])
        self.assertTrue(by_type["deck_profile"])
        self.assertTrue(by_type["archetype"])
        self.assertTrue(by_type["card_pair"])
        self.assertTrue(by_type["counter"])
        self.assertIn("Electro Giant", by_type["card_profile"][0]["text"])
        self.assertTrue(any("P.E.K.K.A" in document["text"] for document in by_type["counter"]))


if __name__ == "__main__":
    unittest.main()
