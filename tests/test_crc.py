from __future__ import annotations

import binascii
import os
import random
import unittest
import zlib

import _support  # noqa: F401
from crcforge.catalog import CHECK_INPUT, get_algorithm
from crcforge.catalog_crc import CRC_CATALOG
from crcforge.crc import (
    CrcAlgorithm,
    _process_bitwise,
    _process_table,
    apply_columns,
    calculate_crc,
    calculate_crc_reference,
    calculate_residue,
    crc_table,
    format_value,
    init_columns,
    raw_crc,
    reflect_bits,
    validate_params,
)

WIDTHS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 16, 17, 21, 24, 31, 32, 33, 40, 63, 64, 65, 82)


def random_algorithm(rng: random.Random, width: int) -> CrcAlgorithm:
    return CrcAlgorithm(
        "random",
        width,
        rng.randrange(1 << width),
        rng.randrange(1 << width),
        rng.randrange(1 << width),
        rng.random() < 0.5,
        rng.random() < 0.5,
    )


class CatalogVectorsTest(unittest.TestCase):
    def test_every_catalog_crc_matches_its_published_check_value(self) -> None:
        for algorithm in CRC_CATALOG:
            with self.subTest(algorithm.name):
                self.assertEqual(calculate_crc(CHECK_INPUT, algorithm), algorithm.check)
                self.assertEqual(calculate_crc_reference(CHECK_INPUT, algorithm), algorithm.check)

    def test_every_catalog_crc_matches_its_published_residue(self) -> None:
        for algorithm in CRC_CATALOG:
            with self.subTest(algorithm.name):
                self.assertEqual(calculate_residue(algorithm), algorithm.residue)

    def test_residue_is_the_register_after_a_valid_codeword(self) -> None:
        # For byte-aligned CRCs with refin == refout, appending the CRC (in transmission order)
        # leaves the residue in the register before the final XOR.
        for algorithm in CRC_CATALOG:
            if algorithm.width % 8 or algorithm.refin != algorithm.refout:
                continue
            with self.subTest(algorithm.name):
                crc = calculate_crc(b"payload", algorithm)
                order = "little" if algorithm.refout else "big"
                codeword = b"payload" + crc.to_bytes(algorithm.width // 8, order)
                self.assertEqual(calculate_crc(codeword, algorithm) ^ algorithm.xor_out, algorithm.residue)


class EngineConsistencyTest(unittest.TestCase):
    def test_table_bitwise_and_reference_agree_for_random_parameters(self) -> None:
        rng = random.Random(1234)
        for width in WIDTHS:
            for _ in range(25):
                algorithm = random_algorithm(rng, width)
                data = rng.randbytes(rng.randrange(0, 300))
                register = reflect_bits(algorithm.init, width) if algorithm.refin else algorithm.init
                with self.subTest(width=width, algorithm=algorithm):
                    self.assertEqual(
                        _process_table(data, width, algorithm.poly, register, algorithm.refin),
                        _process_bitwise(data, width, algorithm.poly, register, algorithm.refin),
                    )
                    self.assertEqual(calculate_crc(data, algorithm), calculate_crc_reference(data, algorithm))

    def test_large_input_uses_table_and_matches_reference(self) -> None:
        data = os.urandom(5000)
        for name in ("crc-16-modbus", "crc-8", "crc-64-xz", "crc-82-darc", "crc-5-usb", "crc-12-umts"):
            algorithm = get_algorithm(name)
            assert isinstance(algorithm, CrcAlgorithm)
            with self.subTest(name):
                self.assertEqual(calculate_crc(data, algorithm), calculate_crc_reference(data, algorithm))

    def test_zlib_fast_path_matches_reference_for_any_init_and_xorout(self) -> None:
        rng = random.Random(5)
        for _ in range(100):
            algorithm = CrcAlgorithm(
                "z", 32, 0x04C11DB7, rng.randrange(1 << 32), rng.randrange(1 << 32), True, rng.random() < 0.5
            )
            data = rng.randbytes(rng.randrange(0, 200))
            self.assertEqual(calculate_crc(data, algorithm), calculate_crc_reference(data, algorithm))

    def test_binascii_fast_path_matches_reference_for_any_init_and_xorout(self) -> None:
        rng = random.Random(6)
        for _ in range(100):
            algorithm = CrcAlgorithm(
                "h", 16, 0x1021, rng.randrange(1 << 16), rng.randrange(1 << 16), False, rng.random() < 0.5
            )
            data = rng.randbytes(rng.randrange(0, 200))
            self.assertEqual(calculate_crc(data, algorithm), calculate_crc_reference(data, algorithm))

    def test_matches_standard_library_implementations(self) -> None:
        data = os.urandom(10_000)
        self.assertEqual(calculate_crc(data, get_algorithm("crc-32")), zlib.crc32(data))  # type: ignore[arg-type]
        self.assertEqual(calculate_crc(data, get_algorithm("xmodem")), binascii.crc_hqx(data, 0))  # type: ignore[arg-type]

    def test_linear_model_raw_plus_init_columns_plus_xorout(self) -> None:
        rng = random.Random(77)
        for width in WIDTHS:
            for _ in range(10):
                algorithm = random_algorithm(rng, width)
                data = rng.randbytes(rng.randrange(0, 50))
                columns = init_columns(width, algorithm.poly, algorithm.refout, len(data))
                model = (
                    raw_crc(data, width, algorithm.poly, algorithm.refin, algorithm.refout)
                    ^ apply_columns(columns, algorithm.init)
                    ^ algorithm.xor_out
                )
                with self.subTest(width=width, algorithm=algorithm):
                    self.assertEqual(model, calculate_crc_reference(data, algorithm))

    def test_empty_input_returns_init_transformed(self) -> None:
        algorithm = get_algorithm("crc-16-ccitt-false")
        assert isinstance(algorithm, CrcAlgorithm)
        self.assertEqual(calculate_crc(b"", algorithm), 0xFFFF)
        self.assertEqual(calculate_crc(b"", get_algorithm("crc-32")), 0)  # type: ignore[arg-type]


class ConventionTest(unittest.TestCase):
    def test_init_uses_reveng_notation_for_reflected_crcs(self) -> None:
        # RevEng lists CRC-16/TMS37157 with init=0x89ec; copying those parameters must reproduce the check.
        algorithm = CrcAlgorithm("tms", 16, 0x1021, 0x89EC, 0x0000, True, True)
        self.assertEqual(calculate_crc(CHECK_INPUT, algorithm), 0x26B1)

    def test_refin_differs_from_refout(self) -> None:
        algorithm = get_algorithm("crc-12-umts")
        assert isinstance(algorithm, CrcAlgorithm)
        self.assertNotEqual(algorithm.refin, algorithm.refout)
        self.assertEqual(calculate_crc(CHECK_INPUT, algorithm), 0xDAF)

    def test_reveng_spec_and_reference(self) -> None:
        algorithm = get_algorithm("crc-16-modbus")
        assert isinstance(algorithm, CrcAlgorithm)
        self.assertEqual(
            algorithm.reveng_spec(),
            "width=16 poly=0x8005 init=0xffff refin=true refout=true xorout=0x0000",
        )
        self.assertTrue(str(algorithm.reference_url).endswith("#crc.cat.crc-16-modbus"))


class HelpersTest(unittest.TestCase):
    def test_reflect_bits(self) -> None:
        self.assertEqual(reflect_bits(0b0001, 4), 0b1000)
        self.assertEqual(reflect_bits(0x1, 32), 0x80000000)
        self.assertEqual(reflect_bits(0, 0), 0)
        for width in (1, 7, 16, 33):
            value = random.Random(width).randrange(1 << width)
            self.assertEqual(reflect_bits(reflect_bits(value, width), width), value)

    def test_format_value_pads_to_width(self) -> None:
        self.assertEqual(format_value(0x5, 16), "0x0005")
        self.assertEqual(format_value(0x1, 3), "0x1")
        self.assertEqual(format_value(0xABC, 12), "0xABC")

    def test_table_cache_returns_same_object(self) -> None:
        self.assertIs(crc_table(16, 0x8005, True), crc_table(16, 0x8005, True))

    def test_validate_params_rejects_values_wider_than_width(self) -> None:
        validate_params(8, 0x07, 0x00, 0xFF)
        with self.assertRaises(ValueError):
            validate_params(8, 0x107, 0, 0)
        with self.assertRaises(ValueError):
            validate_params(0, 0, 0, 0)

    def test_zero_width_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_crc(b"x", CrcAlgorithm("bad", 0, 0, 0, 0, False, False))


if __name__ == "__main__":
    unittest.main()
