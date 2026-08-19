from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeadCodeCleanupTests(unittest.TestCase):
    def test_unreferenced_sse_client_is_removed_and_reported(self):
        self.assertFalse((ROOT / "client.py").exists())
        report = (ROOT / "docs" / "reports" / "dead_code_candidates.md").read_text(encoding="utf-8")
        self.assertIn("Removed 2026-08-19", report)


if __name__ == "__main__":
    unittest.main()
