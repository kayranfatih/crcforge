from __future__ import annotations

import random
import unittest

import _support  # noqa: F401
from crcforge.catalog import get_algorithm, get_algorithms
from crcforge.checksums import SumChecksum, XorChecksum
from crcforge.forge import ForgeError, forge


class ForgeCrcTest(unittest.TestCase):
    def test_every_catalog_crc_can_be_forged_at_any_position(self) -> None:
        rng = random.Random(1)
        for algorithm in get_algorithms(family="crc"):
            data = rng.randbytes(rng.randrange(0, 40))
            target = rng.randrange(1 << algorithm.width)
            for offset in sorted({0, len(data) // 2, len(data)}):
                with self.subTest(algorithm=algorithm.name, offset=offset):
                    result = forge(data, algorithm, target, offset=offset)
                    self.assertEqual(algorithm.compute(result.data), target)
                    self.assertEqual(len(result.patch), (algorithm.width + 7) // 8)
                    self.assertEqual(result.data[:offset], data[:offset])
                    self.assertEqual(result.data[offset + len(result.patch) :], data[offset:])

    def test_overwrite_keeps_the_length(self) -> None:
        algorithm = get_algorithm("crc-32")
        data = bytes(range(64))
        result = forge(data, algorithm, 0xCAFEBABE, offset=10, overwrite=True)
        self.assertEqual(len(result.data), len(data))
        self.assertEqual(result.data[:10], data[:10])
        self.assertEqual(result.data[14:], data[14:])
        self.assertEqual(algorithm.compute(result.data), 0xCAFEBABE)

    def test_overwrite_defaults_to_the_last_bytes(self) -> None:
        algorithm = get_algorithm("crc-16-modbus")
        result = forge(b"0123456789", algorithm, 0x0000, overwrite=True, patch_size=2)
        self.assertEqual(result.data[:8], b"01234567")
        self.assertEqual(algorithm.compute(result.data), 0)

    def test_hello_world_example(self) -> None:
        result = forge(b"hello world", get_algorithm("crc-32"), 0xDEADBEEF)
        self.assertEqual(result.patch.hex(), "46c96e0b")

    def test_larger_patch_is_allowed(self) -> None:
        algorithm = get_algorithm("crc-16-xmodem")
        result = forge(b"abc", algorithm, 0x1234, patch_size=4)
        self.assertEqual(len(result.patch), 4)
        self.assertEqual(algorithm.compute(result.data), 0x1234)

    def test_errors(self) -> None:
        algorithm = get_algorithm("crc-32")
        with self.assertRaises(ForgeError):
            forge(b"abc", algorithm, 1 << 32)
        with self.assertRaises(ForgeError):
            forge(b"abc", algorithm, 0, offset=10)
        with self.assertRaises(ForgeError):
            forge(b"abc", algorithm, 0, offset=1, overwrite=True)
        with self.assertRaises(ForgeError):
            forge(b"abc", algorithm, 0, patch_size=2)
        with self.assertRaises(ForgeError):
            forge(b"abc", get_algorithm("adler-32"), 0)


class ForgeChecksumTest(unittest.TestCase):
    def test_sum_layouts(self) -> None:
        rng = random.Random(2)
        layouts = [
            SumChecksum("s", width, word_size, endian, carry, complement)
            for width in (8, 16, 32)
            for word_size in (1, 2, 4)
            if word_size * 8 <= width
            for endian in ("big", "little")
            for carry in ("discard", "end-around")
            for complement in ("none", "ones", "twos")
        ]
        for layout in layouts:
            for _ in range(4):
                data = rng.randbytes(rng.randrange(0, 20))
                target = rng.randrange(1 << layout.width)
                if layout.carry == "end-around" and layout.finalize(0) == target:
                    continue  # one's complement sums cannot be zero for non-zero data
                if layout.word_size * 8 < layout.width:
                    # Narrow words can only add a little per word: aim near the current checksum.
                    current = layout.accumulate(layout.raw_sum(data))
                    target = layout.finalize(layout.accumulate(current + rng.randrange(0, 2000)))
                    if layout.carry == "end-around" and layout.finalize(0) == target:
                        continue
                with self.subTest(layout=layout, data=data, target=target):
                    result = forge(data, layout, target)
                    self.assertEqual(layout.compute(result.data), target)
                    self.assertEqual(result.data[: len(data)], data)

    def test_sum_insert_must_be_word_aligned(self) -> None:
        with self.assertRaises(ForgeError):
            forge(b"\x01\x02\x03\x04", get_algorithm("internet-checksum"), 0x1234, offset=1)
        result = forge(b"\x01\x02\x03\x04", get_algorithm("internet-checksum"), 0x1234, offset=2)
        self.assertEqual(get_algorithm("internet-checksum").compute(result.data), 0x1234)

    def test_ones_complement_zero_target_is_explained(self) -> None:
        with self.assertRaises(ForgeError) as context:
            forge(b"\x45\x00", get_algorithm("internet-checksum"), 0xFFFF)
        self.assertIn("never" if "never" in str(context.exception) else "zero", str(context.exception))

    def test_xor(self) -> None:
        for width in (8, 16, 32):
            layout = XorChecksum("x", width)
            result = forge(b"\x01\x02\x03", layout, 0xA5 if width == 8 else 0xA5A5)
            self.assertEqual(layout.compute(result.data), 0xA5 if width == 8 else 0xA5A5)

    def test_unreachable_sum_target_is_explained(self) -> None:
        with self.assertRaises(ForgeError) as context:
            forge(b"", get_algorithm("sum-32"), 0xFFFFFFF0)
        self.assertIn("patch", str(context.exception))

    def test_sum_overwrite(self) -> None:
        layout = get_algorithm("sum-16")
        data = bytes(range(10))
        result = forge(data, layout, 0x0100, offset=4, overwrite=True)
        self.assertEqual(layout.compute(result.data), 0x0100)
        self.assertEqual(result.data[:4], data[:4])
        self.assertEqual(len(result.data), len(data))
        # A byte-wise 16-bit sum may need many patch bytes; overwriting past the end is reported.
        with self.assertRaises(ForgeError):
            forge(data, layout, 0x1234, offset=4, overwrite=True)


if __name__ == "__main__":
    unittest.main()
