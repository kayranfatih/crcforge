from __future__ import annotations

import unittest

import _support  # noqa: F401
from crcforge.byteorder import BYTE_ORDER_MODES, describe_byte_order, distinct_views, transform_byte_order


class ByteOrderTestCase(unittest.TestCase):
    def test_swap16(self) -> None:
        self.assertEqual(transform_byte_order(bytes.fromhex("11223344"), "swap16"), bytes.fromhex("22114433"))

    def test_swap32(self) -> None:
        self.assertEqual(transform_byte_order(bytes.fromhex("11223344"), "swap32"), bytes.fromhex("44332211"))

    def test_swap64_and_reverse(self) -> None:
        data = bytes.fromhex("1122334455667788")
        self.assertEqual(transform_byte_order(data, "swap64"), bytes.fromhex("8877665544332211"))
        self.assertEqual(transform_byte_order(data, "reverse"), bytes.fromhex("8877665544332211"))
        self.assertEqual(transform_byte_order(data, " Native "), data)

    def test_invalid_alignment_raises(self) -> None:
        with self.assertRaises(ValueError):
            transform_byte_order(b"abc", "swap16")

    def test_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            transform_byte_order(b"ab", "swap128")
        with self.assertRaises(ValueError):
            describe_byte_order("swap128")

    def test_every_mode_is_described(self) -> None:
        for mode in BYTE_ORDER_MODES:
            self.assertTrue(describe_byte_order(mode))


class DistinctViewsTest(unittest.TestCase):
    def test_identical_views_are_skipped(self) -> None:
        views = distinct_views([bytes.fromhex("11223344")], BYTE_ORDER_MODES)
        by_mode = {mode: (data, reason) for mode, data, reason in views}
        self.assertIsNotNone(by_mode["reverse"][0])
        self.assertEqual(by_mode["swap32"], (None, "same bytes as reverse"))
        self.assertIn("multiple of 8", str(by_mode["swap64"][1]))

    def test_views_apply_to_every_dataset(self) -> None:
        views = distinct_views([b"ab", b"cdef"], ["native", "swap16"])
        self.assertEqual(views[1], ("swap16", [b"ba", b"dcfe"], None))
        self.assertEqual(distinct_views([b"ab", b"cde"], ["swap16"])[0][1], None)


if __name__ == "__main__":
    unittest.main()
