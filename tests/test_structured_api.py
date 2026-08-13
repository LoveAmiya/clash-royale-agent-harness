import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from support import install_test_stubs

install_test_stubs()

from fastapi.testclient import TestClient

import runtime_multi
from rolling_corpus import DATASET_SCOPES
from structured_query import StructuredQueryError


class _Repository:
    def card_catalog(self):
        return {"cards": [{"card_id": "Hog Rider", "display_name_zh": "\u91ce\u732a\u9a91\u58eb"}]}

    def card_stats(self, card_id):
        return {"card": {"card_name": card_id}, "matched_sample_count": 100, "provenance": {}}

    def card_rankings(self, sort_by):
        return {"sort_by": sort_by, "cards": [{"card_name": "Hog Rider", "rank": 1}]}

    def entity_catalog(self):
        return {"entities": [{"entity_id": "card:26000000:evolution", "display_name_zh": "觉醒骑士"}]}

    def entity_rankings(self, sort_by):
        return {"sort_by": sort_by, "entities": [{"entity_id": "tower:159000000", "rank": 1}]}

    def entity_stats(self, entity_id):
        return {"entity": {"entity_id": entity_id}, "matched_sample_count": 10, "provenance": {}}

    def compare_entities(self, entity_ids):
        return {"entities": entity_ids, "differences": {}, "provenance": {}}

    def compare_cards(self, card_ids):
        return {"cards": card_ids, "differences": {}, "provenance": {}}

    def deck_profile(self, cards):
        return {"deck": {"cards": cards}, "matched_sample_count": 10, "provenance": {}}

    def deck_matchup(self, deck_a, deck_b):
        raise StructuredQueryError(
            "NO_MATCHUP_EVIDENCE",
            "No exact battles were found between these two 8-card decks.",
            status_code=404,
            details={"matched_sample_count": 0},
        )

    def full_loadout_profile(self, loadout):
        return {"deck_mode": "full_loadout", "loadout": loadout, "matched_sample_count": 3}

    def full_loadout_matchup(self, loadout_a, loadout_b):
        return {
            "deck_mode": "full_loadout",
            "loadout_a": loadout_a,
            "loadout_b": loadout_b,
            "matched_sample_count": 1,
        }

    def loadout_catalog(self):
        return {"towers": [], "cards": [], "deck_mode": "full_loadout"}

    def archetypes(self):
        return {"archetypes": [], "matched_sample_count": 0, "provenance": {}}


class StructuredAPIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(runtime_multi.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_structured_endpoints_use_repository_contracts(self):
        cards = [f"Card {index}" for index in range(8)]
        with patch.object(runtime_multi, "get_structured_repository", return_value=_Repository(), create=True):
            catalog = self.client.get("/api/cards/catalog")
            rankings = self.client.get("/api/cards/rankings?sort_by=rating&dataset_scope=35d_all")
            card = self.client.get("/api/cards/Hog%20Rider/stats")
            comparison = self.client.post("/api/cards/compare", json={"card_ids": ["A", "B"]})
            entity_catalog = self.client.get("/api/entities/catalog")
            entity_rankings = self.client.get("/api/entities/rankings?sort_by=usage_rate")
            entity_stats = self.client.get("/api/entities/card%3A26000000%3Aevolution/stats")
            entity_compare = self.client.post(
                "/api/entities/compare",
                json={"entity_ids": ["card:26000000:evolution", "tower:159000000"]},
            )
            profile = self.client.post("/api/decks/profile", json={"cards": cards})
            environment = self.client.get("/api/meta/archetypes")
            loadout_catalog = self.client.get("/api/loadouts/catalog")

        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(rankings.status_code, 200)
        self.assertEqual(rankings.json()["sort_by"], "rating")
        self.assertEqual(card.status_code, 200)
        self.assertEqual(comparison.status_code, 200)
        self.assertEqual(entity_catalog.status_code, 200)
        self.assertEqual(entity_rankings.status_code, 200)
        self.assertEqual(entity_stats.status_code, 200)
        self.assertEqual(entity_compare.status_code, 200)
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(environment.status_code, 200)
        self.assertEqual(loadout_catalog.status_code, 200)

    def test_card_rankings_reject_invalid_metric_with_fixed_error_code(self):
        with patch.object(runtime_multi, "get_structured_repository", return_value=_Repository(), create=True):
            response = self.client.get("/api/cards/rankings?sort_by=appearances")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CARD_RANKING_METRIC")

    def test_dataset_scope_is_forwarded_without_model_routing(self):
        repository = _Repository()
        with patch.object(
            runtime_multi,
            "get_structured_repository",
            return_value=repository,
            create=True,
        ) as get_repository:
            response = self.client.post(
                "/api/cards/compare",
                json={"card_ids": ["A", "B"], "dataset_scope": "35d_top_500"},
            )

        self.assertEqual(response.status_code, 200)
        get_repository.assert_called_once_with(runtime_multi.app, "35d_top_500")

    def test_full_loadout_mode_uses_explicit_tower_evolution_and_elite_contract(self):
        loadout = {
            "tower_id": "159000000",
            "cards": [
                {
                    "card_id": str(26000000 + index),
                    "evolution_level": 1 if index == 0 else 2 if index == 1 else 0,
                    "elite": index == 1,
                }
                for index in range(8)
            ],
        }
        with patch.object(runtime_multi, "get_structured_repository", return_value=_Repository(), create=True):
            profile = self.client.post(
                "/api/decks/profile",
                json={"deck_mode": "full_loadout", "loadout": loadout},
            )
            matchup = self.client.post(
                "/api/decks/matchup",
                json={
                    "deck_mode": "full_loadout",
                    "loadout_a": loadout,
                    "loadout_b": {**loadout, "tower_id": "159000001"},
                },
            )

        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["deck_mode"], "full_loadout")
        self.assertEqual(profile.json()["loadout"]["tower"]["id"], "159000000")
        self.assertEqual(matchup.status_code, 200)
        self.assertEqual(matchup.json()["matched_sample_count"], 1)

    def test_invalid_dataset_scope_has_fixed_error_code(self):
        response = self.client.get("/api/cards/catalog?dataset_scope=not-a-scope")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_DATASET_SCOPE")

    def test_process_request_preserves_entity_mode_for_free_answering(self):
        payload = runtime_multi.ProcessRequest(
            input=[{"role": "user", "content": [{"type": "text", "text": "evolved knight usage"}]}],
            entity_mode="loadout_entity",
        )

        self.assertEqual(payload.entity_mode, "loadout_entity")

    def test_dataset_catalog_exposes_all_ten_published_scopes(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            group_id = "group-1"
            group_dir = data_dir / "snapshot_groups" / group_id
            group_dir.mkdir(parents=True)
            datasets = {
                scope: {
                    "snapshot_id": f"{group_id}--{scope}",
                    "window_days": 35 if scope.startswith("35d_") else 7,
                    "rank_limit": None,
                    "window_started_at": "2026-07-23T00:00:00+00:00",
                    "window_ended_at": "2026-07-30T00:00:00+00:00",
                    "unique_battles": 10,
                    "weekly_batch_count": 1,
                    "daily_batch_count": 1,
                    "ranked_coverage": 1.0,
                    "missing_collection_dates": [],
                    "window_kind": "rolling" if scope.startswith(("7d_", "35d_")) else "historical_slice",
                    "window_start_offset_days": 0,
                    "window_end_offset_days": 35 if scope.startswith("35d_") else 7,
                    "complete_loadout_ready": True,
                    "entity_stats_ready": True,
                    "delta_ready": scope.startswith("7d_"),
                }
                for scope in DATASET_SCOPES
            }
            (data_dir / "active_snapshot_group.json").write_text(
                json.dumps({"snapshot_group_id": group_id}), encoding="utf-8"
            )
            (group_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "snapshot_group_id": group_id,
                        "published_at": "2026-07-30T00:00:00+00:00",
                        "default_dataset_scope": "7d_all",
                        "datasets": datasets,
                        "rag_docs_fingerprint": "same",
                        "index_docs_fingerprint": "same",
                        "rag_document_count": 100,
                        "rag_scope_counts": {scope: index + 1 for index, scope in enumerate(DATASET_SCOPES)},
                        "rag_scope_source_counts": {
                            scope: {"deck": 150, "matchup": 500, "card_profile": 12}
                            for scope in DATASET_SCOPES
                        },
                        "fully_aligned": True,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(runtime_multi, "DATA_DIR", data_dir):
                payload = runtime_multi.get_dataset_catalog(runtime_multi.app)

        self.assertEqual(payload["snapshot_group_id"], group_id)
        self.assertEqual({item["dataset_scope"] for item in payload["datasets"]}, set(DATASET_SCOPES))
        self.assertEqual(len(payload["datasets"]), 30)
        self.assertTrue(all("entity_stats_ready" in item for item in payload["datasets"]))
        self.assertTrue(payload["rag"]["fully_aligned"])
        self.assertEqual(payload["rag"]["document_count_semantics"], "scope_sum_including_duplicates")
        self.assertEqual(payload["rag"]["scope_document_count_semantics"], "bounded_evidence_documents")
        self.assertTrue(payload["rag"]["global_count_includes_scope_duplicates"])
        self.assertEqual(payload["rag"]["retrieval"]["fusion_mode"], "rrf")
        self.assertGreater(payload["rag"]["retrieval"]["candidate_top_k"], payload["rag"]["retrieval"]["evidence_top_n"])
        by_scope = {item["dataset_scope"]: item for item in payload["datasets"]}
        self.assertEqual(by_scope["7d_all"]["rag_document_count"], DATASET_SCOPES.index("7d_all") + 1)
        self.assertEqual(by_scope["7d_all"]["rag_source_counts"]["deck"], 150)
        self.assertIn("deck", by_scope["7d_all"]["rag_saturated_source_types"])
        self.assertNotIn("card_profile", by_scope["7d_all"]["rag_saturated_source_types"])

    def test_legacy_ten_scope_group_remains_available_without_faking_new_scopes(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            group_id = "legacy-group"
            group_dir = data_dir / "snapshot_groups" / group_id
            group_dir.mkdir(parents=True)
            (group_dir / "rag_documents.json").write_text(
                json.dumps(
                    [
                        {"source_type": "deck", "metadata": {"dataset_scope": "7d_all"}},
                        {"source_type": "matchup", "metadata": {"dataset_scope": "7d_all"}},
                        {"source_type": "deck", "metadata": {"dataset_scope": "35d_all"}},
                    ]
                ),
                encoding="utf-8",
            )
            legacy_scopes = [
                scope
                for scope in DATASET_SCOPES
                if scope.startswith("7d_") or scope.startswith("35d_")
            ]
            datasets = {
                scope: {
                    "snapshot_id": f"{group_id}--{scope}",
                    "window_days": 35 if scope.startswith("35d_") else 7,
                    "unique_battles": 10,
                    "weekly_batch_count": 1,
                    "daily_batch_count": 0,
                    "ranked_coverage": 1.0,
                    "missing_collection_dates": [],
                    "structured_counts": {"full_loadout_side_records": 20},
                }
                for scope in legacy_scopes
            }
            (data_dir / "active_snapshot_group.json").write_text(
                json.dumps({"snapshot_group_id": group_id}), encoding="utf-8"
            )
            (group_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "snapshot_group_id": group_id,
                        "default_dataset_scope": "7d_all",
                        "datasets": datasets,
                        "rag_docs_fingerprint": "same",
                        "index_docs_fingerprint": "same",
                        "fully_aligned": True,
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(runtime_multi, "DATA_DIR", data_dir):
                payload = runtime_multi.get_dataset_catalog(runtime_multi.app)

        by_scope = {item["dataset_scope"]: item for item in payload["datasets"]}
        self.assertEqual(len(by_scope), 30)
        self.assertTrue(all(by_scope[scope]["ready"] for scope in legacy_scopes))
        self.assertTrue(all(not item["ready"] for scope, item in by_scope.items() if scope not in legacy_scopes))
        self.assertTrue(by_scope["7d_all"]["complete_loadout_ready"])
        self.assertFalse(by_scope["7d_all"]["entity_stats_ready"])
        self.assertEqual(by_scope["7d_all"]["rag_document_count"], 2)
        self.assertEqual(by_scope["35d_all"]["rag_document_count"], 1)
        self.assertEqual(by_scope["7d_all"]["rag_source_counts"], {"deck": 1, "matchup": 1})

    def test_structured_errors_have_one_predictable_shape(self):
        cards = [f"Card {index}" for index in range(8)]
        with patch.object(runtime_multi, "get_structured_repository", return_value=_Repository(), create=True):
            response = self.client.post(
                "/api/decks/matchup",
                json={"deck_a": cards, "deck_b": list(reversed(cards))},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NO_MATCHUP_EVIDENCE")
        self.assertEqual(response.json()["error"]["details"]["matched_sample_count"], 0)

    def test_snapshot_artifact_status_reports_aligned_local_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            for root, counts in (
                ("audit_exports", {"normalized_battles": 200000}),
                ("structured_stats", {"included_battles": 198295}),
            ):
                target = data_dir / root / "snapshot-1"
                target.mkdir(parents=True)
                (target / "manifest.json").write_text(
                    json.dumps({"snapshot_id": "snapshot-1", "counts": counts}),
                    encoding="utf-8",
                )

            status = runtime_multi.get_snapshot_artifact_status(data_dir, "snapshot-1")

        self.assertEqual(status["audit_export"]["status"], "ready")
        self.assertEqual(status["structured_stats"]["counts"]["included_battles"], 198295)


if __name__ == "__main__":
    unittest.main()
