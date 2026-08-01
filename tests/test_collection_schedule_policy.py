import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CollectionSchedulePolicyTests(unittest.TestCase):
    def test_daily_schedule_always_runs_expanded_collection(self):
        schedule = (ROOT / "scripts" / "run_rolling_schedule.ps1").read_text(encoding="utf-8")

        self.assertIn('$mode = "weekly_expanded"', schedule)
        self.assertNotIn("DayOfWeek", schedule)
        self.assertNotIn('"daily_ranked"', schedule)

    def test_manual_collection_defaults_to_expanded_mode(self):
        launcher = (ROOT / "run_rolling_collection.ps1").read_text(encoding="utf-8")

        self.assertIn('[string]$Mode = "weekly_expanded"', launcher)

    def test_collection_handoff_and_prompt_define_daily_expanded_production(self):
        handoff = (ROOT / "docs" / "SNAPSHOT_COLLECTION_HANDOFF.md").read_text(encoding="utf-8")
        prompt = (ROOT / "docs" / "SNAPSHOT_COLLECTION_PROMPT.md").read_text(encoding="utf-8")

        self.assertIn("生产采集每天都运行 `weekly_expanded`", handoff)
        self.assertIn("生产采集每天都固定使用 weekly_expanded", prompt)
        self.assertIn("不要选择 daily_ranked", prompt)

    def test_git_and_docker_exclude_private_runtime_data(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("/data/**", gitignore)
        self.assertIn("!/data/card_aliases.zh-CN.json", gitignore)
        self.assertIn("data/**", dockerignore)
        self.assertIn("!data/card_aliases.zh-CN.json", dockerignore)


if __name__ == "__main__":
    unittest.main()
