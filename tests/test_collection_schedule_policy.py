import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CollectionSchedulePolicyTests(unittest.TestCase):
    def test_parallel_installer_defines_both_isolated_collection_lanes(self):
        schedule = (ROOT / "scripts" / "install_parallel_collection_tasks.ps1").read_text(encoding="utf-8")

        self.assertIn("run_daily_ranked_supervisor.ps1", schedule)
        self.assertIn("-Mode weekly_expanded -TokenIndex 1", schedule)
        self.assertIn("ClashRoyale-Daily-Ranked-Every-2h", schedule)
        self.assertIn("ClashRoyale-Expanded-Continuous", schedule)
        self.assertIn("-MultipleInstances IgnoreNew", schedule)

    def test_core_supervisor_uses_a_two_hour_start_cadence(self):
        supervisor = (ROOT / "scripts" / "run_daily_ranked_supervisor.ps1").read_text(encoding="utf-8")

        self.assertIn("[int]$IntervalMinutes = 120", supervisor)
        self.assertIn('-Mode "daily_ranked" -TokenIndex 0', supervisor)
        self.assertIn("$catchUp = $FinishedAt -ge $scheduledAt", supervisor)

    def test_collection_handoff_and_prompt_define_dual_lane_production(self):
        handoff = (ROOT / "docs" / "SNAPSHOT_COLLECTION_HANDOFF.md").read_text(encoding="utf-8")
        prompt = (ROOT / "docs" / "SNAPSHOT_COLLECTION_PROMPT.md").read_text(encoding="utf-8")

        self.assertIn("核心通道 `daily_ranked`", handoff)
        self.assertIn("扩展通道 `weekly_expanded`", handoff)
        self.assertIn("accepted_publication_failed", handoff)
        self.assertIn("当前生产有两个独立通道", prompt)
        self.assertIn("不建立 Codex 定时监听", prompt)
        self.assertNotIn("不要选择 daily_ranked", prompt)

    def test_git_and_docker_exclude_private_runtime_data(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("/data/**", gitignore)
        self.assertIn("!/data/card_aliases.zh-CN.json", gitignore)
        self.assertIn("data/**", dockerignore)
        self.assertIn("!data/card_aliases.zh-CN.json", dockerignore)


if __name__ == "__main__":
    unittest.main()
