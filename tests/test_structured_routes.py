import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clashroyale_agent.api.structured_routes import register_structured_api_routes


class _Repository:
    def card_catalog(self):
        return {"route": "card_catalog"}

    def card_rankings(self, sort_by):
        return {"route": "card_rankings", "sort_by": sort_by}

    def deck_profile(self, cards):
        return {"route": "deck_profile", "cards": cards}


class StructuredRouteRegistrationTests(unittest.TestCase):
    def test_register_structured_api_routes_delegates_catalog_and_repository_calls(self):
        app = FastAPI()
        repository_calls = []

        def get_repository(dataset_scope):
            repository_calls.append(dataset_scope)
            return _Repository()

        register_structured_api_routes(
            app,
            default_dataset_scope="7d_all",
            get_dataset_catalog=lambda: {"route": "datasets"},
            get_repository=get_repository,
            card_ranking_metrics={"usage_rate", "clean_win_rate", "rating"},
        )

        client = TestClient(app)
        try:
            datasets = client.get("/api/datasets")
            catalog = client.get("/api/cards/catalog?dataset_scope=35d_all")
            rankings = client.get("/api/cards/rankings?sort_by=rating")
            profile = client.post(
                "/api/decks/profile",
                json={"cards": ["A", "B"], "dataset_scope": "7d_top_1000"},
            )
        finally:
            client.close()

        self.assertEqual(datasets.status_code, 200)
        self.assertEqual(datasets.json(), {"route": "datasets"})
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json(), {"route": "card_catalog"})
        self.assertEqual(rankings.status_code, 200)
        self.assertEqual(rankings.json(), {"route": "card_rankings", "sort_by": "rating"})
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json(), {"route": "deck_profile", "cards": ["A", "B"]})
        self.assertEqual(repository_calls, ["35d_all", "7d_all", "7d_top_1000"])


if __name__ == "__main__":
    unittest.main()
