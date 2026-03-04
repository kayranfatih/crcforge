from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CrcAlgorithm:
    name: str
    width: int
    poly: int
    init: int
    xor_out: int
    refin: bool
    refout: bool
    aliases: Tuple[str, ...] = ()
    check: Optional[int] = None

    @property
    def mask(self) -> int:
        return (1 << self.width) - 1


def reflect_bits(value: int, width: int) -> int:
    reflected = 0
    for index in range(width):
        if value & (1 << index):
            reflected |= 1 << (width - 1 - index)
    return reflected


def calculate_crc(data: bytes, algorithm: CrcAlgorithm) -> int:
    if algorithm.width <= 0:
        raise ValueError("CRC width must be positive.")

    crc = algorithm.init & algorithm.mask
    mask = algorithm.mask

    if algorithm.refin:
        poly = reflect_bits(algorithm.poly, algorithm.width)
        for byte in data:
            for bit_index in range(8):
                incoming_bit = (byte >> bit_index) & 0x1
                low_bit = crc & 0x1
                crc >>= 1
                if low_bit ^ incoming_bit:
                    crc ^= poly
                crc &= mask
    else:
        top_shift = algorithm.width - 1
        for byte in data:
            for bit_index in range(8):
                incoming_bit = (byte >> (7 - bit_index)) & 0x1
                top_bit = (crc >> top_shift) & 0x1
                crc = (crc << 1) & mask
                if top_bit ^ incoming_bit:
                    crc ^= algorithm.poly
                crc &= mask

    if algorithm.refout != algorithm.refin:
        crc = reflect_bits(crc, algorithm.width)

    return (crc ^ algorithm.xor_out) & mask


def format_crc(value: int, width: int) -> str:
    digits = max(1, (width + 3) // 4)
    return f"0x{value:0{digits}X}"


ALGORITHM_CATALOG: Tuple[CrcAlgorithm, ...] = (
    CrcAlgorithm(
        "crc-5-epc-c1g2",
        5,
        0x09,
        0x09,
        0x00,
        False,
        False,
        ("epc-c1g2", "crc-5-epc"),
        0x00,
    ),
    CrcAlgorithm(
        "crc-5-usb",
        5,
        0x05,
        0x1F,
        0x1F,
        True,
        True,
        ("usb-5",),
        0x19,
    ),
    CrcAlgorithm(
        "crc-6-cdma2000-a",
        6,
        0x27,
        0x3F,
        0x00,
        False,
        False,
        ("cdma2000-a",),
        0x0D,
    ),
    CrcAlgorithm(
        "crc-6-darc",
        6,
        0x19,
        0x00,
        0x00,
        True,
        True,
        ("darc-6",),
        0x26,
    ),
    CrcAlgorithm(
        "crc-8",
        8,
        0x07,
        0x00,
        0x00,
        False,
        False,
        ("crc8", "crc-8-smbus", "smbus"),
        0xF4,
    ),
    CrcAlgorithm(
        "crc-8-autosar",
        8,
        0x2F,
        0xFF,
        0xFF,
        False,
        False,
        ("autosar-8", "autosar"),
        0xDF,
    ),
    CrcAlgorithm(
        "crc-8-bluetooth",
        8,
        0xA7,
        0x00,
        0x00,
        True,
        True,
        ("bluetooth",),
        0x26,
    ),
    CrcAlgorithm(
        "crc-8-hitag",
        8,
        0x1D,
        0xFF,
        0x00,
        False,
        False,
        ("hitag",),
        0xB4,
    ),
    CrcAlgorithm(
        "crc-8-maxim-dow",
        8,
        0x31,
        0x00,
        0x00,
        True,
        True,
        ("crc-8-maxim", "dallas-1-wire"),
        0xA1,
    ),
    CrcAlgorithm(
        "crc-8-mifare-mad",
        8,
        0x1D,
        0xC7,
        0x00,
        False,
        False,
        ("mifare-mad",),
        0x99,
    ),
    CrcAlgorithm(
        "crc-8-sae-j1850",
        8,
        0x1D,
        0xFF,
        0xFF,
        False,
        False,
        ("j1850", "sae-j1850"),
        0x4B,
    ),
    CrcAlgorithm(
        "crc-10-atm",
        10,
        0x233,
        0x000,
        0x000,
        False,
        False,
        ("atm-10",),
        0x199,
    ),
    CrcAlgorithm(
        "crc-13-bbc",
        13,
        0x1CF5,
        0x0000,
        0x0000,
        False,
        False,
        ("bbc",),
        0x04FA,
    ),
    CrcAlgorithm(
        "crc-14-darc",
        14,
        0x0805,
        0x0000,
        0x0000,
        True,
        True,
        ("darc-14",),
        0x082D,
    ),
    CrcAlgorithm(
        "crc-15-mpt1327",
        15,
        0x6815,
        0x0000,
        0x0001,
        False,
        False,
        ("mpt1327",),
        0x2566,
    ),
    CrcAlgorithm(
        "crc-16-arc",
        16,
        0x8005,
        0x0000,
        0x0000,
        True,
        True,
        ("crc-16", "crc-16-ibm", "arc", "ibm"),
        0xBB3D,
    ),
    CrcAlgorithm(
        "crc-16-ccitt-false",
        16,
        0x1021,
        0xFFFF,
        0x0000,
        False,
        False,
        ("ccitt-false", "crc-16-ibm-3740", "ibm-3740", "crc-16-autosar", "autosar-16"),
        0x29B1,
    ),
    CrcAlgorithm(
        "crc-16-dect-r",
        16,
        0x0589,
        0x0000,
        0x0001,
        False,
        False,
        ("dect-r",),
        0x007E,
    ),
    CrcAlgorithm(
        "crc-16-dect-x",
        16,
        0x0589,
        0x0000,
        0x0000,
        False,
        False,
        ("dect-x",),
        0x007F,
    ),
    CrcAlgorithm(
        "crc-16-dnp",
        16,
        0x3D65,
        0x0000,
        0xFFFF,
        True,
        True,
        ("dnp",),
        0xEA82,
    ),
    CrcAlgorithm(
        "crc-16-en-13757",
        16,
        0x3D65,
        0x0000,
        0xFFFF,
        False,
        False,
        ("en-13757",),
        0xC2B7,
    ),
    CrcAlgorithm(
        "crc-16-genibus",
        16,
        0x1021,
        0xFFFF,
        0xFFFF,
        False,
        False,
        ("genibus",),
        0xD64E,
    ),
    CrcAlgorithm(
        "crc-16-kermit",
        16,
        0x1021,
        0x0000,
        0x0000,
        True,
        True,
        ("kermit",),
        0x2189,
    ),
    CrcAlgorithm(
        "crc-16-m17",
        16,
        0x5935,
        0xFFFF,
        0x0000,
        False,
        False,
        ("m17",),
        0x772B,
    ),
    CrcAlgorithm(
        "crc-16-maxim-dow",
        16,
        0x8005,
        0x0000,
        0xFFFF,
        True,
        True,
        ("crc-16-maxim", "maxim-16"),
        0x44C2,
    ),
    CrcAlgorithm(
        "crc-16-modbus",
        16,
        0x8005,
        0xFFFF,
        0x0000,
        True,
        True,
        ("modbus",),
        0x4B37,
    ),
    CrcAlgorithm(
        "crc-16-nrsc-5",
        16,
        0x080B,
        0xFFFF,
        0x0000,
        True,
        True,
        ("nrsc-5",),
        0xA066,
    ),
    CrcAlgorithm(
        "crc-16-opensafety-a",
        16,
        0x5935,
        0x0000,
        0x0000,
        False,
        False,
        ("opensafety-a",),
        0x5D38,
    ),
    CrcAlgorithm(
        "crc-16-opensafety-b",
        16,
        0x755B,
        0x0000,
        0x0000,
        False,
        False,
        ("opensafety-b",),
        0x20FE,
    ),
    CrcAlgorithm(
        "crc-16-profibus",
        16,
        0x1DCF,
        0xFFFF,
        0xFFFF,
        False,
        False,
        ("profibus",),
        0xA819,
    ),
    CrcAlgorithm(
        "crc-16-spi-fujitsu",
        16,
        0x1021,
        0x1D0F,
        0x0000,
        False,
        False,
        ("spi-fujitsu", "aug-ccitt"),
        0xE5CC,
    ),
    CrcAlgorithm(
        "crc-16-t10-dif",
        16,
        0x8BB7,
        0x0000,
        0x0000,
        False,
        False,
        ("t10-dif",),
        0xD0DB,
    ),
    CrcAlgorithm(
        "crc-16-tms37157",
        16,
        0x1021,
        0x3791,
        0x0000,
        True,
        True,
        ("tms37157",),
        0x26B1,
    ),
    CrcAlgorithm(
        "crc-16-umts",
        16,
        0x8005,
        0x0000,
        0x0000,
        False,
        False,
        ("umts", "crc-16-buypass", "buypass"),
        0xFEE8,
    ),
    CrcAlgorithm(
        "crc-16-usb",
        16,
        0x8005,
        0xFFFF,
        0xFFFF,
        True,
        True,
        ("usb",),
        0xB4C8,
    ),
    CrcAlgorithm(
        "crc-16-x25",
        16,
        0x1021,
        0xFFFF,
        0xFFFF,
        True,
        True,
        ("x-25", "x25", "crc-16-ibm-sdlc", "ibm-sdlc", "iso-hdlc-16"),
        0x906E,
    ),
    CrcAlgorithm(
        "crc-16-xmodem",
        16,
        0x1021,
        0x0000,
        0x0000,
        False,
        False,
        ("xmodem",),
        0x31C3,
    ),
    CrcAlgorithm(
        "crc-24-openpgp",
        24,
        0x864CFB,
        0xB704CE,
        0x000000,
        False,
        False,
        ("openpgp",),
        0x21CF02,
    ),
    CrcAlgorithm(
        "crc-24-os-9",
        24,
        0x800063,
        0xFFFFFF,
        0xFFFFFF,
        False,
        False,
        ("os-9",),
        0x200FA5,
    ),
    CrcAlgorithm(
        "crc-32",
        32,
        0x04C11DB7,
        0xFFFFFFFF,
        0xFFFFFFFF,
        True,
        True,
        ("iso-hdlc", "adccp", "v-42", "ethernet"),
        0xCBF43926,
    ),
    CrcAlgorithm(
        "crc-32-autosar",
        32,
        0xF4ACFB13,
        0xFFFFFFFF,
        0xFFFFFFFF,
        True,
        True,
        ("autosar-32",),
        0x1697D06A,
    ),
    CrcAlgorithm(
        "crc-32-base91-d",
        32,
        0xA833982B,
        0xFFFFFFFF,
        0xFFFFFFFF,
        True,
        True,
        ("base91-d",),
        0x87315576,
    ),
    CrcAlgorithm(
        "crc-32-cksum",
        32,
        0x04C11DB7,
        0x00000000,
        0xFFFFFFFF,
        False,
        False,
        ("cksum", "posix"),
        0x765E7680,
    ),
    CrcAlgorithm(
        "crc-32-bzip2",
        32,
        0x04C11DB7,
        0xFFFFFFFF,
        0xFFFFFFFF,
        False,
        False,
        ("bzip2",),
        0xFC891918,
    ),
    CrcAlgorithm(
        "crc-32c",
        32,
        0x1EDC6F41,
        0xFFFFFFFF,
        0xFFFFFFFF,
        True,
        True,
        ("castagnoli", "crc-32-iscsi", "iscsi"),
        0xE3069283,
    ),
    CrcAlgorithm(
        "crc-32-jamcrc",
        32,
        0x04C11DB7,
        0xFFFFFFFF,
        0x00000000,
        True,
        True,
        ("jamcrc",),
        0x340BC6D9,
    ),
    CrcAlgorithm(
        "crc-32-mpeg-2",
        32,
        0x04C11DB7,
        0xFFFFFFFF,
        0x00000000,
        False,
        False,
        ("mpeg-2",),
        0x0376E6E7,
    ),
    CrcAlgorithm(
        "crc-32q",
        32,
        0x814141AB,
        0x00000000,
        0x00000000,
        False,
        False,
        ("32q", "crc-32-aixm", "aixm"),
        0x3010BF7F,
    ),
    CrcAlgorithm(
        "crc-32-xfer",
        32,
        0x000000AF,
        0x00000000,
        0x00000000,
        False,
        False,
        ("xfer",),
        0xBD0BE338,
    ),
    CrcAlgorithm(
        "crc-64-ecma-182",
        64,
        0x42F0E1EBA9EA3693,
        0x0000000000000000,
        0x0000000000000000,
        False,
        False,
        ("ecma-182",),
        0x6C40DF5F0B497347,
    ),
    CrcAlgorithm(
        "crc-64-we",
        64,
        0x42F0E1EBA9EA3693,
        0xFFFFFFFFFFFFFFFF,
        0xFFFFFFFFFFFFFFFF,
        False,
        False,
        ("we",),
        0x62EC59E3F1A4F00A,
    ),
    CrcAlgorithm(
        "crc-64-go-iso",
        64,
        0x000000000000001B,
        0xFFFFFFFFFFFFFFFF,
        0xFFFFFFFFFFFFFFFF,
        True,
        True,
        ("go-iso",),
        0xB90956C775A41001,
    ),
    CrcAlgorithm(
        "crc-64-nvme",
        64,
        0xAD93D23594C93659,
        0xFFFFFFFFFFFFFFFF,
        0xFFFFFFFFFFFFFFFF,
        True,
        True,
        ("nvme",),
        0xAE8B14860A799888,
    ),
    CrcAlgorithm(
        "crc-64-xz",
        64,
        0x42F0E1EBA9EA3693,
        0xFFFFFFFFFFFFFFFF,
        0xFFFFFFFFFFFFFFFF,
        True,
        True,
        ("xz",),
        0x995DC9BBDF1939FA,
    ),
    CrcAlgorithm(
        "crc-82-darc",
        82,
        0x0308C0111011401440411,
        0x000000000000000000000,
        0x000000000000000000000,
        True,
        True,
        ("darc-82",),
        0x09EA83F625023801FD612,
    ),
)


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "")


def _build_lookup() -> Dict[str, CrcAlgorithm]:
    lookup: Dict[str, CrcAlgorithm] = {}
    for algorithm in ALGORITHM_CATALOG:
        names = (algorithm.name,) + algorithm.aliases
        for name in names:
            normalized = _normalize_name(name)
            if normalized in lookup and lookup[normalized] != algorithm:
                raise ValueError(f"Duplicate CRC alias detected: {name}")
            lookup[normalized] = algorithm
    return lookup


ALGORITHMS_BY_NAME = _build_lookup()


def get_algorithm(name: str) -> CrcAlgorithm:
    normalized = _normalize_name(name)
    try:
        return ALGORITHMS_BY_NAME[normalized]
    except KeyError as error:
        raise KeyError(f"Unknown CRC algorithm: {name}") from error


def get_algorithms(
    names: Optional[Sequence[str]] = None,
    width: Optional[int] = None,
) -> List[CrcAlgorithm]:
    if names:
        selected: List[CrcAlgorithm] = []
        seen = set()
        for name in names:
            algorithm = get_algorithm(name)
            if algorithm.name not in seen:
                selected.append(algorithm)
                seen.add(algorithm.name)
    else:
        selected = list(ALGORITHM_CATALOG)

    if width is not None:
        selected = [algorithm for algorithm in selected if algorithm.width == width]

    return sorted(selected, key=lambda algorithm: (algorithm.width, algorithm.name))


def find_matching_algorithms(
    data: bytes,
    expected_crc: int,
    names: Optional[Sequence[str]] = None,
    width: Optional[int] = None,
) -> List[CrcAlgorithm]:
    matches: List[CrcAlgorithm] = []
    for algorithm in get_algorithms(names=names, width=width):
        if expected_crc > algorithm.mask:
            continue
        if calculate_crc(data, algorithm) == expected_crc:
            matches.append(algorithm)
    return matches


def describe_algorithm(algorithm: CrcAlgorithm) -> str:
    return (
        f"{algorithm.name}: width={algorithm.width}, "
        f"poly={format_crc(algorithm.poly, algorithm.width)}, "
        f"init={format_crc(algorithm.init, algorithm.width)}, "
        f"xor_out={format_crc(algorithm.xor_out, algorithm.width)}, "
        f"refin={algorithm.refin}, refout={algorithm.refout}"
    )


def validate_catalog() -> List[str]:
    failures: List[str] = []
    probe = b"123456789"
    for algorithm in ALGORITHM_CATALOG:
        if algorithm.check is None:
            continue
        calculated = calculate_crc(probe, algorithm)
        if calculated != algorithm.check:
            failures.append(
                f"{algorithm.name}: expected {format_crc(algorithm.check, algorithm.width)}, "
                f"got {format_crc(calculated, algorithm.width)}"
            )
    return failures
