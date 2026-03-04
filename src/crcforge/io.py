from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .algorithms import format_crc


@dataclass(frozen=True)
class ParsedChecksum:
    value: int
    width_hint: int


def normalize_literal(raw_value: str) -> str:
    return "".join(raw_value.strip().split()).replace("_", "")


def parse_checksum_value(raw_value: str) -> ParsedChecksum:
    text = normalize_literal(raw_value).lower()
    if not text:
        raise ValueError("CRC value is empty.")

    if text.startswith("0x"):
        digits = text[2:]
        if not digits:
            raise ValueError("Hex checksum value is empty after 0x.")
        return ParsedChecksum(int(digits, 16), max(1, len(digits) * 4))

    if text.startswith("0b"):
        digits = text[2:]
        if not digits:
            raise ValueError("Binary checksum value is empty after 0b.")
        return ParsedChecksum(int(digits, 2), max(1, len(digits)))

    if any(character in "abcdef" for character in text):
        raise ValueError("Hex checksum values must start with 0x.")

    try:
        value = int(text, 10)
    except ValueError as error:
        raise ValueError(
            "Checksum values must be decimal digits, 0x-prefixed hex, or 0b-prefixed binary."
        ) from error

    return ParsedChecksum(value, max(1, value.bit_length()))


def parse_crc_value(raw_value: str) -> int:
    return parse_checksum_value(raw_value).value


def parse_hex_bytes(raw_value: str) -> bytes:
    text = normalize_literal(raw_value)
    text = text.replace("0x", "").replace("0X", "")
    if not text:
        return b""
    if len(text) % 2 != 0:
        raise ValueError("Hex input must contain an even number of digits.")
    try:
        return bytes.fromhex(text)
    except ValueError as error:
        raise ValueError(f"Invalid hex input: {raw_value}") from error


def parse_binary_bytes(raw_value: str) -> bytes:
    text = normalize_literal(raw_value)
    text = text.replace("0b", "").replace("0B", "")
    if not text:
        return b""
    if any(character not in "01" for character in text):
        raise ValueError(f"Invalid binary input: {raw_value}")
    if len(text) % 8 != 0:
        raise ValueError("Binary input must contain a whole number of bytes (8 bits each).")
    return bytes(int(text[index : index + 8], 2) for index in range(0, len(text), 8))


def is_hex_literal(raw_value: str) -> bool:
    text = normalize_literal(raw_value)
    return text.startswith("0x") or text.startswith("0X")


def is_binary_literal(raw_value: str) -> bool:
    text = normalize_literal(raw_value)
    return text.startswith("0b") or text.startswith("0B")


def load_input_bytes(args: argparse.Namespace) -> bytes:
    if args.as_file:
        return Path(args.original_data).read_bytes()
    if getattr(args, "as_text", False):
        return args.original_data.encode("utf-8")
    if is_hex_literal(args.original_data):
        return parse_hex_bytes(args.original_data)
    if is_binary_literal(args.original_data):
        return parse_binary_bytes(args.original_data)
    return args.original_data.encode("utf-8")


def detect_original_data_kind(args: argparse.Namespace) -> str:
    if getattr(args, "as_file", False):
        return "file-bytes"
    if getattr(args, "as_text", False):
        return "plain-text"
    if hasattr(args, "original_data") and is_hex_literal(args.original_data):
        return "hex-bytes"
    if hasattr(args, "original_data") and is_binary_literal(args.original_data):
        return "binary-bytes"
    return "plain-text"


def format_crc_result(value: int, width: int, output_format: str) -> str:
    hex_value = format_crc(value, width)
    if output_format == "hex":
        return hex_value
    if output_format == "dec":
        return str(value)
    return f"{hex_value} ({value})"


def format_bytes(data: bytes, output_format: str) -> str:
    if output_format == "text":
        return data.decode("utf-8", errors="replace")
    if output_format == "compact-hex":
        return data.hex().upper()
    return " ".join(f"{byte:02X}" for byte in data)
