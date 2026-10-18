"""Recover CRC parameters from data/checksum samples.

A CRC is affine over GF(2): for fixed width, poly, refin and refout,
``crc(data) = raw(data) ^ Z_len(init) ^ xorout`` where ``raw`` is the CRC with
init=0/xorout=0 and ``Z_len`` is a linear map that depends only on the message length.

* Two samples of the same length cancel init and xorout, so the polynomial divides
  ``(M1 ^ M2) * x^width + (C1 ^ C2)``. The GCD over several pairs pins it down without
  scanning (this is how RevEng's search works).
* With the polynomial known, init and xorout follow from a linear system.
* Single-sample searches never scan xorout: it is solved directly.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field

from . import gf2
from .byteorder import distinct_views
from .catalog import Algorithm, get_algorithms
from .checksums import ENDIAN_MODES, SumChecksum, XorChecksum
from .crc import CrcAlgorithm, apply_columns, init_columns, message_poly, output_to_model, raw_crc

ENUMERATE_LIMIT = 4096
# Up to 2^4 equivalent init/xorout pairs are listed in full; larger ambiguities get a representative.
SMALL_AMBIGUITY_BITS = 4
REFLECTION_COMBOS = ((False, False), (True, True), (False, True), (True, False))


@dataclass(frozen=True)
class Sample:
    data: bytes
    checksum: int


@dataclass(frozen=True)
class InitXorSolutions:
    pairs: tuple[tuple[int, int], ...]
    free_bits: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class CrcMatch:
    algorithm: CrcAlgorithm
    input_mode: str = "native"
    crc_mode: str = "as-given"
    catalog_name: str | None = None
    free_bits: int = 0


@dataclass(frozen=True)
class CatalogMatch:
    algorithm: Algorithm
    input_mode: str = "native"
    crc_mode: str = "as-given"


@dataclass
class SolveResult:
    width: int
    sample_count: int
    lengths: tuple[int, ...]
    catalog_matches: list[CatalogMatch] = field(default_factory=list)
    crc_matches: list[CrcMatch] = field(default_factory=list)
    checksum_matches: list[CatalogMatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    derived_notes: list[str] = field(default_factory=list)
    skipped_modes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.catalog_matches or self.crc_matches or self.checksum_matches)


def swap_value_bytes(value: int, width: int) -> int | None:
    """Byte-swap a checksum value; None when it has a single byte or no whole-byte width."""
    if width <= 8 or width % 8 != 0:
        return None
    byte_length = width // 8
    return int.from_bytes(value.to_bytes(byte_length, "big"), "little")


def checksum_variants(value: int, width: int) -> list[tuple[str, int]]:
    variants = [("as-given", value)]
    swapped = swap_value_bytes(value, width)
    if swapped is not None and swapped != value:
        variants.append(("byte-swapped", swapped))
    return variants


def sample_variants(samples: Sequence[Sample], width: int) -> list[tuple[str, list[Sample]]]:
    """The samples as given, plus a copy with every checksum byte-swapped."""
    variants = [("as-given", list(samples))]
    swapped = []
    for sample in samples:
        value = swap_value_bytes(sample.checksum, width)
        if value is None:
            return variants
        swapped.append(Sample(sample.data, value))
    if any(a.checksum != b.checksum for a, b in zip(samples, swapped, strict=True)):
        variants.append(("byte-swapped", swapped))
    return variants


def solve_init_xorout(
    samples: Sequence[Sample],
    width: int,
    poly: int,
    refin: bool,
    refout: bool,
    init_values: Sequence[int] | None = None,
    xorout_values: Sequence[int] | None = None,
    limit: int = 64,
) -> InitXorSolutions | None:
    """Find init/xorout pairs that make the given CRC parameters fit every sample.

    ``None`` means no pair fits. ``free_bits`` > 0 means init and xorout are not fully
    determined by the samples (for example when every sample has the same length).
    """
    mask = (1 << width) - 1
    columns_by_length: dict[int, list[int]] = {}
    terms: list[tuple[list[int], int]] = []
    for sample in samples:
        length = len(sample.data)
        columns = columns_by_length.get(length)
        if columns is None:
            columns = init_columns(width, poly, refout, length)
            columns_by_length[length] = columns
        terms.append((columns, sample.checksum ^ raw_crc(sample.data, width, poly, refin, refout)))

    init_set = None if init_values is None else set(init_values)
    xorout_set = None if xorout_values is None else set(xorout_values)

    if init_values is not None and len(init_values) <= ENUMERATE_LIMIT:
        return _enumerate_init(terms, init_values, xorout_set, limit)

    if xorout_values is not None and len(xorout_values) <= ENUMERATE_LIMIT:
        # Every sample gives Z_i·init = rhs_i ^ xorout; stack the samples into tall columns.
        stacked = [0] * width
        for index, (columns, _) in enumerate(terms):
            for bit, column in enumerate(columns):
                stacked[bit] |= column << (index * width)
        solver = gf2.ColumnSolver(stacked)
        pairs: list[tuple[int, int]] = []
        for xorout in xorout_values:
            target = 0
            for index, (_, rhs) in enumerate(terms):
                target |= (rhs ^ xorout) << (index * width)
            particular = solver.solve(target)
            if particular is None:
                continue
            for init in gf2.span(particular, solver.nullspace, limit):
                if init_set is None or init in init_set:
                    pairs.append((init, xorout))
                    if len(pairs) >= limit:
                        return InitXorSolutions(tuple(pairs), len(solver.nullspace), True)
        return InitXorSolutions(tuple(pairs), len(solver.nullspace)) if pairs else None

    # Both free: subtracting the first sample removes xorout, (Z_i ^ Z_0)·init = rhs_i ^ rhs_0;
    # xorout then follows from the first sample.
    columns0, rhs0 = terms[0]
    stacked = [0] * width
    target = 0
    for index, (columns, rhs) in enumerate(terms[1:]):
        shift = index * width
        for bit in range(width):
            stacked[bit] |= (columns[bit] ^ columns0[bit]) << shift
        target |= (rhs ^ rhs0) << shift
    solved = gf2.solve_columns(stacked, target)
    if solved is None:
        return None
    particular, basis = solved

    def pair(init: int) -> tuple[int, int]:
        return init, rhs0 ^ apply_columns(columns0, init)

    if basis and init_set is None and xorout_set is None:
        # Under-determined: every pair in the solution space produces identical checksums for these
        # samples. List them when there are few; otherwise the conventional choices or one representative.
        if len(basis) <= SMALL_AMBIGUITY_BITS:
            pairs = sorted(
                map(pair, gf2.span(particular, basis, 1 << len(basis))), key=lambda p: (p[0] not in (0, mask), p)
            )
            return InitXorSolutions(tuple(pairs[:limit]), len(basis), len(pairs) > limit)
        conventional = _enumerate_init(terms, (0, mask), None, limit)
        if conventional is not None:
            return InitXorSolutions(conventional.pairs, len(basis), True)
        return InitXorSolutions((pair(particular),), len(basis), True)
    pairs = []
    truncated = (1 << len(basis)) > ENUMERATE_LIMIT * 4
    for init in gf2.span(particular, basis, ENUMERATE_LIMIT * 4):
        init, xorout = pair(init)
        if (init_set is None or init in init_set) and (xorout_set is None or xorout in xorout_set):
            pairs.append((init, xorout))
            if len(pairs) >= limit:
                truncated = True
                break
    return InitXorSolutions(tuple(pairs), len(basis), truncated) if pairs else None


def _enumerate_init(
    terms: Sequence[tuple[list[int], int]],
    init_values: Iterable[int],
    xorout_set: set[int] | None,
    limit: int,
) -> InitXorSolutions | None:
    columns0, rhs0 = terms[0]
    pairs: list[tuple[int, int]] = []
    for init in init_values:
        xorout = rhs0 ^ apply_columns(columns0, init)
        if xorout_set is not None and xorout not in xorout_set:
            continue
        if all(apply_columns(columns, init) ^ xorout == rhs for columns, rhs in terms[1:]):
            pairs.append((init, xorout))
            if len(pairs) >= limit:
                return InitXorSolutions(tuple(pairs), 0, True)
    return InitXorSolutions(tuple(pairs)) if pairs else None


def derive_polys(
    samples: Sequence[Sample],
    width: int,
    refin: bool,
    refout: bool,
    limit: int = 256,
) -> tuple[list[int], bool] | None:
    """Candidate polynomials from pairs of equal-length samples.

    Returns ``None`` when no two samples share a length (nothing cancels), otherwise
    ``(polys, truncated)``. An empty list means no CRC of this width fits.
    """
    groups: dict[int, list[Sample]] = {}
    for sample in samples:
        groups.setdefault(len(sample.data), []).append(sample)
    common = 0
    has_pair = False
    for group in groups.values():
        base = group[0]
        base_poly = message_poly(base.data, refin)
        for other in group[1:]:
            difference = ((base_poly ^ message_poly(other.data, refin)) << width) ^ output_to_model(
                base.checksum ^ other.checksum, width, refout
            )
            if difference == 0:
                continue
            has_pair = True
            common = gf2.gcd(common, difference) if common else difference
            if gf2.degree(common) < width:
                return [], False
    if not has_pair:
        return None
    top = 1 << width
    if gf2.degree(common) == width:
        return [common ^ top], False
    factors = gf2.small_factors(common, width)
    divisors = list(gf2.divisors_of_degree(factors, width, limit + 1))
    truncated = len(divisors) > limit
    return sorted({divisor ^ top for divisor in divisors[:limit]}), truncated


def _catalog_key(algorithm: CrcAlgorithm) -> tuple[int, int, int, int, bool, bool]:
    return (algorithm.width, algorithm.poly, algorithm.init, algorithm.xor_out, algorithm.refin, algorithm.refout)


_CRC_BY_PARAMS: dict[tuple[int, int, int, int, bool, bool], CrcAlgorithm] = {}


def catalog_name_for(algorithm: CrcAlgorithm) -> str | None:
    if not _CRC_BY_PARAMS:
        for entry in get_algorithms(family="crc"):
            if isinstance(entry, CrcAlgorithm):
                _CRC_BY_PARAMS.setdefault(_catalog_key(entry), entry)
    known = _CRC_BY_PARAMS.get(_catalog_key(algorithm))
    return known.name if known else None


def match_catalog(
    samples: Sequence[Sample],
    width: int | None = None,
    names: Sequence[str] | None = None,
    family: str | None = None,
) -> list[Algorithm]:
    """Catalog algorithms that produce every sample's checksum."""
    matches = []
    for algorithm in get_algorithms(names=names, width=width, family=family):
        if all(s.checksum <= algorithm.mask and algorithm.compute(s.data) == s.checksum for s in samples):
            matches.append(algorithm)
    return matches


def sum_family_candidates(width: int) -> list[SumChecksum | XorChecksum]:
    """Every sum/XOR checksum layout of the given width."""
    if width % 8 != 0 or width <= 0:
        return []
    candidates: list[SumChecksum | XorChecksum] = [XorChecksum(f"custom-xor-{width}", width)]
    for word_size in (1, 2, 4, 8):
        if word_size * 8 > width:
            break
        for endian in ENDIAN_MODES if word_size > 1 else ("big",):
            for carry in ("discard", "end-around"):
                for complement in ("none", "ones", "twos"):
                    if carry == "end-around" and complement == "twos":
                        continue
                    name = f"custom-sum-{width}-w{word_size * 8}-{endian}-{carry}-{complement}"
                    candidates.append(SumChecksum(name, width, word_size, endian, carry, complement))
    return candidates


def solve(
    samples: Sequence[Sample],
    width: int,
    *,
    poly_values: Sequence[int] | None = None,
    init_values: Sequence[int] | None = None,
    xorout_values: Sequence[int] | None = None,
    reflections: Sequence[tuple[bool, bool]] = REFLECTION_COMBOS,
    byte_order_modes: Sequence[str] = ("native",),
    swap_checksums: bool = True,
    scan_limit: int = 1 << 16,
    limit: int = 64,
    exhaustive: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> SolveResult:
    """Identify the algorithm behind a set of samples: catalog, derived CRC parameters and sum layouts.

    The slow polynomial scan (needed only when no two samples share a length) is skipped when a
    catalog algorithm already fits, unless ``exhaustive`` is true.
    """
    if not samples:
        raise ValueError("At least one sample is required.")
    if width <= 0:
        raise ValueError("Width must be positive.")
    mask = (1 << width) - 1
    for sample in samples:
        if sample.checksum > mask:
            raise ValueError(f"Checksum 0x{sample.checksum:X} does not fit in {width} bits. Pass a larger --width.")

    lengths = tuple(sorted({len(sample.data) for sample in samples}))
    result = SolveResult(width=width, sample_count=len(samples), lengths=lengths)
    seen_crcs: set[tuple[object, ...]] = set()
    seen_catalog: set[tuple[str, str, str]] = set()
    prepared: list[tuple[str, str, list[Sample]]] = []

    views = distinct_views([sample.data for sample in samples], byte_order_modes)
    for mode, datasets, reason in views:
        if datasets is None:
            result.skipped_modes.append((mode, reason or ""))
            continue
        mode_samples = [Sample(data, sample.checksum) for data, sample in zip(datasets, samples, strict=True)]
        variants = sample_variants(mode_samples, width) if swap_checksums else [("as-given", mode_samples)]
        for crc_mode, variant_samples in variants:
            prepared.append((mode, crc_mode, variant_samples))
            for algorithm in match_catalog(variant_samples, width=width):
                key = (algorithm.name, mode, crc_mode)
                if key not in seen_catalog:
                    seen_catalog.add(key)
                    target = result.catalog_matches if algorithm.family == "crc" else result.checksum_matches
                    target.append(CatalogMatch(algorithm, mode, crc_mode))
            for candidate in sum_family_candidates(width):
                fits = all(candidate.compute(s.data) == s.checksum for s in variant_samples)
                if fits and not any(m.algorithm.params() == candidate.params() for m in result.checksum_matches):
                    result.checksum_matches.append(CatalogMatch(candidate, mode, crc_mode))

    # Derive CRC parameters only after the catalog pass, so a catalog hit in any view can skip the slow scan.
    catalog_fits = bool(seen_catalog)
    for mode, crc_mode, variant_samples in prepared:
        _solve_crc(
            variant_samples,
            width,
            mode,
            crc_mode,
            result,
            seen_crcs,
            poly_values,
            init_values,
            xorout_values,
            reflections,
            scan_limit if exhaustive or not catalog_fits else 0,
            limit,
            progress,
        )
    return result


def _solve_crc(
    samples: Sequence[Sample],
    width: int,
    mode: str,
    crc_mode: str,
    result: SolveResult,
    seen: set[tuple[object, ...]],
    poly_values: Sequence[int] | None,
    init_values: Sequence[int] | None,
    xorout_values: Sequence[int] | None,
    reflections: Sequence[tuple[bool, bool]],
    scan_limit: int,
    limit: int,
    progress: Callable[[int, int], None] | None,
) -> None:
    poly_set = None if poly_values is None else set(poly_values)
    scan_reflections: list[tuple[bool, bool]] = []
    for refin, refout in reflections:
        derived = derive_polys(samples, width, refin, refout)
        if derived is None:
            scan_reflections.append((refin, refout))
            continue
        polys, truncated = derived
        if poly_set is not None:
            polys = [poly for poly in polys if poly in poly_set]
        if truncated:
            _note_once(
                result, "Many polynomials fit these samples; add more samples to narrow them down.", derived=True
            )
        for poly in polys:
            solutions = solve_init_xorout(samples, width, poly, refin, refout, init_values, xorout_values, limit)
            if solutions is None:
                continue
            multiplicity = x_plus_1_multiplicity(width, poly)
            if solutions.free_bits and len({len(s.data) for s in samples}) > 1 and solutions.free_bits <= multiplicity:
                _note_once(
                    result,
                    "Some polynomials are divisible by (x+1); for those each init/xorout pair has equivalent twins "
                    "that give identical CRCs for every message. All twins are listed and all are correct.",
                    derived=True,
                )
            elif solutions.free_bits:
                if len({len(sample.data) for sample in samples}) == 1:
                    reason = "every sample has the same length"
                    advice = "add a sample of a different length to pin them down"
                else:
                    reason = "the samples' lengths are too close together for this polynomial"
                    advice = "add samples whose lengths differ more"
                _note_once(
                    result,
                    f"For some solutions init and xorout are not fully determined because {reason}: several "
                    "init/xorout pairs give identical results. Equivalent pairs are listed when there are few, "
                    "otherwise the conventional init values (0 and all-ones) or one representative. "
                    f"To fix them, {advice}.",
                    derived=True,
                )
            for init, xorout in solutions.pairs:
                if not _record(
                    result,
                    seen,
                    CrcAlgorithm("custom", width, poly, init, xorout, refin, refout),
                    mode,
                    crc_mode,
                    solutions.free_bits,
                    limit,
                ):
                    return

    if not scan_reflections:
        return
    if scan_limit == 0 and poly_values is None:
        _note_once(
            result,
            "A catalog algorithm already fits, so the slower polynomial scan was skipped "
            "(use --all-solutions to run it anyway).",
        )
        return
    if poly_values is not None:
        scan_polys: Sequence[int] = list(poly_values)
    elif width <= 16 and (1 << width) <= scan_limit:
        scan_polys = range(1 << width)
    else:
        _note_once(
            result,
            f"To derive a {width}-bit polynomial, give at least two samples of the same length (or pass --poly).",
        )
        return
    chance = expected_chance_matches(
        len(scan_polys),
        len(scan_reflections),
        len(init_values) if init_values is not None else 1 << width,
        len(xorout_values) if xorout_values is not None else 1 << width,
        width,
        len(samples),
    )
    if chance > 0.5:
        _note_once(
            result,
            "These samples cannot single out a CRC: no two share a length, and with init and xorout free "
            "almost every polynomial fits. Add samples (ideally two of equal length) or fix --init/--xor-out.",
        )
        return
    _note_once(
        result,
        f"No two samples share a length, so {len(scan_polys)} polynomials were scanned. "
        "Equal-length samples make this instant.",
    )
    scan = scan_custom(
        [(mode, crc_mode, tuple(samples))],
        width,
        scan_polys,
        scan_reflections,
        init_values,
        xorout_values,
        keep=limit,
        jobs=default_jobs(len(scan_polys) * len(scan_reflections) * 8),
        progress=progress,
    )
    for match in scan.matches:
        if match.free_bits:
            _note_once(
                result,
                "For some polynomials, several init/xorout pairs give identical results for these sample "
                "lengths; one representative is shown. Samples whose lengths differ more pin them down.",
                derived=True,
            )
        if not _record(result, seen, match.algorithm, mode, crc_mode, match.free_bits, limit):
            return
    if scan.interrupted:
        _note_once(result, "Scan interrupted; results are partial.")


def _record(
    result: SolveResult,
    seen: set[tuple[object, ...]],
    algorithm: CrcAlgorithm,
    mode: str,
    crc_mode: str,
    free_bits: int,
    limit: int,
) -> bool:
    """Add a CRC solution once; returns False when the result limit is reached."""
    key = (algorithm.poly, algorithm.init, algorithm.xor_out, algorithm.refin, algorithm.refout, mode, crc_mode)
    if key not in seen:
        seen.add(key)
        result.crc_matches.append(CrcMatch(algorithm, mode, crc_mode, catalog_name_for(algorithm), free_bits))
    if len(result.crc_matches) >= limit:
        _note_once(result, f"Stopped after {limit} CRC solutions; narrow the search to see the rest.", derived=True)
        return False
    return True


def x_plus_1_multiplicity(width: int, poly: int) -> int:
    """How many times (x+1) divides the full polynomial.

    Every init/xorout pair then has 2**multiplicity equivalent twins: (x+1) divides x^k - 1
    for every k, so the length of the message never separates them.
    """
    remaining = (1 << width) | poly
    count = 0
    while True:
        quotient, remainder = gf2.divmod_poly(remaining, 0b11)
        if remainder:
            return count
        remaining = quotient
        count += 1


def _note_once(result: SolveResult, note: str, derived: bool = False) -> None:
    """Record a note once; ``derived`` notes only concern the derived CRC solutions."""
    target = result.derived_notes if derived else result.notes
    if note not in target:
        target.append(note)


# --- Single-sample custom parameter search -------------------------------------------------


@dataclass
class ScanResult:
    matches: list[CrcMatch] = field(default_factory=list)
    match_count: int = 0
    tested: int = 0
    interrupted: bool = False


ScanView = tuple[str, str, tuple[Sample, ...]]
# input mode, checksum mode, poly, init, xorout, refin, refout, free bits
ScanHit = tuple[str, str, int, int, int, bool, bool, int]


def expected_chance_matches(
    poly_count: int,
    reflection_count: int,
    init_count: int,
    xorout_count: int,
    width: int,
    sample_count: int,
) -> float:
    """How many parameter sets would fit purely by chance (each sample is a width-bit filter)."""
    return poly_count * reflection_count * init_count * xorout_count / float(1 << (width * sample_count))


def _scan_chunk(
    views: Sequence[ScanView],
    width: int,
    polys: Sequence[int],
    reflections: Sequence[tuple[bool, bool]],
    init_values: Sequence[int] | None,
    xorout_values: Sequence[int] | None,
    keep: int,
) -> tuple[list[ScanHit], int, int]:
    found: list[ScanHit] = []
    count = 0
    tested = 0
    for input_mode, crc_mode, samples in views:
        for poly in polys:
            for refin, refout in reflections:
                tested += 1
                solutions = solve_init_xorout(
                    samples, width, poly, refin, refout, init_values, xorout_values, limit=keep
                )
                if solutions is None:
                    continue
                count += len(solutions.pairs)
                for init, xorout in solutions.pairs:
                    if len(found) < keep:
                        found.append((input_mode, crc_mode, poly, init, xorout, refin, refout, solutions.free_bits))
    return found, count, tested


def _chunks(values: Sequence[int], size: int) -> list[Sequence[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def scan_custom(
    views: Sequence[ScanView],
    width: int,
    polys: Sequence[int],
    reflections: Sequence[tuple[bool, bool]],
    init_values: Sequence[int] | None,
    xorout_values: Sequence[int] | None,
    *,
    keep: int = 1000,
    jobs: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> ScanResult:
    """Search custom CRC parameters. xorout (or init) is solved, never scanned."""
    result = ScanResult()
    total = len(polys) * len(reflections) * len(views)
    chunk_size = max(16, min(1024, len(polys) // 64 or 16))
    chunks = _chunks(list(polys), chunk_size)

    def absorb(found: list[ScanHit], count: int, tested: int) -> None:
        result.match_count += count
        result.tested += tested
        for input_mode, crc_mode, poly, init, xorout, refin, refout, free_bits in found:
            if len(result.matches) >= keep:
                break
            algorithm = CrcAlgorithm("custom", width, poly, init, xorout, refin, refout)
            result.matches.append(CrcMatch(algorithm, input_mode, crc_mode, catalog_name_for(algorithm), free_bits))
        if progress is not None:
            progress(result.tested, total)

    if jobs <= 1 or len(chunks) <= 1:
        try:
            for chunk in chunks:
                absorb(*_scan_chunk(views, width, chunk, reflections, init_values, xorout_values, keep))
        except KeyboardInterrupt:
            result.interrupted = True
        _sort_matches(result, views)
        return result

    executor = ProcessPoolExecutor(max_workers=jobs)
    pending: set[Future[tuple[list[ScanHit], int, int]]] = set()
    try:
        for chunk in chunks:
            pending.add(
                executor.submit(_scan_chunk, views, width, chunk, reflections, init_values, xorout_values, keep)
            )
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                absorb(*future.result())
    except KeyboardInterrupt:
        result.interrupted = True
        executor.shutdown(wait=False, cancel_futures=True)
        _sort_matches(result, views)
        return result
    executor.shutdown()
    _sort_matches(result, views)
    return result


def _sort_matches(result: ScanResult, views: Sequence[ScanView]) -> None:
    """Deterministic order regardless of worker timing: catalog matches first, then by view and poly."""
    order = {(view[0], view[1]): index for index, view in enumerate(views)}
    result.matches.sort(
        key=lambda m: (
            m.catalog_name is None,
            order.get((m.input_mode, m.crc_mode), 0),
            m.algorithm.poly,
            m.algorithm.refin,
            m.algorithm.refout,
            m.algorithm.init,
            m.algorithm.xor_out,
        )
    )


def default_jobs(work: int) -> int:
    """Use every core for large searches; small ones are faster in-process."""
    if work < 200_000:
        return 1
    return max(1, min(os.cpu_count() or 1, 16))
