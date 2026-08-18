import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.api.messages import get_user_text
from clashroyale_agent.api.payloads import build_full_loadout_payload
from clashroyale_agent.api.schemas import (
    CardCompareRequest,
    DeckMatchupRequest,
    DeckProfileRequest,
    EntityCompareRequest,
    FeedbackRequest,
    FullLoadoutCardRequest,
    FullLoadoutRequest,
    LiveSampleSettingsRequest,
    ProcessRequest,
)
from rolling_corpus import DEFAULT_DATASET_SCOPE
from structured_query import StructuredQueryError


class RuntimeApiSchemaTests(unittest.TestCase):
    def test_process_request_defaults_match_runtime_contract(self):
        request = ProcessRequest(input=[{"role": "user", "content": "hi"}])
        self.assertEqual(request.dataset_scope, DEFAULT_DATASET_SCOPE)
        self.assertEqual(request.deck_mode, "base8")
        self.assertEqual(request.entity_mode, "base8")

    def test_process_request_message_helper_extracts_first_user_text_block(self):
        request = ProcessRequest(
            input=[
                {"role": "assistant", "content": [{"type": "text", "text": "ignore"}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "text": "ignore"},
                        {"type": "text", "text": "  分析环境  "},
                    ],
                },
            ]
        )

        self.assertEqual(get_user_text(request), "分析环境")

    def test_process_request_message_helper_returns_empty_when_text_missing(self):
        request = ProcessRequest(input=[{"role": "assistant", "content": []}])

        self.assertEqual(get_user_text(request), "")

    def test_structured_route_request_defaults_match_runtime_contract(self):
        self.assertEqual(CardCompareRequest(card_ids=["1"]).dataset_scope, DEFAULT_DATASET_SCOPE)
        self.assertEqual(EntityCompareRequest(entity_ids=["1"]).dataset_scope, DEFAULT_DATASET_SCOPE)
        self.assertEqual(DeckProfileRequest(cards=["1"]).deck_mode, "base8")
        self.assertEqual(DeckMatchupRequest(deck_a=["1"], deck_b=["2"]).deck_mode, "base8")

    def test_full_loadout_and_admin_feedback_payload_shapes(self):
        card = FullLoadoutCardRequest(card_id="26000000", elite=False)
        loadout = FullLoadoutRequest(tower_id="159000000", cards=[card])
        settings = LiveSampleSettingsRequest(target_battles=1000)
        feedback = FeedbackRequest(request_id="req-1", rating="positive")
        self.assertEqual(loadout.cards[0].evolution_level, 0)
        self.assertEqual(settings.target_battles, 1000)
        self.assertIsNone(feedback.correction)

    def test_full_loadout_payload_adapter_preserves_repository_contract(self):
        loadout = FullLoadoutRequest(
            tower_id="159000000",
            cards=[
                FullLoadoutCardRequest(card_id="26000000", evolution_level=1, elite=False),
                FullLoadoutCardRequest(card_id="26000001", evolution_level=0, elite=True),
            ],
        )

        self.assertEqual(
            build_full_loadout_payload(loadout),
            {
                "schema_version": 1,
                "tower": {"id": "159000000"},
                "cards": [
                    {"id": "26000000", "evolution_level": 1, "elite": False},
                    {"id": "26000001", "evolution_level": 0, "elite": True},
                ],
            },
        )

    def test_full_loadout_payload_adapter_keeps_missing_loadout_error(self):
        with self.assertRaises(StructuredQueryError) as raised:
            build_full_loadout_payload(None)

        self.assertEqual(raised.exception.code, "INVALID_FULL_LOADOUT")


if __name__ == "__main__":
    unittest.main()
