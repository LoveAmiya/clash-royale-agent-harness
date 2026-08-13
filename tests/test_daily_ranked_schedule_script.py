import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_daily_ranked_schedule.ps1"


class DailyRankedScheduleScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")

    def test_schedule_allows_multiple_same_day_daily_batches(self):
        self.assertNotIn("skipped_same_day_batch_exists", self.script)
        self.assertIn('"{0}-{1:yyyyMMdd-HHmmss}" -f $Mode', self.script)
        self.assertIn("--batch-id", self.script)

    def test_schedule_log_identifies_the_two_hour_cadence(self):
        self.assertIn('"daily_ranked_every_2h"', self.script)
        self.assertNotIn('task = "daily_ranked_every_3h"', self.script)

    def test_schedule_collects_available_ranked_players_without_a_1000_player_gate(self):
        self.assertNotIn("MinimumLeaderboardPlayers", self.script)
        self.assertNotIn("skipped_insufficient_leaderboard", self.script)
        self.assertNotIn("/locations/global/pathoflegend/players", self.script)
        self.assertIn("collect_rolling_corpus.py", self.script)

    def test_schedule_still_runs_daily_ranked_without_weekly_expansion(self):
        self.assertIn("collect_rolling_corpus.py", self.script)
        self.assertIn("daily_ranked", self.script)

    def test_schedule_can_select_an_isolated_token_for_each_lane(self):
        self.assertIn("SUPERCELL_API_TOKENS", self.script)
        self.assertIn("TokenIndex", self.script)
        self.assertIn("weekly_expanded", self.script)
        self.assertIn("Get-SupercellTokens", self.script)
        self.assertIn("if ($tokens.Count -gt 0)", self.script)
        self.assertIn("combined-token-variables", self.script)

    def test_schedule_supports_pushplus_notification_test_mode(self):
        self.assertIn("[switch]$NotifyTest", self.script)
        self.assertIn("https://www.pushplus.plus/send", self.script)
        self.assertIn("COLLECT_NOTIFY_TOKEN", self.script)
        self.assertIn('"Pushplus"', self.script)
        self.assertIn("notify_test", self.script)

    def test_schedule_notification_includes_required_collection_counts(self):
        self.assertIn("unique_battle_facts", self.script)
        self.assertIn("battle_observations", self.script)
        self.assertIn("complete_loadout_rows", self.script)
        self.assertIn("delta_battle_observations", self.script)
        self.assertIn("delta_unique_battle_facts", self.script)
        self.assertIn("collection_status.json", self.script)

    def test_schedule_notification_includes_actionable_failure_reasons(self):
        self.assertIn('validation_failures: ', self.script)
        self.assertIn('error_type: ', self.script)
        self.assertIn('collector_error_type: ', self.script)
        self.assertIn('publication_error_type: ', self.script)

    def test_schedule_does_not_log_notification_or_supercell_secrets(self):
        self.assertNotIn("67a", self.script)
        self.assertNotIn('Fields["token"]', self.script)
        self.assertNotIn('Fields["supercell_token"]', self.script)
        self.assertNotIn('lines.Add("token', self.script)
        self.assertIn("notify_token_variable", self.script)
        self.assertIn("notify_token_scope", self.script)

    def test_schedule_only_pushes_non_test_notifications_for_failures(self):
        self.assertIn("[bool]$SendNotification = $true", self.script)
        self.assertIn('$Fields["notify_status"] = "suppressed_non_failure"', self.script)
        self.assertIn("$sendFinishNotification = $process.ExitCode -ne 0", self.script)
        self.assertIn('Collector skipped: active writer" $false', self.script)
        self.assertIn('Collector dry run ready" $false', self.script)
        self.assertIn('Collector PushPlus notification test" $true', self.script)

    def test_schedule_treats_pending_merge_as_silent_retryable_work(self):
        self.assertIn("$isDeferredMerge = $process.ExitCode -eq 4", self.script)
        self.assertIn('$finishStatus = if ($isDeferredMerge) { "deferred_merge" } else { "finished" }', self.script)
        self.assertIn("$sendFinishNotification = $process.ExitCode -ne 0 -and -not $isDeferredMerge", self.script)

    def test_schedule_redirects_python_and_sqlite_temp_files_to_the_project_drive(self):
        temp_setup = self.script.index('$runtimeTemp = Join-Path $projectRoot')
        preflight = self.script.index('-m supercell_preflight')
        collector = self.script.index('collect_rolling_corpus.py')

        self.assertLess(temp_setup, preflight)
        self.assertLess(temp_setup, collector)
        self.assertIn('$env:TEMP = $runtimeTemp', self.script)
        self.assertIn('$env:TMP = $runtimeTemp', self.script)
        self.assertIn('$env:TMPDIR = $runtimeTemp', self.script)
        self.assertIn('$env:SQLITE_TMPDIR = $runtimeTemp', self.script)

    def test_schedule_repairs_an_accepted_publication_before_api_preflight(self):
        repair = self.script.index('--retry-publication-only')
        preflight = self.script.index('-m supercell_preflight')

        self.assertLess(repair, preflight)
        self.assertIn('"publication_repaired"', self.script)
        self.assertIn('"publication_repair_failed"', self.script)
        self.assertIn('"publication_repair_deferred"', self.script)


if __name__ == "__main__":
    unittest.main()
