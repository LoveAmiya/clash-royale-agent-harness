from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EvaluationEntrypointTests(unittest.TestCase):
    def test_run_eval_imports_packaged_parser_from_repository_root(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "evaluation.run_eval", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())


if __name__ == "__main__":
    unittest.main()
