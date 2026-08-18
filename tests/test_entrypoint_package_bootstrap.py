from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EntrypointPackageBootstrapTests(unittest.TestCase):
    def test_root_modules_bootstrap_src_before_package_imports(self) -> None:
        offenders: list[str] = []
        for path in sorted(ROOT.glob("*.py")):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            package_import_lines = [
                index
                for index, line in enumerate(lines, start=1)
                if "clashroyale_agent" in line
                and line.lstrip().startswith(("from ", "import "))
            ]
            if not package_import_lines:
                continue

            first_package_import = min(package_import_lines)
            bootstrap_lines = [
                index
                for index, line in enumerate(lines, start=1)
                if "import app_config" in line
                or "from app_config import" in line
                or "_SRC_PATH" in line
                or "sys.path.insert" in line
            ]
            if not bootstrap_lines or min(bootstrap_lines) >= first_package_import:
                offenders.append(f"{path.name}: first package import at line {first_package_import}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
