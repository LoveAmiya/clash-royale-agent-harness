import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryToolingConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_project_metadata_is_explicit(self):
        project = self.config["project"]
        self.assertEqual(project["name"], "clashroyale-agent-harness")
        self.assertEqual(project["readme"], "README.md")
        self.assertEqual(project["requires-python"], ">=3.11")

    def test_pytest_layers_are_declared_without_enabling_live_tests(self):
        pytest_config = self.config["tool"]["pytest"]["ini_options"]
        self.assertEqual(pytest_config["testpaths"], ["tests"])
        markers = "\n".join(pytest_config["markers"])
        for marker in ("unit", "integration", "windows", "collector", "rag", "live_api"):
            self.assertIn(f"{marker}:", markers)
        self.assertIn("-m 'not live_api'", pytest_config["addopts"])

    def test_optional_mypy_scope_stays_inside_package_boundary(self):
        mypy_config = self.config["tool"]["mypy"]
        self.assertEqual(mypy_config["files"], ["src/clashroyale_agent"])
        self.assertTrue(mypy_config["ignore_missing_imports"])

    def test_dev_requirements_only_include_local_quality_tools(self):
        dev_requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        for expected_tool in ("ruff", "pytest", "mypy"):
            self.assertIn(expected_tool, dev_requirements)

        forbidden_live_dependencies = (
            "openai",
            "ollama",
            "supercell",
            "qdrant",
            "redis",
            "fastapi",
            "uvicorn",
        )
        requirement_lines = [
            line.strip().lower()
            for line in dev_requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        normalized = "\n".join(requirement_lines)
        for dependency in forbidden_live_dependencies:
            self.assertNotIn(dependency, normalized)


if __name__ == "__main__":
    unittest.main()
