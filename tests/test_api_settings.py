import types
import unittest

import app_config  # noqa: F401 - initializes the src package path for root runs.
from clashroyale_agent.api.settings import (
    FixedLiveSampleTargetError,
    build_fixed_live_sample_settings,
    fixed_live_sample_target,
    reject_live_sample_target_update,
)


class ApiSettingsTests(unittest.TestCase):
    def test_fixed_live_sample_target_returns_the_configured_weekly_target(self):
        self.assertEqual(fixed_live_sample_target(200000), 200000)

    def test_build_fixed_live_sample_settings_preserves_runtime_status_fields(self):
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(live_refresh_status="cooldown", live_cooldown_until=456.75)
        )

        payload = build_fixed_live_sample_settings(
            app,
            fixed_target_battles=200000,
            can_update_target=False,
        )

        self.assertEqual(payload["target_battles"], 200000)
        self.assertEqual(payload["min_target_battles"], 200000)
        self.assertEqual(payload["max_target_battles"], 200000)
        self.assertEqual(payload["refresh_status"], "cooldown")
        self.assertEqual(payload["cooldown_until"], 456.75)
        self.assertFalse(payload["can_update_target"])

    def test_reject_live_sample_target_update_raises_fixed_target_error(self):
        with self.assertRaises(FixedLiveSampleTargetError) as error:
            reject_live_sample_target_update(1000, fixed_target_battles=200000)

        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("200000", str(error.exception))


if __name__ == "__main__":
    unittest.main()
