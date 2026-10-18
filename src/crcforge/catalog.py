"""The built-in algorithm catalog: lookup, filtering and self-validation."""

from __future__ import annotations

import difflib
from collections.abc import Sequence

from .catalog_crc import CRC_CATALOG
from .checksums import CHECKSUM_CATALOG, Checksum
from .crc import CrcAlgorithm, calculate_residue, format_value

Algorithm = CrcAlgorithm | Checksum

FULL_CATALOG: tuple[Algorithm, ...] = (*CRC_CATALOG, *CHECKSUM_CATALOG)
FAMILIES = ("crc", "checksum")
CHECK_INPUT = b"123456789"


class UnknownAlgorithmError(KeyError):
    """Raised for an algorithm name that is not in the catalog."""

    def __init__(self, name: str, suggestions: Sequence[str] = ()) -> None:
        super().__init__(name)
        self.name = name
        self.suggestions = tuple(suggestions)

    def __str__(self) -> str:
        message = f"Unknown algorithm: {self.name}"
        if self.suggestions:
            message += f". Did you mean: {', '.join(self.suggestions)}?"
        return message


def normalize_name(name: str) -> str:
    return name.strip().lower().replace("/", "-").replace("_", "-").replace(" ", "")


def _build_lookup() -> dict[str, Algorithm]:
    lookup: dict[str, Algorithm] = {}
    for algorithm in FULL_CATALOG:
        for name in (algorithm.name, *algorithm.aliases):
            normalized = normalize_name(name)
            existing = lookup.get(normalized)
            if existing is not None and existing is not algorithm:
                raise ValueError(f"Duplicate algorithm name detected: {name}")
            lookup[normalized] = algorithm
    return lookup


ALGORITHMS_BY_NAME = _build_lookup()


def suggest_names(name: str, limit: int = 3) -> list[str]:
    normalized = normalize_name(name)
    matches = difflib.get_close_matches(normalized, ALGORITHMS_BY_NAME, n=limit * 3, cutoff=0.6)
    if not matches:
        matches = [candidate for candidate in ALGORITHMS_BY_NAME if normalized and normalized in candidate]
    primary: list[str] = []
    for match in matches:
        algorithm_name = ALGORITHMS_BY_NAME[match].name
        if algorithm_name not in primary:
            primary.append(algorithm_name)
    return primary[:limit]


def get_algorithm(name: str) -> Algorithm:
    """Look up a built-in algorithm by name or alias (case and separators are ignored)."""
    try:
        return ALGORITHMS_BY_NAME[normalize_name(name)]
    except KeyError:
        raise UnknownAlgorithmError(name, suggest_names(name)) from None


def get_algorithms(
    names: Sequence[str] | None = None,
    width: int | None = None,
    family: str | None = None,
    search: str | None = None,
) -> list[Algorithm]:
    """Select algorithms from the catalog, sorted by width and name."""
    if names:
        selected: list[Algorithm] = []
        for name in names:
            algorithm = get_algorithm(name)
            if algorithm not in selected:
                selected.append(algorithm)
    else:
        selected = list(FULL_CATALOG)

    if width is not None:
        selected = [algorithm for algorithm in selected if algorithm.width == width]
    if family is not None:
        selected = [algorithm for algorithm in selected if algorithm.family == family]
    if search:
        needle = normalize_name(search)
        selected = [
            algorithm
            for algorithm in selected
            if any(needle in normalize_name(name) for name in (algorithm.name, *algorithm.aliases))
        ]
    return sorted(selected, key=lambda algorithm: (algorithm.width, algorithm.name))


def find_matching_algorithms(
    data: bytes,
    expected: int,
    names: Sequence[str] | None = None,
    width: int | None = None,
    family: str | None = None,
) -> list[Algorithm]:
    return [
        algorithm
        for algorithm in get_algorithms(names=names, width=width, family=family)
        if expected <= algorithm.mask and algorithm.compute(data) == expected
    ]


def validate_catalog() -> list[str]:
    """Check every entry against its published check value (and residue for CRCs)."""
    failures: list[str] = []
    for algorithm in FULL_CATALOG:
        if algorithm.check is not None:
            calculated = algorithm.compute(CHECK_INPUT)
            if calculated != algorithm.check:
                failures.append(
                    f"{algorithm.name}: check expected {format_value(algorithm.check, algorithm.width)}, "
                    f"got {format_value(calculated, algorithm.width)}"
                )
        if isinstance(algorithm, CrcAlgorithm) and algorithm.residue is not None:
            residue = calculate_residue(algorithm)
            if residue != algorithm.residue:
                failures.append(
                    f"{algorithm.name}: residue expected {format_value(algorithm.residue, algorithm.width)}, "
                    f"got {format_value(residue, algorithm.width)}"
                )
    return failures
