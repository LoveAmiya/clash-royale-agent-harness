import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from support import install_test_stubs

install_test_stubs()

from snapshot_store import (
    DAILY_REFRESH_INTERVAL,
    DAILY_TARGET_BATTLES,
    SNAPSHOT_RETENTION_DAYS,
    SNAPSHOT_RETENTION_MAX_COMPLETE,
    build_snapshot_rag_documents,
    cleanup_snapshot_retention,
    compute_rag_docs_fingerprint,
    is_complete_daily_snapshot,
    is_path_of_legend_snapshot,
    load_published_snapshot,
    load_published_snapshot_summary,
    publish_daily_snapshot,
    snapshot_refresh_due,
    validate_snapshot_rag_documents,
)
from supercell_live import (
    JsonlRecordSequence,
    PATH_OF_LEGEND_COLLECTION_SCOPE,
    PATH_OF_LEGEND_SCOPE_CONTRACT,
)


def complete_snapshot(*, fetched_at=None):
    return {
        "snapshot_id": "official-20260725-a",
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "sample_battles": DAILY_TARGET_BATTLES,
        "target_battles": DAILY_TARGET_BATTLES,
        "shortfall_battles": 0,
        "cards_meta": [
            {
                "rank": 1,
                "card_name": "Electro Giant",
                "usage_rate": 8.1,
                "win_rate": 54.2,
                "clean_win_rate": 54.2,
                "appearance_count": 1620,
                "source": "Supercell API live sample",
            }
        ],
        "top_decks": [
            {
                "rank": 1,
                "deck_name": "Baby Dragon / Barbarian Barrel / Bowler / Electro Giant / Goblin Cage / Ice Wizard / Lightning / Tornado",
                "cards": [
                    "Baby Dragon",
                    "Barbarian Barrel",
                    "Bowler",
                    "Electro Giant",
                    "Goblin Cage",
                    "Ice Wizard",
                    "Lightning",
                    "Tornado",
                ],
                "battles": 25,
                "sample_win_rate": 56.0,
                "source": "Supercell API live sample",
            }
        ],
        "deck_matchups": [
            {
                "deck_name": "Baby Dragon / Barbarian Barrel / Bowler / Electro Giant / Goblin Cage / Ice Wizard / Lightning / Tornado",
                "opponent_deck_name": "Cannon / Fireball / Hog Rider / Ice Golem / Ice Spirit / Musketeer / Skeletons / The Log",
                "games": 5,
                "wins": 3,
                "win_rate": 60.0,
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

    def test_path_of_legend_scope_requires_the_versioned_collection_contract(self):
        snapshot = complete_snapshot()
        self.assertFalse(is_path_of_legend_snapshot(snapshot))
        snapshot["collection_scope"] = PATH_OF_LEGEND_COLLECTION_SCOPE
        snapshot["scope_contract"] = PATH_OF_LEGEND_SCOPE_CONTRACT
        self.assertTrue(is_path_of_legend_snapshot(snapshot))

    def test_publish_writes_the_canonical_snapshot_and_derived_json_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            snapshot = complete_snapshot()

            published = publish_daily_snapshot(snapshot, data_dir)

            self.assertEqual(load_published_snapshot(data_dir)["snapshot_id"], snapshot["snapshot_id"])
            self.assertEqual(json.loads((data_dir / "cards_meta.json").read_text(encoding="utf-8"))[0]["card_name"], "Electro Giant")
            self.assertEqual(
                json.loads((data_dir / "top_decks.json").read_text(encoding="utf-8"))[0]["deck_name"],
                snapshot["top_decks"][0]["deck_name"],
            )
            documents = json.loads((data_dir / "rag_documents.json").read_text(encoding="utf-8"))
            self.assertTrue(documents)
            self.assertTrue(all(doc["metadata"]["snapshot_id"] == snapshot["snapshot_id"] for doc in documents))
            self.assertTrue(all(doc["metadata"]["source"] == "Supercell API live sample" for doc in documents))
            validation = validate_snapshot_rag_documents(published, documents)
            self.assertTrue(validation["passed"], validation)
            self.assertEqual(validation["card_documents_checked"], len(published["cards_meta"]))
            self.assertEqual(validation["deck_documents_checked"], len(published["top_decks"]))

    def test_publish_streams_raw_records_and_preserves_the_exact_aggregate_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "published"
            work_dir = data_dir / "snapshot_work" / "collection-test"
            work_dir.mkdir(parents=True)
            raw_path = work_dir / "raw_battles.jsonl"
            records = [
                {
                    "battle_id": "battle-1",
                    "team_deck": ["Electro Giant", "Tornado"],
                    "opponent_deck": ["P.E.K.K.A", "Zap"],
                    "won": True,
                },
                {
                    "battle_id": "battle-2",
                    "team_deck": ["P.E.K.K.A", "Zap"],
                    "opponent_deck": ["Electro Giant", "Tornado"],
                    "won": False,
                },
            ]
            raw_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            aggregate_path = work_dir / "aggregates.sqlite"
            aggregate_path.write_bytes(b"exact-aggregate-store")
            snapshot = complete_snapshot()
            snapshot.update(
                {
                    "sample_battles": 2,
                    "target_battles": 2,
                    "raw_battles": JsonlRecordSequence(raw_path, 2),
                    "_aggregate_store_path": str(aggregate_path),
                    "_streaming_work_dir": str(work_dir),
                }
            )

            with patch("snapshot_store.DAILY_TARGET_BATTLES", 2):
                published = publish_daily_snapshot(snapshot, data_dir)
                collector_summary = load_published_snapshot_summary(data_dir)

            canonical = json.loads((data_dir / "official_daily_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(canonical["raw_battles"], records)
            aggregate_file = data_dir / published["aggregate_store"]["canonical_file"]
            self.assertEqual(aggregate_file.read_bytes(), b"exact-aggregate-store")
            self.assertEqual(published["raw_battles"], [])
            self.assertFalse(work_dir.exists())
            self.assertEqual(collector_summary["snapshot_id"], snapshot["snapshot_id"])
            self.assertEqual(collector_summary["raw_battles"], [])
            self.assertFalse(collector_summary["raw_battles_storage"]["loaded"])

    def test_publish_records_the_exact_rag_document_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            published = publish_daily_snapshot(complete_snapshot(), data_dir)
            documents = json.loads((data_dir / "rag_documents.json").read_text(encoding="utf-8"))

            self.assertEqual(
                published["rag_docs_fingerprint"],
                compute_rag_docs_fingerprint(documents),
            )
            self.assertEqual(
                load_published_snapshot(data_dir)["rag_docs_fingerprint"],
                published["rag_docs_fingerprint"],
            )

    def test_publish_rejects_rag_documents_with_missing_card_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid = complete_snapshot()
            invalid["cards_meta"][0]["win_rate"] = None

            with self.assertRaises(ValueError):
                publish_daily_snapshot(invalid, Path(temp_dir))

            self.assertFalse((Path(temp_dir) / "official_daily_snapshot.json").exists())

    def test_publish_rejects_out_of_scope_battles_from_a_path_of_legend_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = complete_snapshot()
            snapshot.update(
                {
                    "sample_battles": 1,
                    "target_battles": 1,
                    "collection_scope": PATH_OF_LEGEND_COLLECTION_SCOPE,
                    "scope_contract": PATH_OF_LEGEND_SCOPE_CONTRACT,
                    "raw_battles": [{"battle_id": "wrong-mode", "battle_type": "PvP"}],
                }
            )

            with patch("snapshot_store.DAILY_TARGET_BATTLES", 1), self.assertRaisesRegex(
                ValueError, "out-of-scope raw battles"
            ):
                publish_daily_snapshot(snapshot, Path(temp_dir))

    def test_invalid_evidence_does_not_overwrite_previous_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            previous = publish_daily_snapshot(complete_snapshot(), data_dir)
            invalid = complete_snapshot()
            invalid["snapshot_id"] = "official-20260726-invalid"
            invalid["cards_meta"][0]["appearance_count"] = None

            with self.assertRaises(ValueError):
                publish_daily_snapshot(invalid, data_dir)

            restored = load_published_snapshot(data_dir)
            self.assertEqual(restored["snapshot_id"], previous["snapshot_id"])
            self.assertEqual(restored["rag_docs_fingerprint"], previous["rag_docs_fingerprint"])

    def test_consecutive_snapshot_publications_rebuild_all_documents_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            first = publish_daily_snapshot(complete_snapshot(), data_dir)
            second_input = complete_snapshot(fetched_at="2026-07-26T00:00:00+00:00")
            second_input["snapshot_id"] = "official-20260726-b"
            second_input["cards_meta"][0]["usage_rate"] = 9.2
            second_input["cards_meta"][0]["appearance_count"] = 1840
            second = publish_daily_snapshot(second_input, data_dir)
            documents = json.loads((data_dir / "rag_documents.json").read_text(encoding="utf-8"))

            self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertNotEqual(first["rag_docs_fingerprint"], second["rag_docs_fingerprint"])
            self.assertEqual(load_published_snapshot(data_dir)["snapshot_id"], second["snapshot_id"])
            self.assertTrue(validate_snapshot_rag_documents(second, documents)["passed"])

    def test_validator_rejects_any_card_or_deck_document_that_disagrees_with_snapshot(self):
        snapshot = complete_snapshot()
        documents = build_snapshot_rag_documents(snapshot)
        card_document = next(doc for doc in documents if doc["source_type"] == "card")
        card_document["metadata"]["usage_rate"] = 99.9
        deck_document = next(doc for doc in documents if doc["source_type"] == "deck")
        deck_document["metadata"]["cards"] = ["Electro Giant"]

        report = validate_snapshot_rag_documents(snapshot, documents)

        self.assertFalse(report["passed"])
        self.assertIn(card_document["doc_id"], report["invalid_doc_ids"])
        self.assertIn(deck_document["doc_id"], report["invalid_doc_ids"])
        self.assertIn("card_document_mismatch", report["failures"])
        self.assertIn("deck_document_mismatch", report["failures"])

    def test_validator_rejects_none_percent_and_incomplete_aggregate_metadata(self):
        snapshot = complete_snapshot()
        documents = build_snapshot_rag_documents(snapshot)
        card_document = next(doc for doc in documents if doc["source_type"] == "card")
        card_document["text"] = card_document["text"].replace("54.2%", "None%")

        report = validate_snapshot_rag_documents(snapshot, documents)

        self.assertFalse(report["passed"])
        self.assertIn("invalid_evidence_fields", report["failures"])
        self.assertIn(card_document["doc_id"], report["invalid_doc_ids"])

    def test_document_fingerprint_changes_when_same_snapshot_documents_change(self):
        documents = build_snapshot_rag_documents(complete_snapshot())
        original = compute_rag_docs_fingerprint(documents)
        documents[0]["text"] += " changed"

        self.assertNotEqual(compute_rag_docs_fingerprint(documents), original)

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
            partial["shortfall_battles"] = DAILY_TARGET_BATTLES - 100

            with self.assertRaises(ValueError):
                publish_daily_snapshot(partial, data_dir)

            self.assertEqual(load_published_snapshot(data_dir)["snapshot_id"], published["snapshot_id"])

    def test_snapshot_is_due_only_after_weekly_refresh_interval(self):
        now = datetime(2026, 7, 25, tzinfo=timezone.utc)
        fresh = complete_snapshot(fetched_at=(now - DAILY_REFRESH_INTERVAL + timedelta(minutes=1)).isoformat())
        stale = complete_snapshot(fetched_at=(now - DAILY_REFRESH_INTERVAL).isoformat())

        self.assertFalse(snapshot_refresh_due(fresh, now=now))
        self.assertTrue(snapshot_refresh_due(stale, now=now))

    def test_cleanup_retains_only_current_and_previous_complete_snapshot_packages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            now = datetime(2026, 7, 28, tzinfo=timezone.utc)
            snapshot_ids = ["official-current", "official-previous", "official-old"]
            published_times = [
                now,
                now - timedelta(days=7),
                now - timedelta(days=15),
            ]
            for snapshot_id, published_at in zip(snapshot_ids, published_times):
                archive_dir = data_dir / "snapshot_archives" / snapshot_id
                qdrant_dir = data_dir / "daily_snapshot_qdrant" / snapshot_id
                audit_dir = data_dir / "audit_exports" / snapshot_id
                review_dir = data_dir / "external_reviews" / snapshot_id
                structured_dir = data_dir / "structured_stats" / snapshot_id
                for directory in (archive_dir, qdrant_dir, audit_dir, review_dir, structured_dir):
                    directory.mkdir(parents=True)
                    (directory / "marker.txt").write_text(snapshot_id, encoding="utf-8")
                manifest = {
                    "snapshot_id": snapshot_id,
                    "published_at": published_at.isoformat(),
                    "complete": True,
                }
                (archive_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            report = cleanup_snapshot_retention(
                data_dir,
                active_snapshot_id="official-current",
                now=now,
            )

            self.assertEqual(SNAPSHOT_RETENTION_DAYS, 14)
            self.assertEqual(SNAPSHOT_RETENTION_MAX_COMPLETE, 2)
            self.assertEqual(report["retained_snapshot_ids"], ["official-current", "official-previous"])
            self.assertIn("official-old", report["removed_snapshot_ids"])
            for kept in ("official-current", "official-previous"):
                self.assertTrue((data_dir / "snapshot_archives" / kept).exists())
                self.assertTrue((data_dir / "daily_snapshot_qdrant" / kept).exists())
                self.assertTrue((data_dir / "audit_exports" / kept).exists())
                self.assertTrue((data_dir / "external_reviews" / kept).exists())
                self.assertTrue((data_dir / "structured_stats" / kept).exists())
            self.assertFalse((data_dir / "snapshot_archives" / "official-old").exists())
            self.assertFalse((data_dir / "daily_snapshot_qdrant" / "official-old").exists())
            self.assertFalse((data_dir / "audit_exports" / "official-old").exists())
            self.assertFalse((data_dir / "external_reviews" / "official-old").exists())
            self.assertFalse((data_dir / "structured_stats" / "official-old").exists())

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
        profiles = {
            document["metadata"]["card_name"]: document
            for document in by_type["card_profile"]
        }
        self.assertEqual(profiles["Electro Giant"]["metadata"]["games"], 20)
        self.assertEqual(profiles["P.E.K.K.A"]["metadata"]["games"], 20)


if __name__ == "__main__":
    unittest.main()
