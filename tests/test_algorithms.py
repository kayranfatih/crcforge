from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crcforge.algorithms import (
    calculate_checksum,
    calculate_crc,
    find_matching_algorithms,
    get_algorithm,
    validate_catalog,
)


class AlgorithmsTestCase(unittest.TestCase):
    def test_crc_16_modbus_check_vector(self) -> None:
        algorithm = get_algorithm("crc-16-modbus")
        self.assertEqual(calculate_crc(b"123456789", algorithm), 0x4B37)

    def test_alias_lookup_returns_same_algorithm(self) -> None:
        self.assertEqual(get_algorithm("modbus").name, "crc-16-modbus")

    def test_catalog_self_validation(self) -> None:
        self.assertEqual(validate_catalog(), [])


class ComplementChecksumTestCase(unittest.TestCase):
    def test_internet_checksum_ipv4_header(self) -> None:
        # IPv4 header with its checksum field zeroed; the real header carries 0xB861.
        header = bytes.fromhex("450000730000400040110000c0a80001c0a800c7")
        self.assertEqual(calculate_checksum(header, get_algorithm("internet-checksum")), 0xB861)

    def test_internet_checksum_pads_odd_length(self) -> None:
        algorithm = get_algorithm("ones-complement-16")
        self.assertEqual(
            calculate_checksum(b"\x12\x34\x56", algorithm),
            calculate_checksum(b"\x12\x34\x56\x00", algorithm),
        )

    def test_ones_complement_end_around_carry(self) -> None:
        # 0xFF + 0x02 = 0x101 -> fold carry -> 0x02 -> complement -> 0xFD
        self.assertEqual(calculate_checksum(b"\xFF\x02", get_algorithm("ones-complement-8")), 0xFD)
        # Same input with carries discarded: 0x01 -> complement -> 0xFE
        self.assertEqual(calculate_checksum(b"\xFF\x02", get_algorithm("srec")), 0xFE)

    def test_intel_hex_record_checksum(self) -> None:
        record = bytes.fromhex("0300300002337A")
        self.assertEqual(calculate_checksum(record, get_algorithm("intel-hex")), 0x1E)

    def test_motorola_srecord_checksum(self) -> None:
        record = bytes.fromhex("137AF00A0A0D" + "00" * 13)
        self.assertEqual(calculate_checksum(record, get_algorithm("s-record")), 0x61)

    def test_twos_complement_sums_to_zero(self) -> None:
        data = b"firmware block"
        for name in ("twos-complement-8", "twos-complement-16", "twos-complement-32"):
            algorithm = get_algorithm(name)
            word_size = algorithm.width // 8
            padded = data + bytes(-len(data) % word_size)
            checksum = calculate_checksum(data, algorithm)
            total = sum(
                int.from_bytes(padded[index : index + word_size], "big")
                for index in range(0, len(padded), word_size)
            )
            self.assertEqual((total + checksum) & algorithm.mask, 0, name)

    def test_empty_input(self) -> None:
        self.assertEqual(calculate_checksum(b"", get_algorithm("ones-complement-16")), 0xFFFF)
        self.assertEqual(calculate_checksum(b"", get_algorithm("twos-complement-16")), 0x0000)

    def test_find_matches_checksum_algorithms(self) -> None:
        names = [algorithm.name for algorithm in find_matching_algorithms(b"123456789", 0xF62A, width=16)]
        self.assertIn("ones-complement-16", names)


if __name__ == "__main__":
    unittest.main()
