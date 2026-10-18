from __future__ import annotations

import random
import unittest
import zlib

import _support  # noqa: F401
from crcforge.catalog import CHECK_INPUT, get_algorithm
from crcforge.checksums import (
    CHECKSUM_CATALOG,
    Adler32Checksum,
    BsdChecksum,
    FletcherChecksum,
    SumChecksum,
    SysvChecksum,
    XorChecksum,
)


class ReferenceVectorTest(unittest.TestCase):
    def test_catalog_check_values(self) -> None:
        for checksum in CHECKSUM_CATALOG:
            with self.subTest(checksum.name):
                self.assertEqual(checksum.compute(CHECK_INPUT), checksum.check)

    def test_fletcher_wikipedia_vectors(self) -> None:
        vectors = {
            16: {b"abcde": 0xC8F0, b"abcdef": 0x2057, b"abcdefgh": 0x0627},
            32: {b"abcde": 0xF04FC729, b"abcdef": 0x56502D2A, b"abcdefgh": 0xEBE19591},
            64: {b"abcde": 0xC8C6C527646362C6, b"abcdef": 0xC8C72B276463C8C6, b"abcdefgh": 0x312E2B28CCCAC8C6},
        }
        for width, cases in vectors.items():
            algorithm = FletcherChecksum(f"fletcher-{width}", width)
            for data, expected in cases.items():
                with self.subTest(width=width, data=data):
                    self.assertEqual(algorithm.compute(data), expected)

    def test_adler32_matches_zlib(self) -> None:
        rng = random.Random(3)
        algorithm = Adler32Checksum("adler-32")
        for _ in range(50):
            data = rng.randbytes(rng.randrange(0, 5000))
            self.assertEqual(algorithm.compute(data), zlib.adler32(data))

    def test_bsd_and_sysv_match_unix_sum(self) -> None:
        # `printf 123456789 | sum` -> 53615, `sum -s` -> 477
        self.assertEqual(BsdChecksum("bsd").compute(CHECK_INPUT), 53615)
        self.assertEqual(SysvChecksum("sysv").compute(CHECK_INPUT), 477)

    def test_internet_checksum_of_ipv4_header(self) -> None:
        header = bytes.fromhex("450000730000400040110000c0a80001c0a800c7")
        self.assertEqual(get_algorithm("internet-checksum").compute(header), 0xB861)

    def test_intel_hex_and_srecord_checksums(self) -> None:
        self.assertEqual(get_algorithm("intel-hex").compute(bytes.fromhex("0300300002337A")), 0x1E)
        self.assertEqual(get_algorithm("srec").compute(bytes.fromhex("137AF00A0A0D" + "00" * 13)), 0x61)


class SumChecksumTest(unittest.TestCase):
    def test_end_around_carry_versus_discard(self) -> None:
        self.assertEqual(get_algorithm("ones-complement-8").compute(b"\xff\x02"), 0xFD)
        self.assertEqual(get_algorithm("ones-complement-8-no-carry").compute(b"\xff\x02"), 0xFE)

    def test_odd_length_is_zero_padded(self) -> None:
        algorithm = get_algorithm("ones-complement-16")
        self.assertEqual(algorithm.compute(b"\x12\x34\x56"), algorithm.compute(b"\x12\x34\x56\x00"))

    def test_twos_complement_makes_the_total_zero(self) -> None:
        rng = random.Random(9)
        for name in ("twos-complement-8", "twos-complement-16", "twos-complement-32"):
            algorithm = get_algorithm(name)
            assert isinstance(algorithm, SumChecksum)
            for _ in range(20):
                data = rng.randbytes(rng.randrange(0, 64))
                self.assertEqual((algorithm.raw_sum(data) + algorithm.compute(data)) & algorithm.mask, 0)

    def test_ones_complement_checksum_verifies_to_all_ones(self) -> None:
        # RFC 1071: summing the data together with its checksum gives 0xFFFF.
        rng = random.Random(10)
        algorithm = get_algorithm("ones-complement-16")
        assert isinstance(algorithm, SumChecksum)
        for _ in range(50):
            data = rng.randbytes(2 * rng.randrange(1, 40))
            checksum = algorithm.compute(data)
            total = algorithm.accumulate(algorithm.raw_sum(data + checksum.to_bytes(2, "big")))
            self.assertEqual(total, 0xFFFF)

    def test_little_endian_words(self) -> None:
        big = SumChecksum("b", 16, word_size=2, endian="big")
        little = SumChecksum("l", 16, word_size=2, endian="little")
        self.assertEqual(big.compute(b"\x01\x02"), 0x0102)
        self.assertEqual(little.compute(b"\x01\x02"), 0x0201)

    def test_empty_input(self) -> None:
        self.assertEqual(get_algorithm("ones-complement-16").compute(b""), 0xFFFF)
        self.assertEqual(get_algorithm("twos-complement-16").compute(b""), 0)
        self.assertEqual(get_algorithm("sum-8").compute(b""), 0)

    def test_invalid_layouts_are_rejected(self) -> None:
        for kwargs in (
            {"width": 12},
            {"width": 8, "word_size": 2},
            {"width": 16, "endian": "middle"},
            {"width": 16, "carry": "sideways"},
            {"width": 16, "complement": "threes"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SumChecksum("bad", **kwargs)  # type: ignore[arg-type]

    def test_describe_mentions_layout(self) -> None:
        text = get_algorithm("ones-complement-16").describe()
        self.assertIn("words=16-bit", text)
        self.assertIn("carry=end-around", text)
        self.assertIn("complement=ones", text)


class XorChecksumTest(unittest.TestCase):
    def test_xor_words(self) -> None:
        self.assertEqual(XorChecksum("x", 8).compute(b"\x0f\xf0\xff"), 0x00)
        self.assertEqual(XorChecksum("x", 16).compute(b"\x12\x34\x00\x01\x56"), 0x1234 ^ 0x0001 ^ 0x5600)


if __name__ == "__main__":
    unittest.main()
