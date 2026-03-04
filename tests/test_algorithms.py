from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crcforge.algorithms import calculate_crc, get_algorithm, validate_catalog


class AlgorithmsTestCase(unittest.TestCase):
    def test_crc_16_modbus_check_vector(self) -> None:
        algorithm = get_algorithm("crc-16-modbus")
        self.assertEqual(calculate_crc(b"123456789", algorithm), 0x4B37)

    def test_alias_lookup_returns_same_algorithm(self) -> None:
        self.assertEqual(get_algorithm("modbus").name, "crc-16-modbus")

    def test_catalog_self_validation(self) -> None:
        self.assertEqual(validate_catalog(), [])


if __name__ == "__main__":
    unittest.main()
