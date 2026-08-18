from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "install_rolling_schedule.ps1"


class RollingScheduleInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_installer_uses_hidden_non_interactive_s4u_task(self):
        self.assertIn("New-ScheduledTaskPrincipal", self.script)
        self.assertIn("-LogonType S4U", self.script)
        self.assertIn("-RunLevel Limited", self.script)
        self.assertIn("-Hidden", self.script)
        self.assertIn("-NonInteractive", self.script)
        self.assertIn("-WindowStyle Hidden", self.script)
        self.assertIn("-Principal $principal", self.script)

    def test_installer_uses_absolute_windows_powershell_path(self):
        self.assertIn("$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", self.script)
        self.assertNotIn('New-ScheduledTaskAction -Execute "powershell.exe"', self.script)


if __name__ == "__main__":
    unittest.main()
