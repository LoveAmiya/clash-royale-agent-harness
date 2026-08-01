import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rolling_corpus import DATASET_SCOPES, RollingCorpusStore
from rolling_materializer import build_snapshot_group
from structured_query import StructuredStatsRepository


class FakeRetriever:
    def __init__(self, docs, *, index_path, **_kwargs):
        self.docs = docs
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.docs_fingerprint = hashlib.sha256(
            json.dumps(docs, ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.dense_available = True

    def hybrid_search(self, query, *, dataset_scope=None, **_kwargs):
        matches = [doc for doc in self.docs if doc["metadata"]["dataset_scope"] == dataset_scope]
        return [{"doc": matches[0]}] if matches else []

    def close(self):
        return None


class FailingRetriever:
    def __init__(self, *_args, **_kwargs):
        raise RuntimeError("index build failed")


def _battle(battle_id: str) -> dict:
    return {
        "battle_id": battle_id,
        "battle_type": "pathOfLegend",
        "battle_time": "20260730T000000.000Z",
        "team_tag": "#A",
        "opponent_tag": "#B",
        "team_deck": ["A", "B", "C", "D", "E", "F", "G", "H"],
        "opponent_deck": ["I", "J", "K", "L", "M", "N", "O", "P"],
        "team_crowns": 1,
        "opponent_crowns": 0,
        "won": True,
    }


class RollingMaterializerTests(unittest.TestCase):
    def test_builds_one_scoped_stats_database_and_atomically_publishes_thirty_ranges(self):
        now = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            store = RollingCorpusStore(data_dir / "corpus" / "corpus.sqlite")
            store.create_batch(
                "weekly-1",
                batch_type="weekly_expanded",
                started_at=now,
                leaderboard_frozen_at=now,
            )
            store.accept_batch_for_test("weekly-1", completed_at=now)
            store.ingest_battle(
                "weekly-1",
                _battle("battle-1"),
                observer_tag="#A",
                observer_rank=50,
                observer_source="ranked_direct",
                observed_at=now,
            )
            previous = now - timedelta(days=10)
            store.create_batch(
                "daily-previous",
                batch_type="daily_ranked",
                started_at=previous,
                leaderboard_frozen_at=previous,
            )
            store.accept_batch_for_test("daily-previous", completed_at=previous)
            previous_battle = _battle("battle-previous")
            previous_battle["battle_time"] = "20260720T000000.000Z"
            store.ingest_battle(
                "daily-previous",
                previous_battle,
                observer_tag="#OLD",
                observer_rank=50,
                observer_source="ranked_direct",
                observed_at=previous,
            )
            expanded = _battle("battle-2")
            expanded["battle_time"] = "20260730T000001.000Z"
            store.ingest_battle(
                "weekly-1",
                expanded,
                observer_tag="#EXPANDED",
                observer_rank=None,
                observer_source="opponent_expansion",
                observed_at=now,
            )

            manifest = build_snapshot_group(
                store,
                data_dir=data_dir,
                now=now,
                retriever_factory=FakeRetriever,
            )
            store.close()

            pointer = json.loads((data_dir / "active_snapshot_group.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["snapshot_group_id"], manifest["snapshot_group_id"])
            self.assertEqual(set(manifest["datasets"]), set(DATASET_SCOPES))
            self.assertTrue(manifest["fully_aligned"])
            self.assertTrue(manifest["datasets"]["7d_all"]["delta_ready"])

            stats_path = data_dir / "snapshot_groups" / manifest["snapshot_group_id"] / "structured_stats.sqlite"
            connection = sqlite3.connect(stats_path)
            scopes = {row[0] for row in connection.execute("SELECT dataset_scope FROM scope_metadata")}
            card_scopes = {row[0] for row in connection.execute("SELECT DISTINCT dataset_scope FROM card_stats")}
            delta_count = connection.execute(
                "SELECT COUNT(*) FROM meta_delta WHERE current_scope='7d_all' AND baseline_scope='d7_14_all'"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(scopes, set(DATASET_SCOPES))
            ready_scopes = {
                scope for scope, dataset in manifest["datasets"].items() if dataset["ready"]
            }
            self.assertEqual(card_scopes, ready_scopes)
            self.assertTrue(all(not manifest["datasets"][scope]["ready"] for scope in DATASET_SCOPES if scope.startswith(("d14_21_", "d21_28_", "d28_35_"))))
            self.assertGreater(delta_count, 0)

            documents = json.loads(
                (data_dir / "snapshot_groups" / manifest["snapshot_group_id"] / "rag_documents.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual({doc["metadata"]["dataset_scope"] for doc in documents}, set(DATASET_SCOPES))
            self.assertTrue(all(doc["metadata"]["snapshot_group_id"] == manifest["snapshot_group_id"] for doc in documents))
            self.assertTrue(any(doc["source_type"] == "meta_delta" for doc in documents))

            top_repository = StructuredStatsRepository.for_snapshot_group(
                data_dir,
                manifest["snapshot_group_id"],
                "7d_top_100",
            )
            all_repository = StructuredStatsRepository.for_snapshot_group(
                data_dir,
                manifest["snapshot_group_id"],
                "7d_all",
            )
            top_card = top_repository.card_stats("A")
            all_card = all_repository.card_stats("A")
            self.assertEqual(top_card["provenance"]["dataset_scope"], "7d_top_100")
            self.assertEqual(top_card["provenance"]["unique_battles"], 1)
            self.assertEqual(all_card["provenance"]["unique_battles"], 2)

    def test_failed_scope_group_never_replaces_active_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            store = RollingCorpusStore(data_dir / "corpus" / "corpus.sqlite")
            now = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)
            store.create_batch(
                "daily-1",
                batch_type="daily_ranked",
                started_at=now,
                leaderboard_frozen_at=now,
            )
            store.accept_batch_for_test("daily-1", completed_at=now)
            store.create_batch(
                "weekly-1",
                batch_type="weekly_expanded",
                started_at=now,
                leaderboard_frozen_at=now,
            )
            store.accept_batch_for_test("weekly-1", completed_at=now)
            store.ingest_battle(
                "daily-1",
                _battle("ranked"),
                observer_tag="#A",
                observer_rank=1,
                observer_source="ranked_direct",
                observed_at=now,
            )
            old_pointer = {"schema_version": 1, "snapshot_group_id": "previous"}
            (data_dir / "active_snapshot_group.json").write_text(
                json.dumps(old_pointer), encoding="utf-8"
            )

            with self.assertRaisesRegex(RuntimeError, "index build failed"):
                build_snapshot_group(
                    store,
                    data_dir=data_dir,
                    now=now,
                    retriever_factory=FailingRetriever,
                )

            pointer = json.loads((data_dir / "active_snapshot_group.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer, old_pointer)
            generation = store.connection.execute(
                "SELECT status FROM publication_generations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(generation["status"], "failed")
            store.close()


if __name__ == "__main__":
    unittest.main()
