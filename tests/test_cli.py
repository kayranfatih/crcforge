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

    def test_calc_complement_checksum(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["calc", "internet-checksum", "0x450000730000400040110000C0A80001C0A800C7"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Checksum Calculation", buffer.getvalue())
        self.assertIn("0xB861", buffer.getvalue())

    def test_find_complement_checksum(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["find", "0x1E", "0x0300300002337A"])
        self.assertEqual(exit_code, 0)
        self.assertIn("twos-complement-8", buffer.getvalue())

    def test_brute_finds_little_endian_word_checksum(self) -> None:
        # Payload stored as little-endian 16-bit words: swap16 recovers the internet checksum.
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["brute", "0xB861", "0x004573000000004011400000A8C00100A8C0C700", "--width", "16"])
        self.assertEqual(exit_code, 0)
        self.assertIn("[input:swap16][crc:as-given] ones-complement-16", buffer.getvalue())

    def test_banner_command(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = main(["banner"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Fatih Kayran", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
