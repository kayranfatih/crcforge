"""Checks on the repository itself: entry points, generated docs, release tooling."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest

import _support
import crcforge


def load_script(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, _support.ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EntryPointTest(unittest.TestCase):
    def run_python(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            capture_output=True,
            text=True,
            cwd=_support.ROOT,
            env={**__import__("os").environ, "PYTHONPATH": str(_support.ROOT / "src")},
            check=False,
        )

    def test_python_dash_m(self) -> None:
        result = self.run_python("-m", "crcforge", "calc", "crc-32", "123456789", "-q")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0xCBF43926")

    def test_compatibility_wrapper_script(self) -> None:
        result = self.run_python("crcforge.py", "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(crcforge.__version__, result.stdout)


class GeneratedFilesTest(unittest.TestCase):
    def test_algorithm_docs_are_up_to_date(self) -> None:
        expected = load_script("gen_algorithm_docs").render()
        current = (_support.ROOT / "docs" / "algorithms.md").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(current, expected, "run python scripts/gen_algorithm_docs.py")


class ReleaseTest(unittest.TestCase):
    def test_changelog_has_a_section_for_the_current_version(self) -> None:
        changelog = (_support.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        notes = load_script("release_notes").section(crcforge.__version__, changelog)
        self.assertIn("### Added", notes)
        self.assertNotIn(f"## [{crcforge.__version__}]", notes)

    def test_missing_version_is_an_error(self) -> None:
        with self.assertRaises(SystemExit):
            load_script("release_notes").section("0.0.0", "# Changelog\n")

    def test_version_is_single_sourced(self) -> None:
        pyproject = (_support.ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIsNone(re.search(r'^version\s*=\s*"', pyproject, re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
