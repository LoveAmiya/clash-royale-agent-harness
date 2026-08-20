import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_repo.ps1"


class CheckRepoScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")

    def test_script_runs_core_repository_quality_gates(self):
        self.assertIn("git diff --cached --check", self.script)
        self.assertIn("git diff --check", self.script)
        self.assertIn("$IncludeUnstaged", self.script)
        self.assertIn("git ls-files", self.script)
        self.assertIn("git check-ignore", self.script)
        self.assertIn("-m compileall", self.script)
        self.assertIn("-m unittest discover -s tests -t .", self.script)
        self.assertIn("run_tests.ps1", self.script)
        self.assertIn("Summary: checks run:", self.script)
        self.assertIn("Summary: checks skipped:", self.script)
        self.assertIn("Summary: private path scan:", self.script)

    def test_script_scans_private_data_and_secret_like_paths(self):
        self.assertIn(r"^data/(?!card_aliases\.zh-CN\.json$)", self.script)
        self.assertIn("^logs/", self.script)
        self.assertIn("^tmp/", self.script)
        self.assertIn(r"\.sqlite3?", self.script)
        self.assertIn(r"\.jsonl$", self.script)
        self.assertIn("token|secret|api[_-]?key|password", self.script)
        self.assertIn("evaluation/cases.jsonl", self.script)
        self.assertIn("evaluation/fault_scenarios.jsonl", self.script)
        self.assertIn("evaluation/reports/README.md", self.script)

    def test_script_resolves_git_root_before_running_git_checks(self):
        self.assertIn("git rev-parse --show-toplevel", self.script)
        self.assertIn("Unable to resolve Git repository root", self.script)
        self.assertIn("safe.directory", self.script)
        self.assertIn("Push-Location", self.script)
        self.assertIn("Pop-Location", self.script)

    def test_script_avoids_bulk_staging_or_private_env_mutation(self):
        self.assertNotIn("git add .", self.script)
        self.assertNotIn("SUPERCELL_API_TOKEN =", self.script)
        self.assertNotIn("OPENAI_API_KEY =", self.script)


if __name__ == "__main__":
    unittest.main()
