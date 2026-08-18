import importlib
import os
import unittest
from unittest.mock import patch

from support import install_test_stubs


install_test_stubs()


class CollectorPerformanceConfigTests(unittest.TestCase):
    def test_default_high_volume_request_rate_is_conservative_but_not_single_slot(self):
        with patch.dict(os.environ, {}, clear=True):
            import app_config

        reloaded = importlib.reload(app_config)

        self.assertEqual(reloaded.SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND, 2.0)
        self.assertEqual(reloaded.SUPERCELL_FETCH_CONCURRENCY, 2)

    def test_fetch_concurrency_remains_bounded_for_memory_and_api_safety(self):
        with patch.dict(os.environ, {"SUPERCELL_FETCH_CONCURRENCY": "99"}, clear=True):
            import app_config

            reloaded = importlib.reload(app_config)

        self.assertEqual(reloaded.SUPERCELL_FETCH_CONCURRENCY, 4)

    def test_high_volume_request_rate_remains_bounded_for_api_safety(self):
        with patch.dict(os.environ, {"SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND": "99"}, clear=True):
            import app_config

            reloaded = importlib.reload(app_config)

        self.assertEqual(reloaded.SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND, 4.0)


if __name__ == "__main__":
    unittest.main()
