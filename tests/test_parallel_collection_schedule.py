import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_parallel_collection_tasks.ps1"


class ParallelCollectionScheduleTests(unittest.TestCase):
    def setUp(self):
        self.script = INSTALLER.read_text(encoding="utf-8")

    def test_core_uses_single_supervisor_with_recovery_triggers(self):
        self.assertIn("run_daily_ranked_supervisor.ps1", self.script)
        self.assertIn("$coreRecoveryInterval = New-TimeSpan -Minutes 15", self.script)
        self.assertIn("-MultipleInstances IgnoreNew", self.script)
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", self.script)

    def test_expansion_is_continuous_without_overlapping_instances(self):
        self.assertIn("New-TimeSpan -Minutes 15", self.script)
        self.assertIn("-Mode weekly_expanded -TokenIndex 1", self.script)
        self.assertIn("-MultipleInstances IgnoreNew", self.script)
        self.assertIn("New-TimeSpan -Hours 10", self.script)


if __name__ == "__main__":
    unittest.main()
