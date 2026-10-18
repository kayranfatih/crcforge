from __future__ import annotations

import unittest

import _support  # noqa: F401
import crcforge
from crcforge.frames import FrameLayout, candidate_layouts

MODBUS_FRAMES = [
    bytes.fromhex(frame) for frame in ("010300000001840A", "010300000002C40B", "01030000000AC5CD", "0106000100FF984A")
]


class FrameLayoutTest(unittest.TestCase):
    def test_split_end_little_endian(self) -> None:
        sample = FrameLayout(16, "end", "little").split(MODBUS_FRAMES[0])
        self.assertEqual(sample.data, bytes.fromhex("010300000001"))
        self.assertEqual(sample.checksum, 0x0A84)

    def test_split_start_big_endian_with_skip(self) -> None:
        sample = FrameLayout(16, "start", "big", skip=1).split(bytes.fromhex("BEEF7E010203"))
        self.assertEqual((sample.data, sample.checksum), (b"\x01\x02\x03", 0xBEEF))

    def test_non_byte_width_must_fit(self) -> None:
        self.assertEqual(FrameLayout(12, "end", "big").split(b"\x01\x0f\xff").checksum, 0xFFF)
        with self.assertRaises(ValueError):
            FrameLayout(12, "end", "big").split(b"\x01\xff\xff")

    def test_errors(self) -> None:
        with self.assertRaises(ValueError):
            FrameLayout(32).split(b"\x01\x02")
        with self.assertRaises(ValueError):
            FrameLayout(8, "middle").split(b"\x01\x02")

    def test_describe(self) -> None:
        self.assertEqual(FrameLayout(16, "end", "little").describe(), "16-bit checksum at the end, little-endian")
        self.assertEqual(
            FrameLayout(8, "start", skip=2).describe(), "8-bit checksum at the start, data skips 2 header bytes"
        )

    def test_candidate_layouts_skip_endianness_for_single_bytes(self) -> None:
        layouts = candidate_layouts([8, 16], ["end"], ["big", "little"], [0])
        self.assertEqual(
            [(layout.width, layout.endian) for layout in layouts], [(8, "big"), (16, "big"), (16, "little")]
        )


class PublicApiTest(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(crcforge.__version__, "1.1.0")

    def test_calculate_with_name_or_object(self) -> None:
        self.assertEqual(crcforge.calculate("crc-16-modbus", b"123456789"), 0x4B37)
        self.assertEqual(crcforge.calculate(crcforge.get_algorithm("modbus"), bytearray(b"123456789")), 0x4B37)
        custom = crcforge.CrcAlgorithm("mine", 16, 0x1021, 0xFFFF, 0, False, False)
        self.assertEqual(crcforge.calculate(custom, b"123456789"), 0x29B1)

    def test_find(self) -> None:
        self.assertIn("crc-16-modbus", [a.name for a in crcforge.find(b"123456789", 0x4B37)])
        self.assertEqual(crcforge.find(b"123456789", 0x4B37, family="checksum"), [])

    def test_solve_frames(self) -> None:
        samples = FrameLayout(16, "end", "little").split_all(MODBUS_FRAMES)
        result = crcforge.solve(samples, 16, swap_checksums=False)
        self.assertEqual([m.algorithm.name for m in result.catalog_matches], ["crc-16-modbus"])

    def test_forge(self) -> None:
        result = crcforge.forge(b"hello", "crc-32", 0xDEADBEEF)
        self.assertEqual(crcforge.calculate("crc-32", result.data), 0xDEADBEEF)

    def test_list_and_validate(self) -> None:
        self.assertEqual(len(crcforge.list_algorithms()), len(crcforge.FULL_CATALOG))
        self.assertEqual(crcforge.validate_catalog(), [])
        with self.assertRaises(crcforge.UnknownAlgorithmError):
            crcforge.calculate("nope", b"")

    def test_check_records(self) -> None:
        self.assertTrue(crcforge.check_records(":00000001FF\n").ok)


if __name__ == "__main__":
    unittest.main()
