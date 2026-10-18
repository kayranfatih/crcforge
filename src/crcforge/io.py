"""Input parsing: payload literals, checksum values, parameter specs, samples and frames."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .crc import format_value


class InputError(ValueError):
    """Invalid user input, optionally with a hint on how to fix it."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class ParsedChecksum:
    value: int
    width_hint: int
    notation: str = "hex"

    def width_reason(self) -> str:
        if self.notation == "hex":
            return f"{self.width_hint // 4} hex digits"
        if self.notation == "binary":
            return f"{self.width_hint} binary digits"
        return "the decimal value, rounded up to whole bytes"


def normalize_literal(raw_value: str) -> str:
    return "".join(raw_value.strip().split()).replace("_", "")


def parse_checksum_value(raw_value: str) -> ParsedChecksum:
    text = normalize_literal(raw_value).lower()
    if not text:
        raise InputError("Checksum value is empty.")

    if text.startswith("0x"):
        digits = text[2:]
        if not digits:
            raise InputError("Hex checksum value is empty after 0x.")
        try:
            value = int(digits, 16)
        except ValueError:
            raise InputError(f"Invalid hex checksum: {raw_value}") from None
        return ParsedChecksum(value, max(1, len(digits) * 4), "hex")

    if text.startswith("0b"):
        digits = text[2:]
        if not digits:
            raise InputError("Binary checksum value is empty after 0b.")
        try:
            value = int(digits, 2)
        except ValueError:
            raise InputError(f"Invalid binary checksum: {raw_value}") from None
        return ParsedChecksum(value, max(1, len(digits)), "binary")

    if any(character in "abcdef" for character in text):
        raise InputError(
            f"Checksum '{raw_value}' contains hex letters but no 0x prefix.",
            hint=f"Write it as 0x{raw_value.strip()} (plain digits are read as decimal).",
        )
    try:
        value = int(text, 10)
    except ValueError:
        raise InputError(
            f"Invalid checksum: {raw_value}",
            hint="Use decimal digits, 0x-prefixed hex, or 0b-prefixed binary.",
        ) from None
    return ParsedChecksum(value, max(8, -(-value.bit_length() // 8) * 8), "decimal")


def parse_crc_value(raw_value: str) -> int:
    return parse_checksum_value(raw_value).value


def parse_number(raw_value: str, label: str = "value") -> int:
    text = normalize_literal(raw_value).lower()
    try:
        if text.startswith("0x"):
            return int(text[2:], 16)
        if text.startswith("0b"):
            return int(text[2:], 2)
        return int(text, 10)
    except ValueError:
        raise InputError(
            f"Invalid {label}: {raw_value}", hint="Use decimal, 0x-prefixed hex or 0b-prefixed binary."
        ) from None


def parse_value_spec(raw_value: str, mask: int, label: str) -> list[int]:
    """Parse ``VALUE``, comma lists and inclusive ``START:END[:STEP]`` ranges."""
    values: list[int] = []
    seen: set[int] = set()
    width = max(1, mask.bit_length())
    for part in raw_value.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            bounds = [item.strip() for item in token.split(":")]
            if len(bounds) not in (2, 3):
                raise InputError(f"Invalid {label} range '{token}'.", hint="Use START:END or START:END:STEP.")
            start = parse_number(bounds[0], label)
            end = parse_number(bounds[1], label)
            step = parse_number(bounds[2], label) if len(bounds) == 3 else 1
            if step <= 0:
                raise InputError(f"{label} range step must be positive.")
            if start > end:
                raise InputError(f"{label} range start must be <= end.")
            candidates: range | list[int] = range(start, end + 1, step)
        else:
            candidates = [parse_number(token, label)]

        for value in candidates:
            if value < 0 or value > mask:
                raise InputError(
                    f"{label} value {format_value(value, max(width, value.bit_length()))} does not fit in "
                    f"{width} bits.",
                    hint="Check --width, or the value's number of digits.",
                )
            if value not in seen:
                values.append(value)
                seen.add(value)

    if not values:
        raise InputError(f"{label} specification is empty.")
    return values


def parse_hex_bytes(raw_value: str) -> bytes:
    text = normalize_literal(raw_value)
    text = text.replace("0x", "").replace("0X", "")
    if not text:
        return b""
    if len(text) % 2 != 0:
        raise InputError(
            f"Hex input has an odd number of digits ({len(text)}).",
            hint="Each byte needs two hex digits; add a leading 0 or use --as-text for plain text.",
        )
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise InputError(
            f"Invalid hex input: {raw_value}",
            hint="To use this value as plain text instead, add --as-text.",
        ) from None


def parse_binary_bytes(raw_value: str) -> bytes:
    text = normalize_literal(raw_value)
    text = text.replace("0b", "").replace("0B", "")
    if not text:
        return b""
    if any(character not in "01" for character in text):
        raise InputError(
            f"Invalid binary input: {raw_value}",
            hint="To use this value as plain text instead, add --as-text.",
        )
    if len(text) % 8 != 0:
        raise InputError(
            f"Binary input has {len(text)} bits, which is not a whole number of bytes.",
            hint="Binary input needs 8 bits per byte.",
        )
    return bytes(int(text[index : index + 8], 2) for index in range(0, len(text), 8))


def is_hex_literal(raw_value: str) -> bool:
    text = normalize_literal(raw_value)
    return text.startswith(("0x", "0X"))


def is_binary_literal(raw_value: str) -> bool:
    text = normalize_literal(raw_value)
    return text.startswith(("0b", "0B"))


def read_file_bytes(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        raise InputError(f"File not found: {path}") from None
    except IsADirectoryError:
        raise InputError(f"{path} is a directory, not a file.") from None
    except PermissionError:
        raise InputError(f"Permission denied: {path}") from None


def parse_data_literal(raw_value: str, as_file: bool = False, as_text: bool = False) -> bytes:
    """Bytes from a payload literal: ``-`` (stdin), ``0x...``, ``0b...``, ``@file`` or text."""
    if as_file:
        if raw_value == "-":
            return sys.stdin.buffer.read()
        return read_file_bytes(raw_value)
    if as_text:
        return raw_value.encode("utf-8")
    if raw_value == "-":
        return sys.stdin.buffer.read()
    if is_hex_literal(raw_value):
        return parse_hex_bytes(raw_value)
    if is_binary_literal(raw_value):
        return parse_binary_bytes(raw_value)
    return raw_value.encode("utf-8")


def slice_data(data: bytes, offset: int = 0, length: int | None = None, exclude_tail: int = 0) -> bytes:
    if offset < 0 or (length is not None and length < 0) or exclude_tail < 0:
        raise InputError("--offset, --length and --exclude-tail must not be negative.")
    if offset > len(data):
        raise InputError(f"--offset {offset} is beyond the end of the data ({len(data)} bytes).")
    end = len(data) - exclude_tail
    if end < offset:
        raise InputError(
            f"--exclude-tail {exclude_tail} with --offset {offset} leaves no data ({len(data)} bytes in total)."
        )
    if length is not None:
        if offset + length > end:
            raise InputError(
                f"--length {length} at --offset {offset} runs past the end of the data ({end} usable bytes)."
            )
        end = offset + length
    return data[offset:end]


def load_input_bytes(args: argparse.Namespace) -> bytes:
    data = parse_data_literal(
        args.original_data,
        as_file=bool(getattr(args, "as_file", False)),
        as_text=bool(getattr(args, "as_text", False)),
    )
    return slice_data(
        data,
        offset=getattr(args, "offset", 0) or 0,
        length=getattr(args, "length", None),
        exclude_tail=getattr(args, "exclude_tail", 0) or 0,
    )


def detect_original_data_kind(args: argparse.Namespace) -> str:
    value = getattr(args, "original_data", "")
    if getattr(args, "as_file", False):
        return "stdin-bytes" if value == "-" else "file-bytes"
    if getattr(args, "as_text", False):
        return "plain-text"
    if value == "-":
        return "stdin-bytes"
    if is_hex_literal(value):
        return "hex-bytes"
    if is_binary_literal(value):
        return "binary-bytes"
    return "plain-text"


def text_input_note(args: argparse.Namespace) -> str | None:
    """A note when a plain-text payload is also the name of an existing file."""
    value = getattr(args, "original_data", "")
    if detect_original_data_kind(args) != "plain-text" or getattr(args, "as_text", False):
        return None
    if value and os.path.isfile(value):
        return f"'{value}' is the name of an existing file but was used as text; add --as-file to read its bytes."
    return None


def parse_sample(raw_value: str) -> tuple[bytes, ParsedChecksum]:
    """``DATA:CHECKSUM`` where DATA follows the payload rules and may be ``@path``."""
    if ":" not in raw_value:
        raise InputError(
            f"Sample '{raw_value}' has no checksum.",
            hint="Write samples as DATA:CHECKSUM, for example 0x313233:0x5BCE or hello:0x1234.",
        )
    data_text, checksum_text = raw_value.rsplit(":", 1)
    data = read_file_bytes(data_text[1:]) if data_text.startswith("@") else parse_data_literal(data_text)
    return data, parse_checksum_value(checksum_text)


def parse_frame(raw_value: str) -> bytes:
    """A captured frame: hex (``0x`` optional, spaces allowed) or ``@path`` for a binary file."""
    if raw_value.startswith("@"):
        return read_file_bytes(raw_value[1:])
    text = normalize_literal(raw_value)
    if text.lower().startswith("0b"):
        return parse_binary_bytes(text)
    return parse_hex_bytes(text)


def read_lines(path: str) -> list[str]:
    try:
        content = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InputError(f"File not found: {path}") from None
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def format_crc_result(value: int, width: int, output_format: str) -> str:
    hex_value = format_value(value, width)
    if output_format == "hex":
        return hex_value
    if output_format == "dec":
        return str(value)
    if output_format == "bin":
        return f"0b{value:0{width}b}"
    return f"{hex_value} ({value})"


def format_bytes(data: bytes, output_format: str) -> str:
    if output_format == "text":
        return data.decode("utf-8", errors="replace")
    if output_format == "compact-hex":
        return data.hex().upper()
    return " ".join(f"{byte:02X}" for byte in data)
