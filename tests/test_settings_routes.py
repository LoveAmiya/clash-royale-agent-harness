import asyncio
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clashroyale_agent.api.settings_routes import register_live_sample_settings_routes


async def _noop_refresh():
    await asyncio.sleep(0)


class LiveSampleSettingsRouteRegistrationTests(unittest.TestCase):
    def test_register_live_sample_settings_routes_preserves_get_contract(self):
        app = FastAPI()

        register_live_sample_settings_routes(
            app,
            get_settings_payload=lambda: {"target_battles": 200000, "can_update_target": False},
            configure_target=lambda target: {"target_battles": target},
            refresh_live_snapshot_once=_noop_refresh,
            live_sample_settings_admin_enabled=False,
            admin_api_key=None,
            authorize_admin=lambda _expected, _actual: False,
        )

        client = TestClient(app)
        try:
            response = client.get("/settings/live-sample")
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"target_battles": 200000, "can_update_target": False})

    def test_register_live_sample_settings_routes_rejects_disabled_updates(self):
        app = FastAPI()

        register_live_sample_settings_routes(
            app,
            get_settings_payload=lambda: {"target_battles": 200000},
            configure_target=lambda target: {"target_battles": target},
            refresh_live_snapshot_once=_noop_refresh,
            live_sample_settings_admin_enabled=False,
            admin_api_key="secret",
            authorize_admin=lambda _expected, _actual: True,
        )

        client = TestClient(app)
        try:
            response = client.put("/settings/live-sample", json={"target_battles": 1000})
        finally:
            client.close()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "live sample target updates are restricted to administrators",
        )

    def test_register_live_sample_settings_routes_authorizes_and_schedules_refresh(self):
        app = FastAPI()
        configured_targets = []

        def configure_target(target):
            configured_targets.append(target)
            return {"target_battles": target}

        register_live_sample_settings_routes(
            app,
            get_settings_payload=lambda: {"target_battles": 200000},
            configure_target=configure_target,
            refresh_live_snapshot_once=_noop_refresh,
            live_sample_settings_admin_enabled=True,
            admin_api_key="secret",
            authorize_admin=lambda expected, actual: expected == "secret" and actual == "secret",
        )

        client = TestClient(app)
        try:
            response = client.put(
                "/settings/live-sample",
                json={"target_battles": 1000},
                headers={"X-Admin-Key": "secret"},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"target_battles": 1000})
        self.assertEqual(configured_targets, [1000])

    def test_register_live_sample_settings_routes_rejects_missing_admin_credentials(self):
        app = FastAPI()

        register_live_sample_settings_routes(
            app,
            get_settings_payload=lambda: {"target_battles": 200000},
            configure_target=lambda target: {"target_battles": target},
            refresh_live_snapshot_once=_noop_refresh,
            live_sample_settings_admin_enabled=True,
            admin_api_key="secret",
            authorize_admin=lambda _expected, _actual: False,
        )

        client = TestClient(app)
        try:
            response = client.put("/settings/live-sample", json={"target_battles": 1000})
        finally:
            client.close()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "administrator credentials required")


if __name__ == "__main__":
    unittest.main()
