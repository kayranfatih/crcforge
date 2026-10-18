from __future__ import annotations

import argparse
import io
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401
from crcforge.io import (
    InputError,
    detect_original_data_kind,
    format_bytes,
    format_crc_result,
    load_input_bytes,
    parse_binary_bytes,
    parse_checksum_value,
    parse_data_literal,
    parse_frame,
    parse_hex_bytes,
    parse_number,
    parse_sample,
    parse_value_spec,
    read_lines,
    slice_data,
    text_input_note,
)


class ChecksumParsingTest(unittest.TestCase):
    def test_notations_and_width_hints(self) -> None:
        self.assertEqual(parse_checksum_value("0x4B37"), parse_checksum_value("0x4b37"))
        parsed = parse_checksum_value("0x0B37")
        self.assertEqual((parsed.value, parsed.width_hint, parsed.notation), (0xB37, 16, "hex"))
        parsed = parse_checksum_value("0b0100101100110111")
        self.assertEqual((parsed.value, parsed.width_hint, parsed.notation), (0x4B37, 16, "binary"))

    def test_decimal_width_is_rounded_up_to_whole_bytes(self) -> None:
        self.assertEqual(parse_checksum_value("19255").width_hint, 16)  # 0x4B37 has 15 significant bits
        self.assertEqual(parse_checksum_value("0").width_hint, 8)
        self.assertEqual(parse_checksum_value("4294967295").width_hint, 32)
        self.assertIn("decimal", parse_checksum_value("19255").width_reason())

    def test_errors_carry_hints(self) -> None:
        with self.assertRaises(InputError) as context:
            parse_checksum_value("4B37")
        self.assertIn("0x4B37", str(context.exception.hint))
        for bad in ("", "0x", "0b", "0xZZ", "0b102", "12.5"):
            with self.subTest(bad), self.assertRaises(InputError):
                parse_checksum_value(bad)

    def test_spaces_and_underscores_are_ignored(self) -> None:
        self.assertEqual(parse_checksum_value("0x4B_37").value, 0x4B37)
        self.assertEqual(parse_checksum_value(" 0x 4B 37 ").value, 0x4B37)


class ValueSpecTest(unittest.TestCase):
    def test_lists_ranges_and_steps(self) -> None:
        self.assertEqual(parse_value_spec("0x1021,0x8005", 0xFFFF, "poly"), [0x1021, 0x8005])
        self.assertEqual(parse_value_spec("0:3", 0xFF, "init"), [0, 1, 2, 3])
        self.assertEqual(parse_value_spec("0:0x10:8", 0xFF, "init"), [0, 8, 16])
        self.assertEqual(parse_value_spec("1,1,2", 0xFF, "x"), [1, 2])

    def test_errors(self) -> None:
        for spec in ("", "1:2:3:4", "5:1", "0:4:0", "0x100", "zz"):
            with self.subTest(spec), self.assertRaises(InputError):
                parse_value_spec(spec, 0xFF, "poly")

    def test_parse_number(self) -> None:
        self.assertEqual(parse_number("0x10"), 16)
        self.assertEqual(parse_number("0b11"), 3)
        self.assertEqual(parse_number("-4"), -4)
        with self.assertRaises(InputError):
            parse_number("ten")


class DataParsingTest(unittest.TestCase):
    def test_hex_and_binary(self) -> None:
        self.assertEqual(parse_hex_bytes("0xDE AD_BE EF"), b"\xde\xad\xbe\xef")
        self.assertEqual(parse_hex_bytes("0xAA0xBB"), b"\xaa\xbb")
        self.assertEqual(parse_binary_bytes("0b01000001 0b01000010"), b"AB")
        self.assertEqual(parse_hex_bytes("0x"), b"")

    def test_hex_errors_suggest_as_text(self) -> None:
        with self.assertRaises(InputError) as context:
            parse_hex_bytes("0xygen")
        self.assertIn("--as-text", str(context.exception.hint))
        with self.assertRaises(InputError):
            parse_hex_bytes("0xABC")
        with self.assertRaises(InputError):
            parse_binary_bytes("0b0101")
        with self.assertRaises(InputError):
            parse_binary_bytes("0b0102")

    def test_literal_rules(self) -> None:
        self.assertEqual(parse_data_literal("0x4142"), b"AB")
        self.assertEqual(parse_data_literal("0b0100000101000010"), b"AB")
        self.assertEqual(parse_data_literal("hello"), b"hello")
        self.assertEqual(parse_data_literal("0x4142", as_text=True), b"0x4142")
        self.assertEqual(parse_data_literal("héllo"), "héllo".encode())

    def test_stdin(self) -> None:
        original = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(b"\x00\x01binary"))
        try:
            self.assertEqual(parse_data_literal("-"), b"\x00\x01binary")
        finally:
            sys.stdin = original

    def test_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blob.bin"
            path.write_bytes(b"\x01\x02")
            self.assertEqual(parse_data_literal(str(path), as_file=True), b"\x01\x02")
            with self.assertRaises(InputError):
                parse_data_literal(str(path) + ".missing", as_file=True)
            with self.assertRaises(InputError):
                parse_data_literal(directory, as_file=True)

    def test_slice_data(self) -> None:
        data = bytes(range(10))
        self.assertEqual(slice_data(data, offset=2, length=3), b"\x02\x03\x04")
        self.assertEqual(slice_data(data, exclude_tail=2), data[:8])
        self.assertEqual(slice_data(data, offset=10), b"")
        for kwargs in ({"offset": 11}, {"offset": 5, "length": 6}, {"exclude_tail": 11}, {"offset": -1}):
            with self.subTest(kwargs), self.assertRaises(InputError):
                slice_data(data, **kwargs)  # type: ignore[arg-type]

    def test_load_input_bytes_applies_slicing(self) -> None:
        args = argparse.Namespace(
            original_data="0x00112233445566", as_file=False, as_text=False, offset=1, length=None, exclude_tail=2
        )
        self.assertEqual(load_input_bytes(args), bytes.fromhex("11223344"))

    def test_kind_detection_and_file_note(self) -> None:
        def ns(value: str, **kwargs: bool) -> argparse.Namespace:
            return argparse.Namespace(
                original_data=value, as_file=kwargs.get("as_file", False), as_text=kwargs.get("as_text", False)
            )

        self.assertEqual(detect_original_data_kind(ns("0x00")), "hex-bytes")
        self.assertEqual(detect_original_data_kind(ns("0b00000000")), "binary-bytes")
        self.assertEqual(detect_original_data_kind(ns("-")), "stdin-bytes")
        self.assertEqual(detect_original_data_kind(ns("x", as_file=True)), "file-bytes")
        self.assertEqual(detect_original_data_kind(ns("0x00", as_text=True)), "plain-text")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "firmware.bin"
            path.write_bytes(b"x")
            self.assertIn("--as-file", str(text_input_note(ns(str(path)))))
        self.assertIsNone(text_input_note(ns("definitely-not-a-file")))


class SamplesAndFramesTest(unittest.TestCase):
    def test_parse_sample(self) -> None:
        data, checksum = parse_sample("0x313233:0x5BCE")
        self.assertEqual((data, checksum.value), (b"123", 0x5BCE))
        data, checksum = parse_sample("a:b:c:255")
        self.assertEqual((data, checksum.value), (b"a:b:c", 255))
        with self.assertRaises(InputError):
            parse_sample("no-checksum")

    def test_parse_sample_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.bin"
            path.write_bytes(b"\xff")
            data, checksum = parse_sample(f"@{path}:0x12")
            self.assertEqual((data, checksum.value), (b"\xff", 0x12))

    def test_parse_frame(self) -> None:
        self.assertEqual(parse_frame("01 03 00"), b"\x01\x03\x00")
        self.assertEqual(parse_frame("0x010300"), b"\x01\x03\x00")
        self.assertEqual(parse_frame("0b00000001"), b"\x01")

    def test_read_lines_skips_comments_and_blanks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.txt"
            path.write_text("# header\n\n0x01:0x02\n  0x03:0x04  \n")
            self.assertEqual(read_lines(str(path)), ["0x01:0x02", "0x03:0x04"])
        with self.assertRaises(InputError):
            read_lines("missing-file.txt")


class FormattingTest(unittest.TestCase):
    def test_format_crc_result(self) -> None:
        self.assertEqual(format_crc_result(0x4B37, 16, "hex"), "0x4B37")
        self.assertEqual(format_crc_result(0x4B37, 16, "dec"), "19255")
        self.assertEqual(format_crc_result(0x5, 4, "bin"), "0b0101")
        self.assertEqual(format_crc_result(0x4B37, 16, "both"), "0x4B37 (19255)")

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(b"AB", "hex"), "41 42")
        self.assertEqual(format_bytes(b"AB", "compact-hex"), "4142")
        self.assertEqual(format_bytes(b"AB\xff", "text"), "AB�")


if __name__ == "__main__":
    unittest.main()
