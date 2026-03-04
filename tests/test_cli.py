from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crcforge.cli import main


class CliTestCase(unittest.TestCase):
    def test_main_without_args_prints_help(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main([])
        self.assertEqual(exit_code, 0)
        self.assertIn("usage:", buffer.getvalue().lower())

    def test_transform_text_output(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["transform", "0b0100000101000010", "--output-format", "text"])
        self.assertEqual(exit_code, 0)
        self.assertIn("AB", buffer.getvalue())

    def test_banner_command(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["banner"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Fatih Kayran", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
