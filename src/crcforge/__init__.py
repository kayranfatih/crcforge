"""CRCForge: calculate, identify, reverse-engineer and forge CRCs and checksums.

Quick start::

    import crcforge

    crcforge.calculate("crc-16-modbus", b"123456789")          # 0x4B37
    crcforge.find(b"123456789", 0x4B37)                         # [CrcAlgorithm(name='crc-16-modbus', ...)]
    result = crcforge.solve([crcforge.Sample(b"...", 0x1234), ...], width=16)
    crcforge.forge(b"hello", "crc-32", 0xDEADBEEF).data         # b"hello" + 4 patch bytes
"""

from __future__ import annotations

__version__ = "1.1.0"

from .catalog import (
    FULL_CATALOG,
    Algorithm,
    UnknownAlgorithmError,
    get_algorithm,
    get_algorithms,
    validate_catalog,
)
from .checksums import (
    Adler32Checksum,
    BsdChecksum,
    FletcherChecksum,
    SumChecksum,
    SysvChecksum,
    XorChecksum,
)
from .crc import CrcAlgorithm, calculate_crc
from .forge import ForgeError, ForgeResult
from .forge import forge as _forge
from .frames import FrameLayout
from .records import RecordReport, check_records
from .solver import Sample, SolveResult
from .solver import solve as _solve


def _resolve(algorithm: str | Algorithm) -> Algorithm:
    return get_algorithm(algorithm) if isinstance(algorithm, str) else algorithm


def calculate(algorithm: str | Algorithm, data: bytes) -> int:
    """Compute a CRC or checksum. ``algorithm`` is a catalog name/alias or an algorithm object."""
    return _resolve(algorithm).compute(bytes(data))


def find(data: bytes, checksum: int, width: int | None = None, family: str | None = None) -> list[Algorithm]:
    """Catalog algorithms whose result for ``data`` equals ``checksum``."""
    return [
        algorithm
        for algorithm in get_algorithms(width=width, family=family)
        if checksum <= algorithm.mask and algorithm.compute(bytes(data)) == checksum
    ]


def solve(samples: list[Sample], width: int, **options: object) -> SolveResult:
    """Identify the CRC/checksum behind several samples (see :func:`crcforge.solver.solve`)."""
    return _solve(samples, width, **options)  # type: ignore[arg-type]


def forge(
    data: bytes,
    algorithm: str | Algorithm,
    target: int,
    offset: int | None = None,
    overwrite: bool = False,
    patch_size: int | None = None,
) -> ForgeResult:
    """Insert (default: append) or overwrite bytes so ``data`` produces ``target``."""
    return _forge(bytes(data), _resolve(algorithm), target, offset, overwrite, patch_size)


def list_algorithms(width: int | None = None, family: str | None = None, search: str | None = None) -> list[Algorithm]:
    """Catalog entries, optionally filtered by width, family ("crc"/"checksum") or a name search."""
    return get_algorithms(width=width, family=family, search=search)


__all__ = [
    "FULL_CATALOG",
    "Adler32Checksum",
    "Algorithm",
    "BsdChecksum",
    "CrcAlgorithm",
    "FletcherChecksum",
    "ForgeError",
    "ForgeResult",
    "FrameLayout",
    "RecordReport",
    "Sample",
    "SolveResult",
    "SumChecksum",
    "SysvChecksum",
    "UnknownAlgorithmError",
    "XorChecksum",
    "__version__",
    "calculate",
    "calculate_crc",
    "check_records",
    "find",
    "forge",
    "get_algorithm",
    "list_algorithms",
    "solve",
    "validate_catalog",
]
