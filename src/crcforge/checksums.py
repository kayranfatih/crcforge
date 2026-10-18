"""Non-CRC checksum families: sums, one's/two's complement, XOR, Fletcher, Adler, BSD and SysV."""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Literal

from .crc import format_value

CARRY_MODES = ("discard", "end-around")
COMPLEMENT_MODES = ("none", "ones", "twos")
ENDIAN_MODES = ("big", "little")


def _words(data: bytes, word_size: int, endian: str) -> list[int]:
    padded = data + bytes(-len(data) % word_size)
    if word_size == 1:
        return list(padded)
    byteorder: Literal["big", "little"] = "big" if endian == "big" else "little"
    return [int.from_bytes(padded[index : index + word_size], byteorder) for index in range(0, len(padded), word_size)]


class _ChecksumBase:
    name: str
    width: int
    aliases: tuple[str, ...]
    check: int | None

    family = "checksum"
    residue = None
    status = None

    @property
    def mask(self) -> int:
        return (1 << self.width) - 1


@dataclass(frozen=True)
class SumChecksum(_ChecksumBase):
    """Sum of ``word_size``-byte words into a ``width``-bit accumulator, optionally complemented.

    ``carry="end-around"`` folds carries back into the sum (one's complement arithmetic,
    RFC 1071); ``"discard"`` keeps the sum modulo 2**width. ``complement`` is applied last:
    ``"ones"`` inverts every bit, ``"twos"`` negates so that ``sum + checksum == 0``.
    """

    name: str
    width: int
    word_size: int = 1
    endian: str = "big"
    carry: str = "discard"
    complement: str = "none"
    aliases: tuple[str, ...] = ()
    check: int | None = None

    def __post_init__(self) -> None:
        if self.width <= 0 or self.width % 8 != 0:
            raise ValueError("Sum checksum width must be a positive multiple of 8.")
        if self.word_size <= 0 or self.word_size * 8 > self.width:
            raise ValueError("Word size must be between 1 byte and the checksum width.")
        if self.endian not in ENDIAN_MODES:
            raise ValueError(f"Endian must be one of: {', '.join(ENDIAN_MODES)}.")
        if self.carry not in CARRY_MODES:
            raise ValueError(f"Carry must be one of: {', '.join(CARRY_MODES)}.")
        if self.complement not in COMPLEMENT_MODES:
            raise ValueError(f"Complement must be one of: {', '.join(COMPLEMENT_MODES)}.")

    def accumulate(self, total: int) -> int:
        """Reduce a plain integer sum to the accumulator width."""
        mask = self.mask
        if self.carry == "end-around":
            # Deferred carry folding gives the same result as folding after every word.
            while total > mask:
                total = (total & mask) + (total >> self.width)
            return total
        return total & mask

    def finalize(self, accumulator: int) -> int:
        if self.complement == "ones":
            return ~accumulator & self.mask
        if self.complement == "twos":
            return -accumulator & self.mask
        return accumulator

    def raw_sum(self, data: bytes) -> int:
        return sum(_words(data, self.word_size, self.endian))

    def compute(self, data: bytes) -> int:
        return self.finalize(self.accumulate(self.raw_sum(data)))

    def describe(self) -> str:
        parts = [f"{self.name}: width={self.width}", f"words={self.word_size * 8}-bit"]
        if self.word_size > 1:
            parts.append(f"endian={self.endian}")
        parts.append(f"carry={self.carry}")
        parts.append(f"complement={self.complement}")
        return ", ".join(parts)

    def params(self) -> dict[str, object]:
        return {
            "type": "sum",
            "width": self.width,
            "word_bits": self.word_size * 8,
            "endian": self.endian,
            "carry": self.carry,
            "complement": self.complement,
        }


@dataclass(frozen=True)
class XorChecksum(_ChecksumBase):
    """XOR of big-endian ``width``-bit words (BCC / XOR-LRC)."""

    name: str
    width: int
    aliases: tuple[str, ...] = ()
    check: int | None = None

    def raw_xor(self, data: bytes) -> int:
        result = 0
        for word in _words(data, self.width // 8, "big"):
            result ^= word
        return result

    def compute(self, data: bytes) -> int:
        return self.raw_xor(data)

    def describe(self) -> str:
        return f"{self.name}: width={self.width}, words={self.width}-bit big-endian, xor"

    def params(self) -> dict[str, object]:
        return {"type": "xor", "width": self.width}


@dataclass(frozen=True)
class FletcherChecksum(_ChecksumBase):
    """Fletcher checksum over little-endian ``width/2``-bit blocks (matches the usual reference code)."""

    name: str
    width: int
    aliases: tuple[str, ...] = ()
    check: int | None = None

    def compute(self, data: bytes) -> int:
        half = self.width // 2
        modulus = (1 << half) - 1
        first = second = 0
        for word in _words(data, half // 8, "little"):
            first = (first + word) % modulus
            second = (second + first) % modulus
        return (second << half) | first

    def describe(self) -> str:
        block = self.width // 2
        return f"{self.name}: width={self.width}, blocks={block}-bit little-endian, modulus=2^{block}-1"

    def params(self) -> dict[str, object]:
        return {"type": "fletcher", "width": self.width}


@dataclass(frozen=True)
class Adler32Checksum(_ChecksumBase):
    name: str
    width: int = 32
    aliases: tuple[str, ...] = ()
    check: int | None = None

    def compute(self, data: bytes) -> int:
        return zlib.adler32(data) & 0xFFFFFFFF

    def describe(self) -> str:
        return f"{self.name}: width=32, modulus=65521 (zlib)"

    def params(self) -> dict[str, object]:
        return {"type": "adler", "width": 32}


@dataclass(frozen=True)
class BsdChecksum(_ChecksumBase):
    """16-bit BSD checksum: rotate right by one, then add the next byte (``sum -r``)."""

    name: str
    width: int = 16
    aliases: tuple[str, ...] = ()
    check: int | None = None

    def compute(self, data: bytes) -> int:
        checksum = 0
        for byte in data:
            checksum = ((checksum >> 1) | ((checksum & 1) << 15)) + byte
            checksum &= 0xFFFF
        return checksum

    def describe(self) -> str:
        return f"{self.name}: width=16, rotate-right-and-add (sum -r)"

    def params(self) -> dict[str, object]:
        return {"type": "bsd", "width": 16}


@dataclass(frozen=True)
class SysvChecksum(_ChecksumBase):
    """16-bit System V checksum (``sum -s``)."""

    name: str
    width: int = 16
    aliases: tuple[str, ...] = ()
    check: int | None = None

    def compute(self, data: bytes) -> int:
        total = sum(data) & 0xFFFFFFFF
        folded = (total & 0xFFFF) + (total >> 16)
        return ((folded & 0xFFFF) + (folded >> 16)) & 0xFFFF

    def describe(self) -> str:
        return f"{self.name}: width=16, byte sum folded to 16 bits (sum -s)"

    def params(self) -> dict[str, object]:
        return {"type": "sysv", "width": 16}


Checksum = SumChecksum | XorChecksum | FletcherChecksum | Adler32Checksum | BsdChecksum | SysvChecksum


def describe_value(value: int, width: int) -> str:
    return format_value(value, width)


CHECKSUM_CATALOG: tuple[Checksum, ...] = (
    SumChecksum("sum-8", 8, aliases=("checksum-8", "modulo-256"), check=0xDD),
    SumChecksum("sum-16", 16, aliases=("checksum-16",), check=0x01DD),
    SumChecksum("sum-32", 32, aliases=("checksum-32",), check=0x000001DD),
    SumChecksum("sum-16-words", 16, word_size=2, check=0x09D4),
    SumChecksum("sum-32-words", 32, word_size=4, check=0x9F686A6C),
    SumChecksum(
        "ones-complement-8",
        8,
        carry="end-around",
        complement="ones",
        aliases=("1s-complement-8",),
        check=0x21,
    ),
    SumChecksum(
        "ones-complement-8-no-carry",
        8,
        complement="ones",
        aliases=("motorola-srec", "s-record", "srec"),
        check=0x22,
    ),
    SumChecksum(
        "ones-complement-16",
        16,
        word_size=2,
        carry="end-around",
        complement="ones",
        aliases=("1s-complement-16", "internet-checksum", "inet-checksum", "ip-checksum", "rfc-1071"),
        check=0xF62A,
    ),
    SumChecksum(
        "ones-complement-32",
        32,
        word_size=4,
        carry="end-around",
        complement="ones",
        aliases=("1s-complement-32",),
        check=0x60979593,
    ),
    SumChecksum(
        "twos-complement-8",
        8,
        complement="twos",
        aliases=("2s-complement-8", "intel-hex", "ihex", "modbus-lrc"),
        check=0x23,
    ),
    SumChecksum(
        "twos-complement-16",
        16,
        word_size=2,
        complement="twos",
        aliases=("2s-complement-16",),
        check=0xF62C,
    ),
    SumChecksum(
        "twos-complement-32",
        32,
        word_size=4,
        complement="twos",
        aliases=("2s-complement-32",),
        check=0x60979594,
    ),
    XorChecksum("xor-8", 8, aliases=("xor8", "bcc", "lrc-xor"), check=0x31),
    XorChecksum("xor-16", 16, check=0x3908),
    XorChecksum("xor-32", 32, check=0x3D04040C),
    FletcherChecksum("fletcher-16", 16, check=0x1EDE),
    FletcherChecksum("fletcher-32", 32, check=0xDF09D509),
    FletcherChecksum("fletcher-64", 64, check=0x0D0803376C6A689F),
    Adler32Checksum("adler-32", aliases=("adler32", "zlib-adler32"), check=0x091E01DE),
    BsdChecksum("bsd-16", aliases=("bsd-sum", "sum-bsd"), check=0xD16F),
    SysvChecksum("sysv-16", aliases=("sysv-sum", "sum-sysv"), check=0x01DD),
)
