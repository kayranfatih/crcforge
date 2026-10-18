"""Backward-compatible facade over the CRC engine, checksum families and catalog.

New code can import from ``crcforge`` directly; this module keeps the 1.0 import paths working.
"""

from __future__ import annotations

from .catalog import (
    ALGORITHMS_BY_NAME,
    CHECK_INPUT,
    FULL_CATALOG,
    Algorithm,
    UnknownAlgorithmError,
    find_matching_algorithms,
    get_algorithm,
    get_algorithms,
    normalize_name,
    validate_catalog,
)
from .catalog_crc import CRC_CATALOG
from .checksums import (
    CHECKSUM_CATALOG,
    Adler32Checksum,
    BsdChecksum,
    Checksum,
    FletcherChecksum,
    SumChecksum,
    SysvChecksum,
    XorChecksum,
)
from .crc import (
    CrcAlgorithm,
    calculate_crc,
    calculate_crc_reference,
    calculate_residue,
    format_value,
    reflect_bits,
)

ALGORITHM_CATALOG = CRC_CATALOG
format_crc = format_value


def calculate_checksum(data: bytes, algorithm: Algorithm) -> int:
    """Compute any catalog algorithm (CRC or checksum) over ``data``."""
    return algorithm.compute(data)


def describe_algorithm(algorithm: Algorithm) -> str:
    return algorithm.describe()


__all__ = [
    "ALGORITHMS_BY_NAME",
    "ALGORITHM_CATALOG",
    "CHECKSUM_CATALOG",
    "CHECK_INPUT",
    "CRC_CATALOG",
    "FULL_CATALOG",
    "Adler32Checksum",
    "Algorithm",
    "BsdChecksum",
    "Checksum",
    "CrcAlgorithm",
    "FletcherChecksum",
    "SumChecksum",
    "SysvChecksum",
    "UnknownAlgorithmError",
    "XorChecksum",
    "calculate_checksum",
    "calculate_crc",
    "calculate_crc_reference",
    "calculate_residue",
    "describe_algorithm",
    "find_matching_algorithms",
    "format_crc",
    "format_value",
    "get_algorithm",
    "get_algorithms",
    "normalize_name",
    "reflect_bits",
    "validate_catalog",
]
