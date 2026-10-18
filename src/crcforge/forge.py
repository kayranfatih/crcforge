"""Forge data: insert or overwrite a few bytes so the data produces a chosen checksum.

CRCs are affine over GF(2), so the patch bits come from a small linear system.
Sum and XOR checksums are solved arithmetically on a word-aligned patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import gf2
from .catalog import Algorithm
from .checksums import SumChecksum, XorChecksum
from .crc import CrcAlgorithm, calculate_crc, format_value, full_poly, reflect_bits


class ForgeError(ValueError):
    pass


MAX_SUM_PATCH_WORDS = 1024


@dataclass(frozen=True)
class ForgeResult:
    data: bytes
    patch: bytes
    offset: int
    overwrite: bool
    checksum: int


def _place(data: bytes, offset: int, patch: bytes, overwrite: bool) -> bytes:
    end = offset + len(patch) if overwrite else offset
    return data[:offset] + patch + data[end:]


def _check_offset(data: bytes, offset: int, size: int, overwrite: bool) -> None:
    if offset < 0 or offset > len(data):
        raise ForgeError(f"Offset {offset} is outside the data (0..{len(data)}).")
    if overwrite and offset + size > len(data):
        raise ForgeError(f"Cannot overwrite {size} bytes at offset {offset}: the data is only {len(data)} bytes long.")


def forge(
    data: bytes,
    algorithm: Algorithm,
    target: int,
    offset: int | None = None,
    overwrite: bool = False,
    patch_size: int | None = None,
) -> ForgeResult:
    """Return data with a patch inserted (default: appended) or overwritten so it hits ``target``."""
    if target < 0 or target > algorithm.mask:
        raise ForgeError(f"Target {target:#x} does not fit in {algorithm.width} bits.")
    if offset is None:
        offset = len(data) - (patch_size or 0) if overwrite else len(data)
    if isinstance(algorithm, CrcAlgorithm):
        result = _forge_crc(data, algorithm, target, offset, overwrite, patch_size)
    elif isinstance(algorithm, SumChecksum):
        result = _forge_sum(data, algorithm, target, offset, overwrite, patch_size)
    elif isinstance(algorithm, XorChecksum):
        result = _forge_xor(data, algorithm, target, offset, overwrite, patch_size)
    else:
        raise ForgeError(f"Forging is supported for CRCs and sum/XOR checksums; {algorithm.name} is not supported.")
    if algorithm.compute(result.data) != target:  # pragma: no cover - guards the math
        raise ForgeError("Internal error: forged data does not produce the target checksum.")
    return result


def _forge_crc(
    data: bytes, algorithm: CrcAlgorithm, target: int, offset: int, overwrite: bool, patch_size: int | None
) -> ForgeResult:
    width = algorithm.width
    size = patch_size or (width + 7) // 8
    if size * 8 < width:
        raise ForgeError(f"A {width}-bit CRC needs a patch of at least {(width + 7) // 8} bytes.")
    _check_offset(data, offset, size, overwrite)
    base = _place(data, offset, bytes(size), overwrite)
    total = len(base)
    difference = target ^ calculate_crc(base, algorithm)
    modulus = full_poly(width, algorithm.poly)

    # Column for each patch bit: its effect on the output (linear part only).
    columns = []
    for index in range(size):
        position = offset + index
        for bit in range(8):
            model_bit = 7 - bit if algorithm.refin else bit
            degree = 8 * (total - 1 - position) + model_bit + width
            value = gf2.powmod_x(degree, modulus)
            columns.append(reflect_bits(value, width) if algorithm.refout else value)

    variables = 8 * size
    rows = []
    for output_bit in range(width):
        coefficients = 0
        for variable, column in enumerate(columns):
            if (column >> output_bit) & 1:
                coefficients |= 1 << variable
        rows.append(coefficients | (((difference >> output_bit) & 1) << variables))
    solved = gf2.solve_linear(rows, variables)
    if solved is None:
        raise ForgeError(
            "These patch bytes cannot reach the target for this CRC; try a different --at offset or a larger "
            "--patch-size."
        )
    bits = solved[0]
    patch = bytes((bits >> (8 * index)) & 0xFF for index in range(size))
    return ForgeResult(_place(data, offset, patch, overwrite), patch, offset, overwrite, target)


def _alignment(data: bytes, offset: int, word_size: int, overwrite: bool) -> int:
    """Zero bytes to put in front of the patch so it starts on a word boundary."""
    if offset % word_size == 0:
        return 0
    if not overwrite and offset == len(data):
        return -offset % word_size
    raise ForgeError(f"The patch must start on a {word_size}-byte word boundary for this checksum.")


def _forge_sum(
    data: bytes, algorithm: SumChecksum, target: int, offset: int, overwrite: bool, patch_size: int | None
) -> ForgeResult:
    word_size = algorithm.word_size
    width = algorithm.width
    mask = algorithm.mask
    pad = _alignment(data, offset, word_size, overwrite)
    if algorithm.complement == "ones":
        wanted = ~target & mask
    elif algorithm.complement == "twos":
        wanted = -target & mask
    else:
        wanted = target

    word_max = (1 << (8 * word_size)) - 1
    if patch_size is not None and (patch_size - pad) % word_size:
        raise ForgeError(f"--patch-size must cover whole {word_size}-byte words.")
    fixed_words = None if patch_size is None else (patch_size - pad) // word_size
    if fixed_words is not None and fixed_words <= 0:
        raise ForgeError("--patch-size is too small for this checksum.")
    if algorithm.carry == "end-around" and wanted == 0:
        raise ForgeError(
            f"{algorithm.name} cannot produce {format_value(target, width)}: a one's complement sum is zero only "
            "when every word is zero."
        )

    def needed_for(word_count: int) -> int:
        _check_offset(data, offset, pad + word_count * word_size, overwrite)
        raw = algorithm.raw_sum(_place(data, offset, bytes(pad + word_count * word_size), overwrite))
        if algorithm.carry == "end-around":
            needed = (wanted - raw) % mask
            if needed == 0 and algorithm.accumulate(raw) != wanted:
                needed = mask
            return needed
        return (wanted - raw) % (1 << width)

    words = fixed_words or 1
    needed = needed_for(words)
    if fixed_words is None:
        # Overwriting more words also removes more original data, so iterate to a fixed point.
        for _ in range(64):
            required = max(1, -(-needed // word_max))
            if required <= words:
                break
            words = required
            needed = needed_for(words)
    if fixed_words is None and words > MAX_SUM_PATCH_WORDS:
        raise ForgeError(
            f"Reaching {format_value(target, width)} with {algorithm.name} would need a {words * word_size}-byte "
            f"patch: each {word_size * 8}-bit word adds at most {word_max:#x} to a {width}-bit sum. "
            "Pick a target closer to the current checksum or a checksum with wider words."
        )
    if needed > words * word_max:
        raise ForgeError(f"A {words}-word patch cannot add {needed:#x}; pass a larger --patch-size or omit it.")
    values = []
    remaining = needed
    for _ in range(words):
        value = min(remaining, word_max)
        values.append(value)
        remaining -= value
    byteorder: Literal["big", "little"] = "little" if algorithm.endian == "little" else "big"
    patch = bytes(pad) + b"".join(value.to_bytes(word_size, byteorder) for value in values)
    if overwrite:
        _check_offset(data, offset, len(patch), overwrite)
    forged = _place(data, offset, patch, overwrite)
    if algorithm.compute(forged) != target:
        raise ForgeError(f"Cannot reach {format_value(target, width)} with this patch position for {algorithm.name}.")
    return ForgeResult(forged, patch, offset, overwrite, target)


def _forge_xor(
    data: bytes, algorithm: XorChecksum, target: int, offset: int, overwrite: bool, patch_size: int | None
) -> ForgeResult:
    word_size = algorithm.width // 8
    pad = _alignment(data, offset, word_size, overwrite)
    if patch_size is not None and patch_size != pad + word_size:
        raise ForgeError(f"XOR checksums use a {pad + word_size}-byte patch here.")
    _check_offset(data, offset, pad + word_size, overwrite)
    base = _place(data, offset, bytes(pad + word_size), overwrite)
    patch = bytes(pad) + (target ^ algorithm.raw_xor(base)).to_bytes(word_size, "big")
    return ForgeResult(_place(data, offset, patch, overwrite), patch, offset, overwrite, target)
