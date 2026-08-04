import unittest

from support import install_test_stubs

install_test_stubs()

from retrieval_postprocess import (
    build_context_and_refs,
    compress_doc,
    select_diverse_results,
    strip_generated_reference_section,
)
from snapshot_store import DAILY_TARGET_BATTLES, build_snapshot_rag_documents


class RetrievalEvidencePreservationTests(unittest.TestCase):
    def test_diverse_selection_keeps_multiple_evidence_types_before_filling(self):
        results = [
            {
                "doc": {"doc_id": f"deck-{index}", "source_type": "deck_profile"},
                "rerank_score": 1.0 - index / 100,
            }
            for index in range(5)
        ] + [
            {
                "doc": {"doc_id": "archetype-1", "source_type": "archetype"},
                "rerank_score": 0.7,
            },
            {
                "doc": {"doc_id": "pair-1", "source_type": "card_pair"},
                "rerank_score": 0.6,
            },
        ]

        selected = select_diverse_results(results, top_n=5, per_source_limit=3)

        self.assertEqual(len(selected), 5)
        self.assertEqual(
            {item["doc"]["source_type"] for item in selected},
            {"deck_profile", "archetype", "card_pair"},
        )
        self.assertLessEqual(
            sum(item["doc"]["source_type"] == "deck_profile" for item in selected),
            3,
        )

    def test_verified_retrieval_references_continue_after_structured_sources(self):
        result = {
            "doc": {
                "doc_id": "snapshot-1:card:Skeletons",
                "source_type": "card",
                "text": "Skeletons evidence",
                "metadata": {"source": "Supercell API live sample"},
            },
            "final_score": 1.0,
        }

        context, refs = build_context_and_refs([result], start_index=3)

        self.assertTrue(context.startswith("[3] source_type: card"))
        self.assertTrue(refs.startswith("[3] card | snapshot-1:card:Skeletons"))

    def test_model_generated_reference_section_is_removed_before_verified_sources(self):
        answer = "可靠结论。\n\n参考来源：\n[1] model-written source"

        self.assertEqual(strip_generated_reference_section(answer), "可靠结论。")

    def test_card_compression_preserves_snapshot_usage_and_win_rates(self):
        snapshot = {
            "snapshot_id": "official-regression",
            "fetched_at": "2026-07-26T17:43:32+00:00",
            "sample_battles": DAILY_TARGET_BATTLES,
            "target_battles": DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "cards_meta": [
                {
                    "rank": 1,
                    "card_name": "Skeletons",
                    "usage_rate": 33.1,
                    "win_rate": 64.8,
                    "clean_win_rate": 64.8,
                    "appearance_count": 6610,
                }
            ],
            "top_decks": [],
            "deck_matchups": [],
            "raw_battles": [{"battle_id": "battle-1"}],
            "collection_metrics": {},
        }

        card_doc = next(
            document
            for document in build_snapshot_rag_documents(snapshot)
            if document["source_type"] == "card"
        )
        compressed = compress_doc(card_doc)

        self.assertIn("Skeletons", compressed)
        self.assertIn("33.1%", compressed)
        self.assertIn("64.8%", compressed)
        self.assertIn("6610", compressed)
        self.assertIn("6610 appearances", compressed)
        self.assertNotIn("None", compressed)

    def test_deck_compression_preserves_snapshot_sample_size_and_win_rate(self):
        snapshot = {
            "snapshot_id": "official-regression",
            "fetched_at": "2026-07-26T17:43:32+00:00",
            "sample_battles": DAILY_TARGET_BATTLES,
            "target_battles": DAILY_TARGET_BATTLES,
            "shortfall_battles": 0,
            "cards_meta": [],
            "top_decks": [
                {
                    "rank": 1,
                    "deck_name": "Electro Giant / Lightning / Tornado",
                    "cards": ["Electro Giant", "Lightning", "Tornado"],
                    "battles": 80,
                    "sample_win_rate": 52.5,
                }
            ],
            "deck_matchups": [],
            "raw_battles": [{"battle_id": "battle-1"}],
            "collection_metrics": {},
        }

        deck_doc = next(
            document
            for document in build_snapshot_rag_documents(snapshot)
            if document["source_type"] == "deck"
        )
        compressed = compress_doc(deck_doc)

        self.assertIn("Electro Giant", compressed)
        self.assertIn("80", compressed)
        self.assertIn("80 games", compressed)
        self.assertIn("52.5%", compressed)
        self.assertNotIn("None", compressed)

    def test_archetype_compression_uses_groundable_side_record_units(self):
        compressed = compress_doc(
            {
                "doc_id": "scope:archetype:野猪速转",
                "source_type": "archetype",
                "text": "Archetype evidence for 野猪速转: 136798 side records, usage 7.293225%, win rate 49.534264%.",
                "metadata": {
                    "archetype": "野猪速转",
                    "games": 136798,
                    "usage_rate": 7.293225,
                    "win_rate": 49.534264,
                },
            }
        )

        self.assertIn("野猪速转", compressed)
        self.assertIn("136798 次", compressed)
        self.assertIn("7.293225%", compressed)
        self.assertIn("49.534264%", compressed)


if __name__ == "__main__":
    unittest.main()
