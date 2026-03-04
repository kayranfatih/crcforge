from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crcforge.byteorder import transform_byte_order


class ByteOrderTestCase(unittest.TestCase):
    def test_swap16(self) -> None:
        self.assertEqual(transform_byte_order(bytes.fromhex("11223344"), "swap16"), bytes.fromhex("22114433"))

    def test_swap32(self) -> None:
        self.assertEqual(transform_byte_order(bytes.fromhex("11223344"), "swap32"), bytes.fromhex("44332211"))

    def test_invalid_alignment_raises(self) -> None:
        with self.assertRaises(ValueError):
            transform_byte_order(b"abc", "swap16")


if __name__ == "__main__":
    unittest.main()
