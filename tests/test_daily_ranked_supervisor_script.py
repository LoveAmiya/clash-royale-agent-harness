import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_daily_ranked_supervisor.ps1"
POWERSHELL = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


class DailyRankedSupervisorScriptTests(unittest.TestCase):
    def run_plan(self, started_at: str, finished_at: str) -> dict:
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-PlanOnly",
                "-RunStartedAt",
                started_at,
                "-RunFinishedAt",
                finished_at,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_short_run_waits_until_two_hours_from_actual_start(self):
        plan = self.run_plan("2026-08-10T10:00:00+08:00", "2026-08-10T11:30:00+08:00")

        self.assertEqual(plan["next_run_at"], "2026-08-10T12:00:00.0000000+08:00")
        self.assertEqual(plan["delay_seconds"], 1800)
        self.assertFalse(plan["catch_up"])

    def test_overdue_run_restarts_immediately_and_reanchors_from_that_start(self):
        plan = self.run_plan("2026-08-10T10:00:00+08:00", "2026-08-10T12:10:00+08:00")

        self.assertEqual(plan["next_run_at"], "2026-08-10T12:10:00.0000000+08:00")
        self.assertEqual(plan["delay_seconds"], 0)
        self.assertTrue(plan["catch_up"])

    def test_multiple_missed_intervals_collapse_to_one_immediate_run(self):
        plan = self.run_plan("2026-08-10T10:00:00+08:00", "2026-08-10T16:30:00+08:00")

        self.assertEqual(plan["next_run_at"], "2026-08-10T16:30:00.0000000+08:00")
        self.assertEqual(plan["delay_seconds"], 0)
        self.assertTrue(plan["catch_up"])

    def test_supervisor_is_isolated_to_core_lane_and_token_zero(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("run_daily_ranked_schedule.ps1", source)
        self.assertIn('"daily_ranked"', source)
        self.assertIn("-TokenIndex 0", source)
        self.assertNotIn("weekly_expanded", source)
        self.assertNotIn("-TokenIndex 1", source)


if __name__ == "__main__":
    unittest.main()
