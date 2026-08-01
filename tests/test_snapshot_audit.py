import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from snapshot_audit import (
    ExternalReviewValidationError,
    export_snapshot_audit,
    import_reviewed_rag_documents,
)
from snapshot_store import compute_rag_docs_fingerprint


SNAPSHOT_ID = "supercell-test-audit"
CARDS_A = [f"Card A{i}" for i in range(8)]
CARDS_B = [f"Card B{i}" for i in range(8)]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _create_snapshot_package(data_dir: Path) -> list[dict]:
    archive = data_dir / "snapshot_archives" / SNAPSHOT_ID
    archive.mkdir(parents=True)
    database = sqlite3.connect(archive / "aggregates.sqlite")
    database.executescript(
        """
        CREATE TABLE battles(sequence INTEGER PRIMARY KEY, battle_id TEXT UNIQUE, payload TEXT NOT NULL);
        CREATE TABLE card_stats(card_name TEXT PRIMARY KEY, appearances INTEGER, wins INTEGER);
        CREATE TABLE deck_stats(
            deck_key TEXT PRIMARY KEY, deck_json TEXT, battles INTEGER, wins INTEGER,
            elixir_total REAL, elixir_samples INTEGER
        );
        CREATE TABLE matchup_stats(
            deck_key TEXT, opponent_key TEXT, opponent_json TEXT, games INTEGER, wins INTEGER,
            PRIMARY KEY(deck_key, opponent_key)
        );
        CREATE TABLE probe_battles(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        """
    )
    battles = [
        {
            "battle_id": "battle-1",
            "battle_time": "20260728T010000.000Z",
            "team_tag": "#AAA",
            "opponent_tag": "#BBB",
            "team_deck": CARDS_A,
            "opponent_deck": CARDS_B,
            "team_crowns": 2,
            "opponent_crowns": 1,
            "won": True,
        },
        {
            "battle_id": "battle-2",
            "battle_time": "20260728T010100.000Z",
            "team_tag": "#CCC",
            "opponent_tag": "#DDD",
            "team_deck": CARDS_B,
            "opponent_deck": CARDS_A,
            "team_crowns": 1,
            "opponent_crowns": 1,
            "won": False,
        },
    ]
    database.executemany(
        "INSERT INTO battles(sequence, battle_id, payload) VALUES (?, ?, ?)",
        [(index, battle["battle_id"], json.dumps(battle)) for index, battle in enumerate(battles, 1)],
    )
    database.executemany(
        "INSERT INTO card_stats(card_name, appearances, wins) VALUES (?, ?, ?)",
        [(card, 1, 1) for card in CARDS_A] + [(card, 1, 0) for card in CARDS_B],
    )
    deck_a = json.dumps(CARDS_A, separators=(",", ":"))
    deck_b = json.dumps(CARDS_B, separators=(",", ":"))
    database.executemany(
        "INSERT INTO deck_stats VALUES (?, ?, ?, ?, ?, ?)",
        [(deck_a, deck_a, 1, 1, 3.5, 1), (deck_b, deck_b, 1, 0, 4.0, 1)],
    )
    database.execute(
        "INSERT INTO matchup_stats VALUES (?, ?, ?, ?, ?)",
        (deck_a, deck_b, deck_b, 1, 1),
    )
    database.executemany(
        "INSERT INTO probe_battles(sequence, payload) VALUES (?, ?)",
        [(1, json.dumps({"battleTime": "20260728T010000.000Z", "team": [{"tag": "#AAA"}]}))],
    )
    database.commit()
    database.close()

    documents = [
        {
            "doc_id": f"{SNAPSHOT_ID}:overview",
            "source_type": "snapshot",
            "text": "Snapshot evidence covers 2 battles.",
            "metadata": {
                "snapshot_id": SNAPSHOT_ID,
                "fetched_at": "2026-07-28T01:02:00+00:00",
                "sample_battles": 2,
                "source": "Supercell API live sample",
            },
        },
        {
            "doc_id": f"{SNAPSHOT_ID}:archetype:test",
            "source_type": "archetype",
            "text": "Test archetype has 1 game and a 100.0% observed win rate.",
            "metadata": {
                "snapshot_id": SNAPSHOT_ID,
                "fetched_at": "2026-07-28T01:02:00+00:00",
                "sample_battles": 2,
                "source": "Supercell API live sample",
                "archetype": "Test",
                "games": 1,
                "sample_win_rate": 100.0,
            },
        },
    ]
    _write_json(archive / "rag_documents.json", documents)
    _write_json(
        archive / "collector_snapshot.json",
        {
            "snapshot_id": SNAPSHOT_ID,
            "fetched_at": "2026-07-28T01:02:00+00:00",
            "published_at": "2026-07-28T01:03:00+00:00",
            "sample_battles": 2,
            "target_battles": 2,
            "shortfall_battles": 0,
            "rag_docs_fingerprint": compute_rag_docs_fingerprint(documents),
        },
    )
    _write_json(
        archive / "manifest.json",
        {"schema_version": 1, "snapshot_id": SNAPSHOT_ID, "complete": True, "sample_battles": 2},
    )
    _write_json(data_dir / "official_snapshot_pointer.json", {"snapshot_id": SNAPSHOT_ID})
    _write_json(data_dir / "rag_documents.json", documents)
    return documents


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class SnapshotAuditExportTests(unittest.TestCase):
    def test_export_streams_normalized_battles_and_records_verified_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _create_snapshot_package(data_dir)

            manifest = export_snapshot_audit(data_dir, SNAPSHOT_ID, partition_size=1)
            export_dir = data_dir / "audit_exports" / SNAPSHOT_ID

            self.assertEqual(manifest["snapshot_id"], SNAPSHOT_ID)
            self.assertEqual(manifest["source_boundaries"]["raw_api_payload_coverage"], "probe_subset_only")
            self.assertEqual(manifest["counts"]["normalized_battles"], 2)
            self.assertEqual(manifest["counts"]["side_records"], 4)
            self.assertEqual(len(list(export_dir.glob("normalized_battles.part-*.jsonl"))), 2)
            self.assertEqual(len(_read_jsonl(export_dir / "side_records.jsonl")), 4)
            self.assertEqual(len(_read_jsonl(export_dir / "raw_battlelogs.part-00001.jsonl")), 1)

            for entry in manifest["files"]:
                payload = (export_dir / entry["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"])
                self.assertEqual(len(payload), entry["bytes"])

            with (export_dir / "decks.csv").open(encoding="utf-8", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 2)


class ExternalReviewImportTests(unittest.TestCase):
    def test_valid_review_is_staged_after_local_validation_without_replacing_active_docs(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            documents = _create_snapshot_package(data_dir)
            export_snapshot_audit(data_dir, SNAPSHOT_ID)
            active_before = (data_dir / "rag_documents.json").read_bytes()
            reviewed = [dict(document) for document in documents]
            reviewed[0] = {**reviewed[0], "text": "This reviewed snapshot still covers 2 battles."}
            incoming = data_dir / "incoming.reviewed.jsonl"
            incoming.write_text(
                "".join(json.dumps(document, ensure_ascii=False) + "\n" for document in reviewed),
                encoding="utf-8",
            )

            report = import_reviewed_rag_documents(data_dir, SNAPSHOT_ID, incoming)

            self.assertTrue(report["passed"])
            self.assertEqual(report["document_count"], 2)
            self.assertEqual((data_dir / "rag_documents.json").read_bytes(), active_before)
            staged = data_dir / "external_reviews" / SNAPSHOT_ID / "rag_documents.reviewed.jsonl"
            self.assertEqual(_read_jsonl(staged), reviewed)

    def test_changed_numeric_claim_is_rejected_without_replacing_active_docs(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            documents = _create_snapshot_package(data_dir)
            export_snapshot_audit(data_dir, SNAPSHOT_ID)
            active_before = (data_dir / "rag_documents.json").read_bytes()
            reviewed = [dict(document) for document in documents]
            reviewed[1] = {**reviewed[1], "text": "Test archetype has 99 games and a 100.0% observed win rate."}
            incoming = data_dir / "incoming.reviewed.jsonl"
            incoming.write_text(
                "".join(json.dumps(document, ensure_ascii=False) + "\n" for document in reviewed),
                encoding="utf-8",
            )

            with self.assertRaises(ExternalReviewValidationError) as caught:
                import_reviewed_rag_documents(data_dir, SNAPSHOT_ID, incoming)

            self.assertIn("numeric_claim_mismatch", caught.exception.report["failures"])
            self.assertEqual((data_dir / "rag_documents.json").read_bytes(), active_before)
            self.assertFalse(
                (data_dir / "external_reviews" / SNAPSHOT_ID / "rag_documents.reviewed.jsonl").exists()
            )

    def test_tampered_audit_file_is_rejected_before_review_import(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            documents = _create_snapshot_package(data_dir)
            export_snapshot_audit(data_dir, SNAPSHOT_ID)
            active_before = (data_dir / "rag_documents.json").read_bytes()
            cards_path = data_dir / "audit_exports" / SNAPSHOT_ID / "cards.csv"
            cards_path.write_text(cards_path.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
            incoming = data_dir / "incoming.reviewed.jsonl"
            incoming.write_text(
                "".join(json.dumps(document, ensure_ascii=False) + "\n" for document in documents),
                encoding="utf-8",
            )

            with self.assertRaises(ExternalReviewValidationError) as caught:
                import_reviewed_rag_documents(data_dir, SNAPSHOT_ID, incoming)

            self.assertIn("audit_file_hash_mismatch", caught.exception.report["failures"])
            self.assertEqual((data_dir / "rag_documents.json").read_bytes(), active_before)


if __name__ == "__main__":
    unittest.main()
