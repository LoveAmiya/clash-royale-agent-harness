import unittest

import alert_receiver
import app_config
from clashroyale_agent.ops import alert_receiver as packaged_alert_receiver
from clashroyale_agent.ops import app_config as packaged_app_config


class OpsPackageMigrationTests(unittest.TestCase):
    def test_app_config_wrapper_aliases_packaged_module(self):
        self.assertIs(app_config, packaged_app_config)
        self.assertIs(app_config.DATA_DIR, packaged_app_config.DATA_DIR)
        self.assertEqual(app_config.OPENAI_MODEL, packaged_app_config.OPENAI_MODEL)

    def test_alert_receiver_wrapper_aliases_packaged_module(self):
        self.assertIs(alert_receiver, packaged_alert_receiver)
        self.assertIs(alert_receiver.app, packaged_alert_receiver.app)
        self.assertIs(alert_receiver.persist_alert, packaged_alert_receiver.persist_alert)
        self.assertIs(alert_receiver.normalize_alert_payload, packaged_alert_receiver.normalize_alert_payload)


if __name__ == "__main__":
    unittest.main()
