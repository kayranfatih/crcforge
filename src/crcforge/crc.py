"""CRC model and engine.

Parameters follow the Rocksoft / RevEng model: ``init`` and ``xor_out`` are given in
the same notation as the RevEng CRC catalogue, so values can be copied from it as-is.
"""

from __future__ import annotations

import binascii
import zlib
from dataclasses import dataclass, field

from . import gf2

_REFLECT8 = tuple(int(f"{byte:08b}"[::-1], 2) for byte in range(256))
_TABLE_CACHE: dict[tuple[int, int, bool], tuple[int, ...]] = {}
_TABLE_CACHE_LIMIT = 256
# Building a table costs about as much as 256 bytes of bitwise processing.
_TABLE_MIN_BYTES = 256

REVENG_URL = "https://reveng.sourceforge.io/crc-catalogue/all.htm"


def reflect_bits(value: int, width: int) -> int:
    if width <= 0:
        return 0
    return int(f"{value & ((1 << width) - 1):0{width}b}"[::-1], 2)


def reflect_bytes(data: bytes) -> bytes:
    return bytes(_REFLECT8[byte] for byte in data)


def format_value(value: int, width: int) -> str:
    digits = max(1, (width + 3) // 4)
    return f"0x{value:0{digits}X}"


@dataclass(frozen=True)
class CrcAlgorithm:
    name: str
    width: int
    poly: int
    init: int
    xor_out: int
    refin: bool
    refout: bool
    aliases: tuple[str, ...] = ()
    check: int | None = None
    residue: int | None = None
    status: str | None = field(default=None, compare=False)
    reveng_name: str | None = field(default=None, compare=False)

    family = "crc"

    @property
    def mask(self) -> int:
        return (1 << self.width) - 1

    @property
    def reference_url(self) -> str | None:
        if self.reveng_name is None:
            return None
        anchor = self.reveng_name.lower().replace("/", "-")
        return f"{REVENG_URL}#crc.cat.{anchor}"

    def compute(self, data: bytes) -> int:
        return calculate_crc(data, self)

    def describe(self) -> str:
        return (
            f"{self.name}: width={self.width}, "
            f"poly={format_value(self.poly, self.width)}, "
            f"init={format_value(self.init, self.width)}, "
            f"refin={str(self.refin).lower()}, refout={str(self.refout).lower()}, "
            f"xorout={format_value(self.xor_out, self.width)}"
        )

    def params(self) -> dict[str, object]:
        return {
            "width": self.width,
            "poly": format_value(self.poly, self.width),
            "init": format_value(self.init, self.width),
            "refin": self.refin,
            "refout": self.refout,
            "xorout": format_value(self.xor_out, self.width),
        }

    def reveng_spec(self) -> str:
        """Parameters in RevEng's ``width=... poly=...`` notation."""
        digits = max(1, (self.width + 3) // 4)
        return (
            f"width={self.width} poly=0x{self.poly:0{digits}x} init=0x{self.init:0{digits}x} "
            f"refin={str(self.refin).lower()} refout={str(self.refout).lower()} "
            f"xorout=0x{self.xor_out:0{digits}x}"
        )


def validate_params(width: int, poly: int, init: int, xor_out: int) -> None:
    if width <= 0:
        raise ValueError("CRC width must be positive.")
    mask = (1 << width) - 1
    for label, value in (("poly", poly), ("init", init), ("xorout", xor_out)):
        if value < 0 or value > mask:
            raise ValueError(
                f"{label} value {format_value(value, max(width, value.bit_length()))} does not fit in {width} bits."
            )


def _build_table(width: int, poly: int, refin: bool) -> tuple[int, ...]:
    table = []
    if refin:
        reflected_poly = reflect_bits(poly, width)
        for index in range(256):
            register = index
            for _ in range(8):
                register = (register >> 1) ^ reflected_poly if register & 1 else register >> 1
            table.append(register)
    else:
        register_width = max(width, 8)
        shifted_poly = poly << (register_width - width)
        top_bit = 1 << (register_width - 1)
        register_mask = (1 << register_width) - 1
        for index in range(256):
            register = index << (register_width - 8)
            for _ in range(8):
                if register & top_bit:
                    register = ((register << 1) ^ shifted_poly) & register_mask
                else:
                    register = (register << 1) & register_mask
            table.append(register)
    return tuple(table)


def crc_table(width: int, poly: int, refin: bool) -> tuple[int, ...]:
    key = (width, poly, refin)
    table = _TABLE_CACHE.get(key)
    if table is None:
        if len(_TABLE_CACHE) >= _TABLE_CACHE_LIMIT:
            _TABLE_CACHE.clear()
        table = _build_table(width, poly, refin)
        _TABLE_CACHE[key] = table
    return table


def _process_bitwise(data: bytes, width: int, poly: int, register: int, refin: bool) -> int:
    mask = (1 << width) - 1
    if refin:
        reflected_poly = reflect_bits(poly, width)
        for byte in data:
            for bit_index in range(8):
                if (register ^ (byte >> bit_index)) & 1:
                    register = (register >> 1) ^ reflected_poly
                else:
                    register >>= 1
    else:
        top_shift = width - 1
        for byte in data:
            for bit_index in range(7, -1, -1):
                top_bit = ((register >> top_shift) ^ (byte >> bit_index)) & 1
                register = (register << 1) & mask
                if top_bit:
                    register ^= poly
    return register


def _process_table(data: bytes, width: int, poly: int, register: int, refin: bool) -> int:
    table = crc_table(width, poly, refin)
    if refin:
        for byte in data:
            register = table[(register ^ byte) & 0xFF] ^ (register >> 8)
        return register
    register_width = max(width, 8)
    shift = register_width - width
    top_shift = register_width - 8
    register_mask = (1 << register_width) - 1
    register <<= shift
    for byte in data:
        register = table[((register >> top_shift) ^ byte) & 0xFF] ^ ((register << 8) & register_mask)
    return register >> shift


def process_register(data: bytes, width: int, poly: int, register: int, refin: bool) -> int:
    """Run ``data`` through the CRC register.

    ``register`` is in the processing domain: reflected when ``refin`` is true.
    """
    if len(data) >= _TABLE_MIN_BYTES or (width, poly, refin) in _TABLE_CACHE:
        return _process_table(data, width, poly, register, refin)
    return _process_bitwise(data, width, poly, register, refin)


def _fast_path(data: bytes, algorithm: CrcAlgorithm) -> int | None:
    if algorithm.width == 32 and algorithm.poly == 0x04C11DB7 and algorithm.refin:
        start = reflect_bits(algorithm.init, 32) ^ 0xFFFFFFFF
        register = zlib.crc32(data, start) ^ 0xFFFFFFFF
        if not algorithm.refout:
            register = reflect_bits(register, 32)
        return register ^ algorithm.xor_out
    if algorithm.width == 16 and algorithm.poly == 0x1021 and not algorithm.refin:
        register = binascii.crc_hqx(data, algorithm.init)
        if algorithm.refout:
            register = reflect_bits(register, 16)
        return register ^ algorithm.xor_out
    return None


def calculate_crc(data: bytes, algorithm: CrcAlgorithm) -> int:
    width = algorithm.width
    if width <= 0:
        raise ValueError("CRC width must be positive.")
    fast = _fast_path(data, algorithm)
    if fast is not None:
        return fast
    register = reflect_bits(algorithm.init, width) if algorithm.refin else algorithm.init
    register = process_register(data, width, algorithm.poly, register, algorithm.refin)
    if algorithm.refout != algorithm.refin:
        register = reflect_bits(register, width)
    return (register ^ algorithm.xor_out) & algorithm.mask


def calculate_crc_reference(data: bytes, algorithm: CrcAlgorithm) -> int:
    """Bit-by-bit implementation of the model, used to cross-check the fast engine."""
    width = algorithm.width
    register = reflect_bits(algorithm.init, width) if algorithm.refin else algorithm.init
    register = _process_bitwise(data, width, algorithm.poly, register, algorithm.refin)
    if algorithm.refout != algorithm.refin:
        register = reflect_bits(register, width)
    return (register ^ algorithm.xor_out) & algorithm.mask


def raw_crc(data: bytes, width: int, poly: int, refin: bool, refout: bool) -> int:
    """CRC with init=0 and xorout=0; the linear part of every CRC with these parameters."""
    register = process_register(data, width, poly, 0, refin)
    if refout != refin:
        register = reflect_bits(register, width)
    return register


def full_poly(width: int, poly: int) -> int:
    return (1 << width) | poly


def calculate_residue(algorithm: CrcAlgorithm) -> int:
    """Register contents after an error-free codeword, before the final XOR (RevEng definition)."""
    width = algorithm.width
    xor_model = reflect_bits(algorithm.xor_out, width) if algorithm.refout else algorithm.xor_out
    register = gf2.mod(xor_model << width, full_poly(width, algorithm.poly))
    return reflect_bits(register, width) if algorithm.refout else register


def message_poly(data: bytes, refin: bool) -> int:
    """The message as a polynomial in the CRC model (first transmitted bit = highest degree)."""
    return int.from_bytes(reflect_bytes(data) if refin else data, "big")


def output_to_model(value: int, width: int, refout: bool) -> int:
    return reflect_bits(value, width) if refout else value


def init_columns(width: int, poly: int, refout: bool, length: int) -> list[int]:
    """Effect of each init bit on the output for a message of ``length`` bytes.

    The output depends on init linearly: ``out = raw_crc(data) ^ Z(init) ^ xorout``
    where ``Z(init)`` is the XOR of the columns for the bits set in ``init``.
    """
    modulus = full_poly(width, poly)
    value = gf2.powmod_x(8 * length, modulus)
    top = 1 << width
    columns = []
    for _ in range(width):
        columns.append(reflect_bits(value, width) if refout else value)
        value <<= 1
        if value & top:
            value ^= modulus
    return columns


def apply_columns(columns: list[int], vector: int) -> int:
    result = 0
    index = 0
    while vector:
        if vector & 1:
            result ^= columns[index]
        vector >>= 1
        index += 1
    return result
