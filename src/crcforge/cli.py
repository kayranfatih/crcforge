"""Command-line interface for CRCForge."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__
from . import io as io_helpers
from .byteorder import BYTE_ORDER_MODES, describe_byte_order, distinct_views, transform_byte_order
from .catalog import (
    CHECK_INPUT,
    FAMILIES,
    FULL_CATALOG,
    Algorithm,
    UnknownAlgorithmError,
    get_algorithm,
    get_algorithms,
    validate_catalog,
)
from .checksums import CARRY_MODES, COMPLEMENT_MODES, ENDIAN_MODES, SumChecksum
from .codegen import LANGUAGES, CodegenError, generate
from .crc import CrcAlgorithm, calculate_residue, format_value, validate_params
from .forge import ForgeError, forge
from .frames import DEFAULT_FRAME_WIDTHS, POSITIONS, FrameLayout, candidate_layouts
from .io import InputError, format_bytes, format_crc_result, parse_checksum_value, parse_value_spec
from .output import ANSI_CREDIT, ANSI_FRAME, ANSI_TEXT, Output, colorize, supports_color
from .records import check_records
from .solver import (
    ENUMERATE_LIMIT,
    REFLECTION_COMBOS,
    CrcMatch,
    Sample,
    ScanView,
    SolveResult,
    checksum_variants,
    default_jobs,
    expected_chance_matches,
    scan_custom,
    solve,
)

TOOL_NAME = "CRCForge"
PLAIN_BANNER = r"""
+-----------------------------------------------------------+
|   ____ ____   ____ _____ ___  ____   ____ _____           |
|  / ___|  _ \ / ___|  ___/ _ \|  _ \ / ___| ____|          |
| | |   | |_) | |   | |_ | | | | |_) | |  _|  _|            |
| | |___|  _ <| |___|  _|| |_| |  _ <| |_| | |___           |
|  \____|_| \_\\____|_|   \___/|_| \_\\____|_____|          |
|                                                           |
+-----------------------------------------------------------+
""".strip("\n")
AUTHOR_LINE = "by Fatih Kayran"
SOCIAL_LINE = "X/Twitter: @kayranfatih"

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

MATCH_DISPLAY_LIMIT = 50
DERIVED_DISPLAY_LIMIT = 8
GLOBAL_FLAGS = frozenset({"--json", "-q", "--quiet", "--no-color", "--show-banner"})


class CliError(InputError):
    pass


# --- Presentation helpers ------------------------------------------------------------------


def render_banner(color: bool | None = None) -> str:
    colored = supports_color() if color is None else color
    lines = []
    for line in PLAIN_BANNER.splitlines():
        code = ANSI_FRAME if line.startswith(("+", "|")) else ANSI_TEXT
        lines.append(colorize(line, code, colored))
    return "\n".join(lines)


def render_credit_lines(color: bool | None = None) -> str:
    colored = supports_color() if color is None else color
    return "\n".join((colorize(AUTHOR_LINE, ANSI_CREDIT, colored), colorize(SOCIAL_LINE, ANSI_CREDIT, colored)))


def plural(count: int, noun: str, plural_form: str | None = None) -> str:
    return f"{count} {noun if count == 1 else (plural_form or noun + 's')}"


def describe_params(algorithm: Algorithm) -> str:
    """The parameter part of ``describe()``, without the name."""
    return algorithm.describe().split(": ", 1)[1]


def match_label(algorithm: Algorithm, catalog_name: str | None = None) -> str:
    if algorithm.name.startswith("custom"):
        text = describe_params(algorithm)
        if catalog_name:
            text += f"  (= {catalog_name})"
        return text
    return algorithm.describe()


def algorithm_json(algorithm: Algorithm) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": algorithm.name,
        "family": algorithm.family,
        "width": algorithm.width,
        "aliases": list(algorithm.aliases),
        "params": algorithm.params(),
    }
    if algorithm.check is not None:
        payload["check"] = format_value(algorithm.check, algorithm.width)
    if isinstance(algorithm, CrcAlgorithm):
        if algorithm.residue is not None:
            payload["residue"] = format_value(algorithm.residue, algorithm.width)
        payload["reveng"] = algorithm.reveng_spec()
        if algorithm.status:
            payload["status"] = algorithm.status
        if algorithm.reference_url:
            payload["reference"] = algorithm.reference_url
    return payload


def value_json(value: int, width: int) -> dict[str, object]:
    return {"value": value, "hex": format_value(value, width)}


def mode_tag(input_mode: str, crc_mode: str, show_input: bool = True) -> str:
    return f"[input:{input_mode}][crc:{crc_mode}]" if show_input else f"[crc:{crc_mode}]"


def coincidence_note(out: Output, sample_count: int, width: int, match_count: int) -> None:
    if match_count > 1 and sample_count == 1 and width <= 16:
        out.note(
            f"With a single sample, short {width}-bit checksums often match by coincidence. "
            "Confirm with more samples: crcforge solve DATA:CHECKSUM DATA:CHECKSUM ..."
        )


# --- Argument parsing helpers --------------------------------------------------------------


def parse_reflect_option(raw_value: str) -> list[bool]:
    normalized = raw_value.strip().lower()
    if normalized == "auto":
        return [False, True]
    if normalized in ("true", "1", "yes", "on"):
        return [True]
    if normalized in ("false", "0", "no", "off"):
        return [False]
    raise CliError(f"Invalid reflection option: {raw_value}", hint="Use auto, true or false.")


def parse_reflect_value(raw_value: str | None, label: str, default: bool = False) -> bool:
    if raw_value is None:
        return default
    values = parse_reflect_option(raw_value)
    if len(values) != 1:
        raise CliError(f"{label} must be true or false here, not auto.")
    return values[0]


def parse_offset(raw_value: str, data_length: int) -> int:
    text = raw_value.strip().lower()
    if text == "end":
        return data_length
    if text == "start":
        return 0
    value = io_helpers.parse_number(raw_value, "offset")
    if value < 0:
        value += data_length
    return value


def using_custom_crc(args: argparse.Namespace) -> bool:
    return any(getattr(args, name, None) is not None for name in ("poly", "init", "xor_out", "refin", "refout"))


def using_custom_sum(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "sum", False))


def custom_algorithm(args: argparse.Namespace) -> Algorithm | None:
    """The algorithm described by custom parameter flags, if any were given."""
    if using_custom_sum(args):
        if using_custom_crc(args):
            raise CliError("Use either --sum or CRC parameters (--poly, --init, ...), not both.")
        width = args.width or 8
        word_bits = args.word_size or 8
        if word_bits % 8:
            raise CliError("--word-size must be a multiple of 8 bits.")
        return SumChecksum(
            "custom-sum",
            width,
            word_size=word_bits // 8,
            endian=args.endian or "big",
            carry=args.carry or "discard",
            complement=args.complement or "none",
        )
    if not using_custom_crc(args):
        return None
    if args.width is None:
        raise CliError("Custom CRC parameters need --width.", hint="For example: --width 16 --poly 0x1021")
    if args.poly is None:
        raise CliError("Custom CRC parameters need --poly.", hint="For example: --width 16 --poly 0x1021")
    width = args.width
    if width <= 0:
        raise CliError("--width must be positive.")
    mask = (1 << width) - 1
    poly = single_value(args.poly, mask, "poly")
    init = single_value(args.init or "0", mask, "init")
    xor_out = single_value(args.xor_out or "0", mask, "xor-out")
    validate_params(width, poly, init, xor_out)
    return CrcAlgorithm(
        "custom",
        width,
        poly,
        init,
        xor_out,
        parse_reflect_value(args.refin, "--refin"),
        parse_reflect_value(args.refout, "--refout"),
    )


def single_value(raw_value: str, mask: int, label: str) -> int:
    values = parse_value_spec(raw_value, mask, label)
    if len(values) != 1:
        raise CliError(f"--{label} must be a single value here, not a list or range.")
    return values[0]


def take_algorithm(
    args: argparse.Namespace,
    items: Sequence[str],
    rest_names: Sequence[str],
    optional: int = 0,
    command: str = "",
) -> tuple[Algorithm, list[str]]:
    """Resolve the algorithm from ``--algorithm``, custom flags or the first positional value."""
    custom = custom_algorithm(args)
    named = getattr(args, "algorithm", None)
    if custom is not None and named:
        raise CliError("Use either --algorithm or custom parameters, not both.")
    if custom is not None:
        algorithm: Algorithm = custom
        rest = list(items)
    elif named:
        algorithm = get_algorithm(named)
        rest = list(items)
    else:
        if not items:
            raise CliError(
                "Missing algorithm name.", hint=f"Usage: crcforge {command} <algorithm> {' '.join(rest_names)}"
            )
        algorithm = get_algorithm(items[0])
        rest = list(items[1:])
    required = len(rest_names) - optional
    if not required <= len(rest) <= len(rest_names):
        usage = " ".join(f"<{name}>" for name in rest_names)
        raise CliError(
            f"Expected {usage} after the algorithm, got {len(rest)} value{'s' if len(rest) != 1 else ''}.",
            hint=f"Usage: crcforge {command} <algorithm> {usage}  (or pass --algorithm NAME before the data)",
        )
    return algorithm, rest


def load_data(args: argparse.Namespace) -> bytes:
    return io_helpers.load_input_bytes(args)


def data_summary(args: argparse.Namespace, data: bytes) -> str:
    return f"{io_helpers.detect_original_data_kind(args)}, {len(data)} bytes"


def emit_input_note(out: Output, args: argparse.Namespace) -> None:
    note = io_helpers.text_input_note(args)
    if note:
        out.note(note)


def resolve_jobs(requested: int | None, work: int) -> int:
    if requested is None or requested == 0:
        return default_jobs(work)
    if requested < 0:
        raise CliError("--jobs must be 0 (auto) or a positive number.")
    return requested


# --- Commands ------------------------------------------------------------------------------


def handle_list(args: argparse.Namespace, out: Output) -> int:
    algorithms = get_algorithms(width=args.width, family=args.family, search=args.search)
    if not algorithms:
        out.error("No algorithms match these filters.", hint="Run 'crcforge list' to see the whole catalog.")
        return EXIT_NO_MATCH
    if out.json:
        out.emit_json({"count": len(algorithms), "algorithms": [algorithm_json(a) for a in algorithms]})
        return EXIT_OK
    if out.quiet:
        for algorithm in algorithms:
            out.value(algorithm.name)
        return EXIT_OK

    crc_count = sum(1 for algorithm in algorithms if algorithm.family == "crc")
    alias_count = sum(len(algorithm.aliases) for algorithm in algorithms)
    out.section("Built-in CRC & Checksum Catalog")
    out.kv(
        "Algorithms",
        f"{len(algorithms)} ({plural(crc_count, 'CRC')}, {plural(len(algorithms) - crc_count, 'checksum')})",
    )
    out.kv("Aliases", str(alias_count))
    out.kv("Names total", str(len(algorithms) + alias_count))
    filters = [
        f"width={args.width}" if args.width else "",
        f"family={args.family}" if args.family else "",
        f"search={args.search}" if args.search else "",
    ]
    if any(filters):
        out.kv("Filters", ", ".join(item for item in filters if item))
    for algorithm in algorithms:
        out.kv("Algorithm", algorithm.describe(), status="text")
        if args.aliases and algorithm.aliases:
            out.kv("Aliases", ", ".join(algorithm.aliases), status="text")
    if not args.aliases:
        out.note("Add --aliases to show alternative names, or use 'crcforge info <name>' for details.")
    return EXIT_OK


def handle_info(args: argparse.Namespace, out: Output) -> int:
    algorithm = get_algorithm(args.name)
    if out.json:
        out.emit_json(algorithm_json(algorithm))
        return EXIT_OK
    if out.quiet:
        out.value(algorithm.describe())
        return EXIT_OK
    out.section("Algorithm Info")
    out.kv("Name", algorithm.name)
    out.kv("Family", algorithm.family)
    if algorithm.aliases:
        out.kv("Aliases", ", ".join(algorithm.aliases))
    for key, value in algorithm.params().items():
        out.kv(key, str(value).lower() if isinstance(value, bool) else value, status="text")
    if algorithm.check is not None:
        out.kv("Check", f"{format_value(algorithm.check, algorithm.width)} for the ASCII string 123456789")
    if isinstance(algorithm, CrcAlgorithm):
        out.kv("Residue", format_value(calculate_residue(algorithm), algorithm.width))
        out.kv("RevEng spec", algorithm.reveng_spec())
        if algorithm.reveng_name:
            status = f" ({algorithm.status})" if algorithm.status else ""
            out.kv("RevEng name", f"{algorithm.reveng_name}{status}")
        if algorithm.reference_url:
            out.kv("Reference", algorithm.reference_url)
    return EXIT_OK


def handle_calc(args: argparse.Namespace, out: Output) -> int:
    if args.all:
        return calc_all(args, out)
    if args.width is not None and not using_custom_crc(args) and not using_custom_sum(args) and not args.algorithm:
        raise CliError(
            "--width needs --poly (custom CRC), --sum (custom sum checksum) or --all.",
            hint="Example: crcforge calc 123456789 --width 16 --poly 0x1021",
        )
    algorithm, rest = take_algorithm(args, args.calc_items, ("original-data",), command="calc")
    args.original_data = rest[0]
    data = transform_byte_order(load_data(args), args.byte_order)
    value = algorithm.compute(data)
    custom = algorithm.name.startswith("custom")

    if out.json:
        payload: dict[str, object] = {"algorithm": algorithm_json(algorithm), **value_json(value, algorithm.width)}
        payload.update({"data_length": len(data), "byte_order": args.byte_order})
        out.emit_json(payload)
        return EXIT_OK
    if out.quiet:
        out.value(
            format_crc_result(value, algorithm.width, args.output_format if args.output_format != "both" else "hex")
        )
        return EXIT_OK

    out.section("CRC Calculation" if algorithm.family == "crc" else "Checksum Calculation")
    out.kv("Mode", "custom-parameters" if custom else "built-in")
    out.kv("Original data", io_helpers.detect_original_data_kind(args))
    out.kv("Data length", f"{len(data)} bytes")
    out.kv("Byte order", f"{args.byte_order} ({describe_byte_order(args.byte_order)})")
    if custom:
        out.kv("Parameters", describe_params(algorithm))
    else:
        out.kv("Algorithm", algorithm.describe())
    out.kv("Result", format_crc_result(value, algorithm.width, args.output_format), status="success")
    emit_input_note(out, args)
    return EXIT_OK


def calc_all(args: argparse.Namespace, out: Output) -> int:
    if len(args.calc_items) != 1:
        raise CliError("--all takes exactly one original-data value.", hint="Example: crcforge calc 123456789 --all")
    if using_custom_crc(args) or using_custom_sum(args) or args.algorithm:
        raise CliError("--all cannot be combined with --algorithm or custom parameters.")
    args.original_data = args.calc_items[0]
    data = transform_byte_order(load_data(args), args.byte_order)
    algorithms = get_algorithms(width=args.width, family=args.family)
    results = [(algorithm, algorithm.compute(data)) for algorithm in algorithms]
    if out.json:
        out.emit_json(
            {
                "data_length": len(data),
                "results": [{"name": a.name, "width": a.width, **value_json(v, a.width)} for a, v in results],
            }
        )
        return EXIT_OK
    name_width = max((len(algorithm.name) for algorithm, _ in results), default=10)
    if out.quiet:
        for algorithm, value in results:
            out.value(f"{algorithm.name} {format_value(value, algorithm.width)}")
        return EXIT_OK
    out.section("All Catalog Results")
    out.kv("Original data", data_summary(args, data))
    out.kv("Algorithms", str(len(results)))
    for algorithm, value in results:
        out.line(f"  {algorithm.name:{name_width}}  {format_crc_result(value, algorithm.width, args.output_format)}")
    emit_input_note(out, args)
    return EXIT_OK


def catalog_search(
    data: bytes,
    expected: int,
    modes: Sequence[str],
    names: Sequence[str] | None,
    width: int | None,
    family: str | None,
) -> tuple[list[tuple[str, str, Algorithm]], list[tuple[str, str]], int, int]:
    """Match one sample against the catalog across byte-order views and checksum byte swaps."""
    matches: list[tuple[str, str, Algorithm]] = []
    skipped: list[tuple[str, str]] = []
    tested = 0
    scanned_modes = 0
    algorithms = get_algorithms(names=names, width=width, family=family)
    for mode, datasets, reason in distinct_views([data], modes):
        if datasets is None:
            skipped.append((mode, reason or ""))
            continue
        scanned_modes += 1
        for algorithm in algorithms:
            if expected > algorithm.mask:
                continue
            value = algorithm.compute(datasets[0])
            for crc_mode, candidate in checksum_variants(expected, algorithm.width):
                tested += 1
                if value == candidate:
                    matches.append((mode, crc_mode, algorithm))
    return matches, skipped, tested, scanned_modes


def ordered_modes(primary: str, scan_all: bool) -> list[str]:
    if not scan_all:
        return [primary]
    return [primary, *(mode for mode in BYTE_ORDER_MODES if mode != primary)]


def matches_json(matches: Sequence[tuple[str, str, Algorithm]]) -> list[dict[str, object]]:
    return [
        {"input_mode": mode, "crc_mode": crc_mode, "algorithm": algorithm_json(algorithm)}
        for mode, crc_mode, algorithm in matches
    ]


def handle_find(args: argparse.Namespace, out: Output) -> int:
    data = load_data(args)
    checksum = parse_checksum_value(args.checksum)
    display_width = max(8, args.width or checksum.width_hint)
    matches, skipped, _, _ = catalog_search(
        data,
        checksum.value,
        ordered_modes(args.byte_order, args.scan_byte_order),
        args.algorithm,
        args.width,
        args.family,
    )
    if out.json:
        out.emit_json(
            {
                "checksum": format_value(checksum.value, display_width),
                "data_length": len(data),
                "matches": matches_json(matches),
                "skipped_modes": [{"mode": m, "reason": r} for m, r in skipped],
            }
        )
        return EXIT_OK if matches else EXIT_NO_MATCH
    if out.quiet:
        for _, _, algorithm in matches:
            out.value(algorithm.name)
        return EXIT_OK if matches else EXIT_NO_MATCH

    out.section("CRC Match Search")
    out.kv("Checksum", format_value(checksum.value, display_width))
    out.kv("Original data", io_helpers.detect_original_data_kind(args))
    out.kv("Byte-order scan", "all supported modes" if args.scan_byte_order else args.byte_order)
    for mode, reason in skipped:
        out.warn(f"Skipped [{mode}]: {reason}")
    if not matches:
        out.kv("Matches", "0", status="warn")
        out.failure("No catalogued CRC or checksum algorithm matched.")
        hints = []
        if not args.scan_byte_order:
            hints.append("--scan-byte-order to try other byte orders")
        hints.append("'crcforge brute' for custom CRC parameters")
        hints.append("'crcforge solve' with several samples")
        out.note("Try " + ", ".join(hints) + ".")
        emit_input_note(out, args)
        return EXIT_NO_MATCH
    out.kv("Matches", str(len(matches)), status="success")
    show_mode = args.scan_byte_order or args.byte_order != "native"
    for mode, crc_mode, algorithm in matches:
        out.kv("Match", f"{mode_tag(mode, crc_mode, show_mode)} {algorithm.describe()}", status="success")
    coincidence_note(out, 1, display_width, len(matches))
    emit_input_note(out, args)
    return EXIT_OK


def handle_brute(args: argparse.Namespace, out: Output) -> int:
    if using_custom_crc(args) or args.full_custom:
        if args.algorithm:
            raise CliError("Do not combine --algorithm with custom brute parameters.")
        return run_custom_brute(args, out)

    data = load_data(args)
    checksum = parse_checksum_value(args.checksum)
    display_width = max(8, args.width or checksum.width_hint)
    matches, skipped, tested, scanned = catalog_search(
        data, checksum.value, BYTE_ORDER_MODES, args.algorithm, args.width, args.family
    )
    algorithm_names = [a.name for a in get_algorithms(names=args.algorithm, width=args.width, family=args.family)]
    if out.json:
        out.emit_json(
            {
                "mode": "built-in-catalog",
                "checksum": format_value(checksum.value, display_width),
                "data_length": len(data),
                "tested": tested,
                "matches": matches_json(matches),
                "skipped_modes": [{"mode": m, "reason": r} for m, r in skipped],
            }
        )
        return EXIT_OK if matches else EXIT_NO_MATCH
    if out.quiet:
        for _, _, algorithm in matches:
            out.value(algorithm.name)
        return EXIT_OK if matches else EXIT_NO_MATCH

    out.section("CRC Brute Scan")
    out.kv("Mode", "built-in-catalog")
    out.kv("Checksum", format_value(checksum.value, display_width))
    out.kv("Original data", data_summary(args, data))
    out.kv("Tried", f"{tested} algorithm/checksum combinations across {scanned} input byte-order modes.")
    out.kv("Algorithms", f"{len(algorithm_names)} built-in entries")
    for mode, reason in skipped:
        out.warn(f"Skipped [{mode}]: {reason}")
    if not matches:
        out.failure("Brute scan found no catalogued CRC or checksum match.")
        out.note("For a custom CRC, add --poly SPEC or --full-custom; with several samples use 'crcforge solve'.")
        emit_input_note(out, args)
        return EXIT_NO_MATCH
    out.kv("Matches", str(len(matches)), status="success")
    for mode, crc_mode, algorithm in matches:
        out.kv("Match", f"{mode_tag(mode, crc_mode)} {algorithm.describe()}", status="success")
    coincidence_note(out, 1, display_width, len(matches))
    emit_input_note(out, args)
    return EXIT_OK


def run_custom_brute(args: argparse.Namespace, out: Output) -> int:
    if args.progress_interval < 0:
        raise CliError("--progress-interval must be 0 or greater.")
    if args.limit <= 0:
        raise CliError("--limit must be positive.")
    checksum = parse_checksum_value(args.checksum)
    width = args.width
    width_note = None
    if width is None:
        if not args.full_custom:
            raise CliError("Custom brute mode needs --width.", hint="Pass --width N, or use --full-custom to infer it.")
        width = checksum.width_hint
        width_note = f"inferred from {checksum.width_reason()}; pass --width to override"
    if width <= 0:
        raise CliError("--width must be positive.")
    mask = (1 << width) - 1
    if checksum.value > mask:
        raise CliError(f"Checksum {format_value(checksum.value, width)} does not fit in {width} bits.")

    if args.poly is not None:
        polys: Sequence[int] = parse_value_spec(args.poly, mask, "poly")
    elif width <= 16:
        polys = range(mask + 1)
    else:
        raise CliError(
            f"Scanning all 2^{width} polynomials is not practical.",
            hint="Pass --poly, or run 'crcforge solve' with two or more samples of the same length; "
            "it derives the polynomial directly.",
        )
    init_values = parse_value_spec(args.init, mask, "init") if args.init else (None if args.full_custom else [0])
    xorout_values = (
        parse_value_spec(args.xor_out, mask, "xor-out") if args.xor_out else (None if args.full_custom else [0])
    )
    conventional = False
    if init_values is None and xorout_values is None:
        init_values = xorout_values = sorted({0, mask})
        conventional = True
    reflections = [
        (refin, refout)
        for refin in parse_reflect_option(args.refin or "auto")
        for refout in parse_reflect_option(args.refout or "auto")
    ]

    data = load_data(args)
    views: list[ScanView] = []
    skipped: list[tuple[str, str]] = []
    for mode, datasets, reason in distinct_views([data], BYTE_ORDER_MODES):
        if datasets is None:
            skipped.append((mode, reason or ""))
            continue
        for crc_mode, value in checksum_variants(checksum.value, width):
            views.append((mode, crc_mode, (Sample(datasets[0], value),)))

    work = len(polys) * len(reflections) * len(views)
    if work > args.max_combinations:
        raise CliError(
            f"This search needs {work:,} evaluations, above the limit of {args.max_combinations:,}.",
            hint="Narrow --poly, fix --refin/--refout, or raise --max-combinations.",
        )
    init_count = len(init_values) if init_values is not None else mask + 1
    xorout_count = len(xorout_values) if xorout_values is not None else mask + 1
    chance = expected_chance_matches(len(polys), len(reflections) * len(views), init_count, xorout_count, width, 1)
    if init_values is not None and len(init_values) <= ENUMERATE_LIMIT:
        solved = f"init={len(init_values)} value(s), xorout=solved directly"
    elif xorout_values is not None and len(xorout_values) <= ENUMERATE_LIMIT:
        solved = f"xorout={len(xorout_values)} value(s), init=solved directly"
    else:
        solved = "init and xorout solved jointly"
    jobs = resolve_jobs(args.jobs, work * max(1, width // 4))

    if out.human:
        out.section("CRC Brute Scan")
        out.kv("Mode", "custom-parameters")
        out.kv("Checksum", format_value(checksum.value, max(8, width)))
        out.kv("Width", f"{width} bits" + (f" ({width_note})" if width_note else ""))
        out.kv("Original data", data_summary(args, data))
        out.kv("Search space", f"poly={len(polys)}, {solved}, refin/refout={len(reflections)}")
        out.kv(
            "Evaluations",
            f"{work:,} across {len(views)} input/checksum views" + (f", {jobs} workers" if jobs > 1 else ""),
        )
        for mode, reason in skipped:
            out.warn(f"Skipped [{mode}]: {reason}")
        if conventional:
            out.note(
                "With one sample, a free init and a free xorout fit every polynomial, so both were limited to "
                "0 and all-ones. Pass --init/--xor-out ranges to widen this, or use 'crcforge solve' with more samples."
            )
        if chance > 1:
            out.warn(
                f"Expect about {chance:,.0f} coincidental matches: one {width}-bit sample cannot single out "
                "these parameters. Add samples with 'crcforge solve' or narrow the search."
            )

    progress = out.progress(args.progress_interval)
    scan = scan_custom(
        views,
        width,
        polys,
        reflections,
        init_values,
        xorout_values,
        keep=args.limit,
        jobs=jobs,
        progress=progress.update,
    )
    progress.close()

    if out.json:
        out.emit_json(
            {
                "mode": "custom-parameters",
                "width": width,
                "checksum": format_value(checksum.value, width),
                "evaluations": scan.tested,
                "match_count": scan.match_count,
                "expected_chance_matches": chance,
                "interrupted": scan.interrupted,
                "matches": [crc_match_json(match) for match in scan.matches],
            }
        )
        return EXIT_INTERRUPTED if scan.interrupted else (EXIT_OK if scan.matches else EXIT_NO_MATCH)
    if out.quiet:
        for match in scan.matches:
            out.value(match.algorithm.reveng_spec())
        return EXIT_INTERRUPTED if scan.interrupted else (EXIT_OK if scan.matches else EXIT_NO_MATCH)

    out.kv("Tried", f"{scan.tested:,} parameter sets.")
    if scan.interrupted:
        out.warn("Interrupted: showing the matches found so far.")
    if not scan.matches:
        out.failure("Custom brute scan found no matching CRC parameters.")
        return EXIT_INTERRUPTED if scan.interrupted else EXIT_NO_MATCH
    out.kv("Matches", f"{scan.match_count:,}", status="success")
    for match in scan.matches:
        out.kv(
            "Match",
            f"{mode_tag(match.input_mode, match.crc_mode)} {match_label(match.algorithm, match.catalog_name)}",
            status="success",
        )
    if scan.match_count > len(scan.matches):
        out.note(f"Showing the first {len(scan.matches)} matches; raise --limit to see more.")
    return EXIT_INTERRUPTED if scan.interrupted else EXIT_OK


def crc_match_json(match: CrcMatch) -> dict[str, object]:
    payload: dict[str, object] = {
        "input_mode": match.input_mode,
        "crc_mode": match.crc_mode,
        "params": match.algorithm.params(),
        "reveng": match.algorithm.reveng_spec(),
    }
    if match.catalog_name:
        payload["catalog_name"] = match.catalog_name
    if match.free_bits:
        payload["free_bits"] = match.free_bits
    return payload


def solve_json(result: SolveResult) -> dict[str, object]:
    return {
        "width": result.width,
        "samples": result.sample_count,
        "lengths": list(result.lengths),
        "catalog": [
            {"input_mode": m.input_mode, "crc_mode": m.crc_mode, "algorithm": algorithm_json(m.algorithm)}
            for m in result.catalog_matches
        ],
        "crc": [crc_match_json(match) for match in result.crc_matches],
        "checksums": [
            {"input_mode": m.input_mode, "crc_mode": m.crc_mode, "algorithm": algorithm_json(m.algorithm)}
            for m in result.checksum_matches
        ],
        "notes": [*result.notes, *result.derived_notes],
    }


def group_crc_matches(matches: Sequence[CrcMatch]) -> list[list[CrcMatch]]:
    """Group solutions that differ only in (equivalent) init/xorout pairs."""
    groups: dict[tuple[int, bool, bool, str, str], list[CrcMatch]] = {}
    for match in matches:
        key = (match.algorithm.poly, match.algorithm.refin, match.algorithm.refout, match.input_mode, match.crc_mode)
        groups.setdefault(key, []).append(match)
    return list(groups.values())


def print_solve_result(out: Output, result: SolveResult, show_modes: bool, all_solutions: bool = False) -> None:
    def tag(input_mode: str, crc_mode: str) -> str:
        parts = []
        if show_modes or input_mode != "native":
            parts.append(f"input:{input_mode}")
        if crc_mode != "as-given":
            parts.append(f"crc:{crc_mode}")
        return f"[{']['.join(parts)}] " if parts else ""

    for match in result.catalog_matches:
        out.kv("Catalog CRC", f"{tag(match.input_mode, match.crc_mode)}{match.algorithm.describe()}", status="success")

    groups = group_crc_matches(visible_crc_matches(result))
    catalog_checksums = [m for m in result.checksum_matches if not m.algorithm.name.startswith("custom")]
    layouts = [m for m in result.checksum_matches if m.algorithm.name.startswith("custom")]
    known = bool(result.catalog_matches or catalog_checksums)

    if groups and known and not all_solutions:
        out.kv(
            "Other fits",
            f"{plural(len(groups), 'other CRC parameter set')} also fit these samples; "
            "they are most likely coincidences.",
            status="dim",
        )
        out.detail("List them with --all-solutions or --json; more or longer samples rule them out.", status="dim")
        derived_notes: list[str] = []
    else:
        derived_notes = result.derived_notes
        shown = groups if all_solutions else groups[:DERIVED_DISPLAY_LIMIT]
        for group in shown:
            first = group[0]
            spec = first.algorithm.reveng_spec()
            out.kv("Derived CRC", f"{tag(first.input_mode, first.crc_mode)}{spec}", status="success")
            if first.catalog_name:
                out.detail(f"= {first.catalog_name} (built-in)")
            for twin in group[1:]:
                pair = f"init=0x{twin.algorithm.init:X} xorout=0x{twin.algorithm.xor_out:X}"
                out.detail(f"or {pair} (equivalent)", status="dim")
            if first.free_bits and len(group) == 1:
                out.detail("init/xorout: one of several equivalent pairs for these samples", status="dim")
        if len(groups) > len(shown):
            out.note(f"{len(groups) - len(shown)} more CRC parameter sets fit; use --all-solutions or --json.")
        if len(groups) > 1 and not known:
            out.warn("Several different CRCs fit these samples. Add more (and longer) samples to tell them apart.")

    for match in catalog_checksums:
        out.kv("Checksum", f"{tag(match.input_mode, match.crc_mode)}{match.algorithm.describe()}", status="success")
    if layouts and (known or groups) and not all_solutions:
        out.kv(
            "Other fits", f"{plural(len(layouts), 'sum/XOR layout')} also fit; most likely coincidences.", status="dim"
        )
    else:
        for match in layouts if all_solutions else layouts[:DERIVED_DISPLAY_LIMIT]:
            out.kv(
                "Checksum", f"{tag(match.input_mode, match.crc_mode)}{match_label(match.algorithm)}", status="success"
            )
    for mode, reason in result.skipped_modes:
        out.note(f"Skipped [{mode}]: {reason}")
    for note in [*result.notes, *derived_notes]:
        out.note(note)


def solve_settings(args: argparse.Namespace, width: int) -> dict[str, object]:
    mask = (1 << width) - 1
    return {
        "poly_values": parse_value_spec(args.poly, mask, "poly") if args.poly else None,
        "init_values": parse_value_spec(args.init, mask, "init") if args.init else None,
        "xorout_values": parse_value_spec(args.xor_out, mask, "xor-out") if args.xor_out else None,
        "reflections": [
            (refin, refout)
            for refin, refout in REFLECTION_COMBOS
            if refin in parse_reflect_option(args.refin or "auto")
            and refout in parse_reflect_option(args.refout or "auto")
        ],
        "byte_order_modes": BYTE_ORDER_MODES if args.scan_byte_order else ("native",),
        "limit": args.limit,
    }


def handle_solve(args: argparse.Namespace, out: Output) -> int:
    values = list(args.samples)
    if args.samples_file:
        values.extend(io_helpers.read_lines(args.samples_file))
    if not values:
        raise CliError(
            "No samples given.",
            hint="Pass samples as DATA:CHECKSUM (or frames with --frames), or use --samples-file FILE.",
        )
    if args.frames:
        return solve_frames(args, out, [io_helpers.parse_frame(value) for value in values])

    parsed = [io_helpers.parse_sample(value) for value in values]
    width = args.width
    width_note = None
    if width is None:
        hint_sample = max(parsed, key=lambda item: item[1].width_hint)[1]
        width = hint_sample.width_hint
        width_note = f"inferred from {hint_sample.width_reason()}; pass --width to override"
    samples = [Sample(data, checksum.value) for data, checksum in parsed]
    progress = out.progress(args.progress_interval)
    result = solve(samples, width, progress=progress.update, **solve_settings(args, width))  # type: ignore[arg-type]
    progress.close()

    if out.json:
        out.emit_json(solve_json(result))
        return EXIT_OK if result.found else EXIT_NO_MATCH
    if out.quiet:
        quiet_solve(out, result, args.all_solutions)
        return EXIT_OK if result.found else EXIT_NO_MATCH

    out.section("CRC Solver")
    lengths = ", ".join(str(length) for length in result.lengths)
    out.kv("Samples", f"{result.sample_count} (lengths: {lengths} bytes)")
    out.kv("Width", f"{width} bits" + (f" ({width_note})" if width_note else ""))
    if result.sample_count == 1:
        out.warn("Only one sample: matches may be coincidental. Add more samples to confirm.")
    if not result.found:
        out.failure("No CRC or checksum fits every sample.")
        out.note(
            "Check that the checksums belong to the data, try --scan-byte-order, another --width, "
            "or use --frames if the checksum is inside the data."
        )
        for note in result.notes:
            out.note(note)
        return EXIT_NO_MATCH
    print_solve_result(out, result, args.scan_byte_order, args.all_solutions)
    if result.sample_count == 2 and width <= 16:
        out.note("Two samples leave room for coincidences at this width; a third sample confirms the result.")
    return EXIT_OK


def visible_crc_matches(result: SolveResult) -> list[CrcMatch]:
    """Derived CRCs worth showing next to the catalog matches.

    A derived CRC that is a catalog match is already listed. When init/xorout are ambiguous
    (same-length samples), pairs equivalent to a catalog match add nothing either.
    """
    catalog = [m for m in result.catalog_matches if isinstance(m.algorithm, CrcAlgorithm)]
    visible = []
    for crc in result.crc_matches:
        if crc.catalog_name and any(m.algorithm.name == crc.catalog_name for m in catalog):
            continue
        if crc.free_bits and any(
            isinstance(m.algorithm, CrcAlgorithm)
            and (m.algorithm.poly, m.algorithm.refin, m.algorithm.refout)
            == (crc.algorithm.poly, crc.algorithm.refin, crc.algorithm.refout)
            and m.input_mode == crc.input_mode
            and m.crc_mode == crc.crc_mode
            for m in catalog
        ):
            continue
        visible.append(crc)
    return visible


def quiet_solve(out: Output, result: SolveResult, all_solutions: bool = False) -> None:
    """Catalog names first; derived parameters and layouts only when nothing in the catalog fits."""
    catalog_checksums = [m for m in result.checksum_matches if not m.algorithm.name.startswith("custom")]
    for match in [*result.catalog_matches, *catalog_checksums]:
        out.value(match.algorithm.name)
    if (result.catalog_matches or catalog_checksums) and not all_solutions:
        return
    for group in group_crc_matches(visible_crc_matches(result)):
        out.value(group[0].algorithm.reveng_spec())
    for match in result.checksum_matches:
        if match.algorithm.name.startswith("custom"):
            out.value(describe_params(match.algorithm))


def solve_frames(args: argparse.Namespace, out: Output, frames: list[bytes]) -> int:
    widths = [args.width] if args.width else list(DEFAULT_FRAME_WIDTHS)
    positions = list(POSITIONS) if args.crc_at == "auto" else [args.crc_at]
    endians = ["big", "little"] if args.crc_endian == "auto" else [args.crc_endian]
    skips = list(range(9)) if args.skip == "auto" else [io_helpers.parse_number(args.skip, "skip")]
    layouts = candidate_layouts(widths, positions, endians, skips)
    progress = out.progress(args.progress_interval)
    found: list[tuple[FrameLayout, SolveResult]] = []
    for index, layout in enumerate(layouts):
        progress.update(index, len(layouts), layout.describe())
        try:
            samples = layout.split_all(frames)
        except ValueError:
            continue
        if any(not sample.data for sample in samples):
            continue
        result = solve(samples, layout.width, swap_checksums=False, **solve_settings(args, layout.width))  # type: ignore[arg-type]
        if result.found:
            found.append((layout, result))
    progress.close()

    if out.json:
        out.emit_json(
            {
                "frames": len(frames),
                "layouts_tested": len(layouts),
                "layouts": [{"layout": layout.as_dict(), **solve_json(result)} for layout, result in found],
            }
        )
        return EXIT_OK if found else EXIT_NO_MATCH
    if out.quiet:
        for layout, result in found:
            out.value(f"# {layout.describe()}")
            quiet_solve(out, result, args.all_solutions)
        return EXIT_OK if found else EXIT_NO_MATCH

    out.section("CRC Solver (frames)")
    sizes = sorted({len(frame) for frame in frames})
    out.kv("Frames", f"{len(frames)} (lengths: {', '.join(map(str, sizes))} bytes)")
    out.kv("Layouts tested", str(len(layouts)))
    if len(frames) == 1:
        out.warn("Only one frame: matches may be coincidental. Add more frames to confirm.")
    if not found:
        out.failure("No layout produced a CRC or checksum that fits every frame.")
        out.note("Try --crc-at auto, --crc-endian auto, --skip auto, or a specific --width.")
        return EXIT_NO_MATCH
    for layout, result in found:
        out.kv("Layout", layout.describe(), status="info")
        print_solve_result(out, result, args.scan_byte_order, args.all_solutions)
    return EXIT_OK


def handle_verify(args: argparse.Namespace, out: Output) -> int:
    rest_names = ("original-data",) if args.frame else ("original-data", "checksum")
    algorithm, rest = take_algorithm(args, args.items, rest_names, command="verify")
    args.original_data = rest[0]
    data = load_data(args)
    width = algorithm.width

    if args.frame:
        endians = ["big", "little"] if args.crc_endian == "auto" else [args.crc_endian]
        attempts = []
        for endian in endians if (width + 7) // 8 > 1 else ["big"]:
            layout = FrameLayout(width, args.crc_at, endian, args.skip)
            sample = layout.split(data)
            actual = algorithm.compute(transform_byte_order(sample.data, args.byte_order))
            attempts.append((layout, sample, actual))
        valid = [attempt for attempt in attempts if attempt[1].checksum == attempt[2]]
        layout, sample, actual = (valid or attempts)[0]
        expected_value, given = actual, sample.checksum
        description = layout.describe()
        ok = bool(valid)
        swapped = False
    else:
        given = parse_checksum_value(rest[1]).value
        expected_value = algorithm.compute(transform_byte_order(data, args.byte_order))
        ok = expected_value == given
        swapped = not ok and any(value == expected_value for mode, value in checksum_variants(given, width)[1:])
        description = None

    if out.json:
        payload: dict[str, object] = {
            "valid": ok,
            "algorithm": algorithm.name,
            "expected": format_value(expected_value, width),
            "given": format_value(given, width),
        }
        if description:
            payload["layout"] = description
        if swapped:
            payload["matches_byte_swapped"] = True
        out.emit_json(payload)
        return EXIT_OK if ok else EXIT_NO_MATCH
    if out.quiet:
        out.value("valid" if ok else "invalid")
        return EXIT_OK if ok else EXIT_NO_MATCH

    out.section("Checksum Verification")
    out.kv("Algorithm", algorithm.describe() if not algorithm.name.startswith("custom") else describe_params(algorithm))
    out.kv("Original data", data_summary(args, data))
    if description:
        out.kv("Frame layout", description)
    out.kv("Given", format_value(given, width))
    out.kv("Calculated", format_value(expected_value, width))
    if ok:
        out.kv("Result", "VALID", status="success")
        return EXIT_OK
    out.kv("Result", "INVALID", status="error")
    if swapped:
        out.note("The checksum matches if its bytes are swapped: it is probably stored in the other byte order.")
    elif args.frame and args.crc_endian != "auto":
        out.note("Try --crc-endian auto to accept either byte order.")
    return EXIT_NO_MATCH


def handle_forge(args: argparse.Namespace, out: Output) -> int:
    algorithm, rest = take_algorithm(args, args.items, ("original-data", "target"), command="forge")
    args.original_data = rest[0]
    data = load_data(args)
    target = parse_checksum_value(rest[1]).value
    offset = parse_offset(args.at, len(data)) if args.at is not None else None
    result = forge(data, algorithm, target, offset=offset, overwrite=args.overwrite, patch_size=args.patch_size)
    if args.output:
        Path(args.output).write_bytes(result.data)

    if out.json:
        out.emit_json(
            {
                "algorithm": algorithm.name,
                "target": format_value(target, algorithm.width),
                "offset": result.offset,
                "mode": "overwrite" if result.overwrite else "insert",
                "patch": result.patch.hex().upper(),
                "data": result.data.hex().upper(),
                "written_to": args.output,
            }
        )
        return EXIT_OK
    if out.quiet:
        out.value(format_bytes(result.data, args.output_format or "compact-hex"))
        return EXIT_OK

    out.section("Forge")
    out.kv("Algorithm", algorithm.describe() if not algorithm.name.startswith("custom") else describe_params(algorithm))
    out.kv("Original data", data_summary(args, data))
    out.kv("Target", format_value(target, algorithm.width))
    action = "overwrote" if result.overwrite else "inserted"
    out.kv("Patch", f"{format_bytes(result.patch, 'hex')} ({action} at offset {result.offset})", status="success")
    rendered = format_bytes(result.data, args.output_format or "hex")
    if len(result.data) > 64 and not args.output_format:
        rendered = format_bytes(result.data[:64], "hex") + f" ... ({len(result.data)} bytes)"
    out.kv("Forged data", rendered)
    out.kv("Verified", f"{format_value(algorithm.compute(result.data), algorithm.width)} == target", status="success")
    if args.output:
        out.kv("Written to", args.output)
    return EXIT_OK


def handle_records(args: argparse.Namespace, out: Output) -> int:
    if args.file == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(args.file).read_text(encoding="ascii", errors="replace")
        except FileNotFoundError:
            raise CliError(f"File not found: {args.file}") from None
    report = check_records(text)
    issues = report.issues
    address_range = None
    if report.lowest_address is not None and report.highest_address is not None:
        address_range = f"0x{report.lowest_address:08X}-0x{report.highest_address:08X}"

    if out.json:
        out.emit_json(
            {
                "format": report.format,
                "valid": report.ok,
                "records": report.records,
                "data_bytes": report.data_bytes,
                "address_range": address_range,
                "record_types": report.types,
                "issues": [
                    {
                        "line": issue.line,
                        "message": issue.message,
                        **({"expected": f"0x{issue.expected:02X}"} if issue.expected is not None else {}),
                        **({"actual": f"0x{issue.actual:02X}"} if issue.actual is not None else {}),
                    }
                    for issue in issues
                ],
            }
        )
        return EXIT_OK if report.ok else EXIT_NO_MATCH
    if out.quiet:
        out.value("valid" if report.ok else f"invalid ({len(issues)} issues)")
        return EXIT_OK if report.ok else EXIT_NO_MATCH

    out.section("Record File Check")
    out.kv("Format", "Intel HEX" if report.format == "intel-hex" else "Motorola S-record")
    out.kv("Records", str(report.records))
    out.kv("Data bytes", str(report.data_bytes))
    if address_range:
        out.kv("Address range", address_range)
    if report.types:
        out.kv("Record types", ", ".join(f"{name}={count}" for name, count in sorted(report.types.items())))
    if report.ok:
        out.kv("Result", "VALID: every record checksum is correct", status="success")
        return EXIT_OK
    out.kv("Result", f"INVALID: {len(issues)} issue{'s' if len(issues) != 1 else ''}", status="error")
    for issue in issues[:MATCH_DISPLAY_LIMIT]:
        where = f"line {issue.line}" if issue.line else "file"
        text = f"{where}: {issue.message}"
        if issue.expected is not None and issue.actual is not None:
            text += f" (expected 0x{issue.expected:02X}, found 0x{issue.actual:02X})"
        out.kv("Issue", text, status="error")
    if len(issues) > MATCH_DISPLAY_LIMIT:
        out.note(f"{len(issues) - MATCH_DISPLAY_LIMIT} more issues not shown (use --json).")
    return EXIT_NO_MATCH


def handle_codegen(args: argparse.Namespace, out: Output) -> int:
    algorithm, _ = take_algorithm(args, args.items, (), command="codegen")
    if not isinstance(algorithm, CrcAlgorithm):
        raise CodegenError(f"Code generation supports CRC algorithms; {algorithm.name} is a checksum.")
    code = generate(algorithm, args.lang, args.name)
    if args.output:
        Path(args.output).write_text(code, encoding="utf-8")
    if out.json:
        out.emit_json({"algorithm": algorithm.name, "language": args.lang, "code": code, "written_to": args.output})
        return EXIT_OK
    if args.output:
        if out.human:
            out.success(f"Wrote {args.lang} code for {algorithm.name} to {args.output}")
        return EXIT_OK
    out.stdout.write(code)
    return EXIT_OK


def handle_transform(args: argparse.Namespace, out: Output) -> int:
    data = load_data(args)
    transformed = transform_byte_order(data, args.byte_order)
    if out.json:
        out.emit_json({"byte_order": args.byte_order, "input_length": len(data), "result": transformed.hex().upper()})
        return EXIT_OK
    if out.quiet:
        out.value(format_bytes(transformed, args.output_format))
        return EXIT_OK
    out.section("Byte Transform")
    out.kv("Original data", io_helpers.detect_original_data_kind(args))
    out.kv("Input length", f"{len(data)} bytes")
    out.kv("Byte order", f"{args.byte_order} ({describe_byte_order(args.byte_order)})")
    out.kv("Result", format_bytes(transformed, args.output_format), status="success")
    emit_input_note(out, args)
    return EXIT_OK


def handle_self_test(args: argparse.Namespace, out: Output) -> int:
    failures = validate_catalog()
    crc_count = sum(1 for algorithm in FULL_CATALOG if algorithm.family == "crc")
    if out.json:
        out.emit_json({"passed": not failures, "algorithms": len(FULL_CATALOG), "failures": failures})
        return EXIT_NO_MATCH if failures else EXIT_OK
    if failures:
        for failure in failures:
            out.error(failure)
        return EXIT_NO_MATCH
    if out.quiet:
        out.value("passed")
        return EXIT_OK
    out.section("Self Test")
    out.kv(
        "Status",
        f"CRC and checksum catalog validation passed: {len(FULL_CATALOG)} algorithms "
        f"({crc_count} CRCs with check and residue, {len(FULL_CATALOG) - crc_count} checksums) "
        f"against {CHECK_INPUT.decode()}.",
        status="success",
    )
    return EXIT_OK


def handle_banner(args: argparse.Namespace, out: Output) -> int:
    out.line(render_banner(out.color))
    out.line(render_credit_lines(out.color))
    return EXIT_OK


# --- Parser --------------------------------------------------------------------------------


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=32)


def add_common_options(parser: argparse.ArgumentParser, suppress: bool) -> None:
    # Sub-commands use SUPPRESS so a flag given before the command is not reset by the sub-parser.
    default: object = argparse.SUPPRESS if suppress else False
    group = parser.add_argument_group("output options")
    group.add_argument("--json", action="store_true", default=default, help="Print machine-readable JSON.")
    group.add_argument("-q", "--quiet", action="store_true", default=default, help="Print only the result value.")
    group.add_argument(
        "--no-color", action="store_true", default=default, help="Disable colored output (NO_COLOR is also honored)."
    )
    group.add_argument(
        "--show-banner", action="store_true", default=default, help="Print the banner before the output."
    )


def add_input_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("input options")
    group.add_argument(
        "--as-file", action="store_true", help="Read the original data from this file path ('-' for stdin)."
    )
    group.add_argument(
        "--as-text", action="store_true", help="Treat the original data as plain text, even if it starts with 0x or 0b."
    )
    group.add_argument("--offset", type=int, default=0, help="Skip this many bytes at the start of the data.")
    group.add_argument("--length", type=int, help="Use only this many bytes (after --offset).")
    group.add_argument(
        "--exclude-tail",
        type=int,
        default=0,
        metavar="N",
        help="Ignore the last N bytes (for example a stored checksum).",
    )


def add_original_data(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "original_data",
        metavar="original-data",
        help="Data to process: 0x.. hex bytes, 0b.. binary bytes, '-' for stdin, otherwise text.",
    )
    add_input_options(parser)


def add_byte_order(parser: argparse.ArgumentParser, allow_scan: bool = False) -> None:
    parser.add_argument(
        "--byte-order",
        default="native",
        choices=BYTE_ORDER_MODES,
        help="Transform the data bytes first: native, reverse, swap16, swap32 or swap64.",
    )
    if allow_scan:
        parser.add_argument("--scan-byte-order", action="store_true", help="Also try every other byte-order mode.")


def add_crc_params(parser: argparse.ArgumentParser, spec: bool) -> None:
    group = parser.add_argument_group("custom CRC parameters" + (" (VALUE, A,B,C or START:END[:STEP])" if spec else ""))
    group.add_argument("--width", type=int, help="CRC width in bits.")
    group.add_argument("--poly", help="Polynomial" + (" spec." if spec else " (without the top bit)."))
    group.add_argument(
        "--init", help="Initial register value (RevEng notation). Default: 0." if not spec else "init spec."
    )
    group.add_argument("--xor-out", dest="xor_out", help="Final XOR value. Default: 0." if not spec else "xorout spec.")
    group.add_argument(
        "--refin", help="Reflect input bytes: true or false." + (" Default: auto." if spec else " Default: false.")
    )
    group.add_argument(
        "--refout", help="Reflect the output: true or false." + (" Default: auto." if spec else " Default: false.")
    )


def add_sum_params(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("custom sum checksum (with --sum; uses --width, default 8)")
    group.add_argument("--sum", action="store_true", help="Use a custom sum checksum instead of a CRC.")
    group.add_argument(
        "--word-size", type=int, metavar="BITS", help="Bits per summed word: 8, 16, 32 or 64. Default: 8."
    )
    group.add_argument("--endian", choices=ENDIAN_MODES, help="Byte order of multi-byte words. Default: big.")
    group.add_argument("--carry", choices=CARRY_MODES, help="Carry handling. Default: discard.")
    group.add_argument("--complement", choices=COMPLEMENT_MODES, help="Final complement. Default: none.")


def add_frame_options(parser: argparse.ArgumentParser, auto: bool) -> None:
    group = parser.add_argument_group("frame layout")
    group.add_argument(
        "--crc-at",
        choices=(*POSITIONS, "auto") if auto else POSITIONS,
        default="end",
        help="Where the checksum sits in the frame." + (" 'auto' tries both." if auto else ""),
    )
    group.add_argument(
        "--crc-endian",
        choices=("big", "little", "auto"),
        default="auto",
        help="Byte order of the stored checksum. Default: auto (try both).",
    )
    if auto:
        group.add_argument("--skip", default="0", help="Header bytes excluded from the data, or 'auto' (0-8).")
    else:
        group.add_argument("--skip", type=int, default=0, help="Header bytes excluded from the data.")


DESCRIPTION = f"""{{banner}}

{TOOL_NAME} calculates, identifies, reverse-engineers and forges CRCs and checksums.

Input rules:
  - data starting with 0x is hex bytes, 0b is binary bytes, '-' reads stdin, anything else is text
  - checksums are decimal by default; use 0x for hex or 0b for binary
  - --as-file reads a file, --as-text forces text
"""

EPILOG = """Common tasks:
  crcforge calc crc-16-modbus 123456789            calculate a known CRC
  crcforge calc 123456789 --all --width 16         every 16-bit algorithm at once
  crcforge find 0x4B37 123456789                   which catalog algorithm made this checksum?
  crcforge solve 0x0102:0x4110 0x0304:0x7181 ...   derive CRC parameters from several samples
  crcforge solve --frames 010300000001840A ...     same, with the checksum inside each frame
  crcforge brute 0x4B37 123456789 --width 16 --poly 0x8005 --init 0:0xFFFF
  crcforge verify crc-16-modbus 0x010300000001840A --frame
  crcforge forge crc-32 "hello world" 0xDEADBEEF   append 4 bytes so the CRC becomes 0xDEADBEEF
  crcforge records firmware.hex                    check every Intel HEX / S-record checksum
  crcforge codegen crc-16-modbus --lang c          generate a C implementation
  crcforge info crc-32                             parameters, check value and reference

Exit codes: 0 success, 1 no match / invalid, 2 usage or input error, 130 interrupted.
Run 'crcforge <command> --help' for details on a command."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crcforge",
        formatter_class=HelpFormatter,
        description=DESCRIPTION.format(banner=f"{render_banner()}\n{render_credit_lines()}"),
        epilog=EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_common_options(parser, suppress=False)
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", title="commands")
    subparsers.required = True
    common = argparse.ArgumentParser(add_help=False)
    add_common_options(common, suppress=True)

    def command(
        name: str, help_text: str, description: str, handler: Callable[..., int], epilog: str = ""
    ) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(
            name,
            help=help_text,
            description=description,
            epilog=epilog or None,
            formatter_class=HelpFormatter,
            parents=[common],
        )
        sub.set_defaults(handler=handler)
        return sub

    calc = command(
        "calc",
        "Calculate a CRC or checksum.",
        "Calculate a built-in algorithm, custom CRC parameters, a custom sum checksum, or every catalog entry.",
        handle_calc,
        "Examples:\n  crcforge calc crc-16-modbus 123456789\n  crcforge calc 123456789 --algorithm crc-32\n"
        "  crcforge calc 0x313233 --width 16 --poly 0x1021 --init 0xFFFF\n"
        "  crcforge calc 123456789 --sum --width 16 --word-size 16 --complement ones\n"
        "  crcforge calc firmware.bin --as-file --all --family crc",
    )
    calc.add_argument(
        "calc_items",
        nargs="+",
        metavar="value",
        help="<algorithm> <original-data>, or <original-data> with --algorithm/--all/custom parameters.",
    )
    calc.add_argument("--algorithm", help="Built-in algorithm name or alias.")
    calc.add_argument(
        "--all", action="store_true", help="Calculate every catalog algorithm (filter with --width/--family)."
    )
    calc.add_argument("--family", choices=FAMILIES, help="With --all: only CRCs or only checksums.")
    calc.add_argument(
        "--output-format", choices=("hex", "dec", "bin", "both"), default="both", help="Result format. Default: both."
    )
    add_crc_params(calc, spec=False)
    add_sum_params(calc)
    add_input_options(calc)
    add_byte_order(calc)

    find = command(
        "find",
        "Find which built-in algorithm produced a checksum.",
        "Match one data/checksum pair against the catalog. Checksums that match after a byte swap are reported too.",
        handle_find,
        "Examples:\n  crcforge find 0x4B37 123456789\n  crcforge find 19255 123456789 --width 16\n"
        "  crcforge find 0x1A47 0xA1B2C3D4 --scan-byte-order",
    )
    find.add_argument("checksum", help="Expected checksum: decimal, 0x hex or 0b binary.")
    add_original_data(find)
    find.add_argument("--width", type=int, help="Only algorithms of this width.")
    find.add_argument("--family", choices=FAMILIES, help="Only CRCs or only checksums.")
    find.add_argument("--algorithm", action="append", help="Only this algorithm (repeatable).")
    add_byte_order(find, allow_scan=True)

    brute = command(
        "brute",
        "Scan the catalog across byte orders, or search custom CRC parameters.",
        "Without custom flags, scan every catalog algorithm against every byte-order transform. With --poly or "
        "--full-custom, search custom CRC parameters; xorout (or init) is solved directly rather than scanned.",
        handle_brute,
        "Examples:\n  crcforge brute 0x4B37 123456789\n"
        "  crcforge brute 0x4B37 123456789 --width 16 --poly 0x8005 --init 0:0xFFFF\n"
        "  crcforge brute 0x31C3 123456789 --full-custom --refin false --refout false\n\n"
        "With one sample, wide searches match by coincidence; 'crcforge solve' with more samples is exact.",
    )
    brute.add_argument("checksum", help="Expected checksum: decimal, 0x hex or 0b binary.")
    add_original_data(brute)
    brute.add_argument("--algorithm", action="append", help="Catalog mode: only this algorithm (repeatable).")
    brute.add_argument("--family", choices=FAMILIES, help="Catalog mode: only CRCs or only checksums.")
    add_crc_params(brute, spec=True)
    brute.add_argument(
        "--full-custom",
        action="store_true",
        help="Custom mode: search every poly (and free init/xorout); width defaults to the checksum's digits.",
    )
    brute.add_argument(
        "--max-combinations", type=int, default=50_000_000, help="Safety limit on evaluations. Default: 50,000,000."
    )
    brute.add_argument("--limit", type=int, default=100, help="Maximum matches to show. Default: 100.")
    brute.add_argument("--jobs", type=int, default=0, help="Worker processes; 0 = automatic. Default: 0.")
    brute.add_argument(
        "--progress-interval",
        type=float,
        default=2.0,
        help="Seconds between progress lines when not on a terminal; 0 disables progress.",
    )

    solve_parser = command(
        "solve",
        "Derive CRC parameters from several samples.",
        "Identify the CRC or checksum behind several data/checksum samples. Two or more samples of the same length "
        "let CRCForge derive the polynomial directly (any width); samples of different lengths pin down init and "
        "xorout. Catalog algorithms and sum/XOR layouts are checked too.",
        handle_solve,
        "Examples:\n  crcforge solve 0x313233343536373839:0x4B37 0x3132:0x... 0x41424344:0x...\n"
        "  crcforge solve --samples-file samples.txt --width 32\n"
        "  crcforge solve --frames 0103000000018408 0103000000024409 --crc-endian auto\n"
        "  crcforge solve --frames @frame1.bin @frame2.bin --crc-at auto --skip auto\n\n"
        "Sample syntax: DATA:CHECKSUM, where DATA follows the usual input rules or is @file.\n"
        "Samples files hold one sample (or frame) per line; # starts a comment.",
    )
    solve_parser.add_argument(
        "samples", nargs="*", metavar="sample", help="DATA:CHECKSUM pairs, or whole frames with --frames."
    )
    solve_parser.add_argument(
        "--samples-file", metavar="FILE", help="Read samples (or frames) from a file, one per line ('-' for stdin)."
    )
    solve_parser.add_argument(
        "--frames", action="store_true", help="Each sample is a whole frame with the checksum inside it."
    )
    add_frame_options(solve_parser, auto=True)
    add_crc_params(solve_parser, spec=True)
    solve_parser.add_argument(
        "--scan-byte-order", action="store_true", help="Also try byte-order transforms of the data."
    )
    solve_parser.add_argument("--limit", type=int, default=64, help="Maximum CRC solutions to compute. Default: 64.")
    solve_parser.add_argument(
        "--all-solutions", action="store_true", help="List every fitting parameter set, not just the likely answer."
    )
    solve_parser.add_argument(
        "--progress-interval",
        type=float,
        default=2.0,
        help="Seconds between progress lines when not on a terminal; 0 disables progress.",
    )

    verify = command(
        "verify",
        "Check that data and a checksum belong together.",
        "Verify a checksum. With --frame, the checksum is read from the end (or start) of the data itself.",
        handle_verify,
        "Examples:\n  crcforge verify crc-16-modbus 123456789 0x4B37\n"
        "  crcforge verify crc-16-modbus 0x01030000000184 0A --frame\n"
        "  crcforge verify crc-32 firmware.bin 0xCBF43926 --as-file",
    )
    verify.add_argument(
        "items", nargs="+", metavar="value", help="<algorithm> <original-data> <checksum> (no checksum with --frame)."
    )
    verify.add_argument("--algorithm", help="Built-in algorithm name or alias.")
    verify.add_argument("--frame", action="store_true", help="The checksum is stored inside the data.")
    add_frame_options(verify, auto=False)
    add_crc_params(verify, spec=False)
    add_sum_params(verify)
    add_input_options(verify)
    add_byte_order(verify)

    forge_parser = command(
        "forge",
        "Patch data so it produces a chosen checksum.",
        "Insert (default: append) or overwrite a few bytes so the data's CRC or sum/XOR checksum becomes the target.",
        handle_forge,
        'Examples:\n  crcforge forge crc-32 "hello world" 0xDEADBEEF\n'
        "  crcforge forge crc-16-modbus firmware.bin 0x0000 --as-file --at 0x100 --overwrite -o patched.bin\n"
        "  crcforge forge sum-8 0x0102030405 0x00",
    )
    forge_parser.add_argument("items", nargs="+", metavar="value", help="<algorithm> <original-data> <target>.")
    forge_parser.add_argument("--algorithm", help="Built-in algorithm name or alias.")
    forge_parser.add_argument(
        "--at", help="Patch position: byte offset, negative from the end, 'start' or 'end'. Default: end."
    )
    forge_parser.add_argument("--overwrite", action="store_true", help="Overwrite bytes at --at instead of inserting.")
    forge_parser.add_argument(
        "--patch-size", type=int, metavar="BYTES", help="Patch length. Default: the checksum width."
    )
    forge_parser.add_argument("-o", "--output", metavar="FILE", help="Write the forged bytes to FILE.")
    forge_parser.add_argument(
        "--output-format", choices=("hex", "compact-hex", "text"), help="How to print the forged data."
    )
    add_crc_params(forge_parser, spec=False)
    add_sum_params(forge_parser)
    add_input_options(forge_parser)

    records = command(
        "records",
        "Validate an Intel HEX or Motorola S-record file.",
        "Check every record checksum, record length and structure in an Intel HEX or S-record file.",
        handle_records,
        "Examples:\n  crcforge records firmware.hex\n  crcforge records firmware.s19 --json",
    )
    records.add_argument("file", help="Path to the .hex/.s19/.srec file ('-' for stdin).")

    codegen = command(
        "codegen",
        "Generate CRC source code (C, Python, Rust).",
        "Generate a table-driven implementation of a built-in or custom CRC.",
        handle_codegen,
        "Examples:\n  crcforge codegen crc-16-modbus --lang c -o crc16_modbus.c\n"
        "  crcforge codegen --width 16 --poly 0x1021 --init 0xFFFF --lang python",
    )
    codegen.add_argument(
        "items", nargs="*", metavar="algorithm", help="Built-in algorithm name (omit with custom parameters)."
    )
    codegen.add_argument("--algorithm", help=argparse.SUPPRESS)
    codegen.add_argument("--lang", choices=LANGUAGES, default="c", help="Target language. Default: c.")
    codegen.add_argument("--name", help="Function name. Default: derived from the algorithm name.")
    codegen.add_argument("-o", "--output", metavar="FILE", help="Write the code to FILE.")
    add_crc_params(codegen, spec=False)

    transform = command(
        "transform",
        "Apply a byte-order transform.",
        "Transform the data's byte order and print the result, without any checksum work.",
        handle_transform,
        "Examples:\n  crcforge transform 0xA9BB7BFD --byte-order swap32\n"
        "  crcforge transform 0x48656C6C6F --output-format text",
    )
    add_original_data(transform)
    add_byte_order(transform)
    transform.add_argument(
        "--output-format",
        choices=("hex", "compact-hex", "text"),
        default="hex",
        help="Render as spaced hex, compact hex or UTF-8 text.",
    )

    list_parser = command(
        "list",
        "List built-in algorithms.",
        "List the built-in CRC and checksum catalog.",
        handle_list,
        "Examples:\n  crcforge list --width 16\n  crcforge list --family checksum\n"
        "  crcforge list --search modbus --aliases",
    )
    list_parser.add_argument("--width", type=int, help="Only this width.")
    list_parser.add_argument("--family", choices=FAMILIES, help="Only CRCs or only checksums.")
    list_parser.add_argument("--search", help="Only names or aliases containing this text.")
    list_parser.add_argument("--aliases", action="store_true", help="Also print alias names.")

    info = command(
        "info",
        "Show one algorithm in detail.",
        "Show parameters, aliases, check value, residue and reference.",
        handle_info,
    )
    info.add_argument("name", help="Algorithm name or alias.")

    command(
        "self-test",
        "Validate the built-in catalog.",
        "Check every catalog entry against its published check value and residue.",
        handle_self_test,
    )
    command("banner", "Print the banner.", "Print the banner, author credit and X/Twitter handle.", handle_banner)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if all(argument in GLOBAL_FLAGS for argument in arguments):
        parser.print_help()
        return EXIT_OK
    args = parser.parse_args(arguments)
    out = Output(json_mode=args.json, quiet=args.quiet, color=False if args.no_color else None)
    try:
        if args.show_banner and out.human:
            out.line(render_banner(out.color))
            out.line(render_credit_lines(out.color))
        return int(args.handler(args, out))
    except UnknownAlgorithmError as error:
        hint = f"Did you mean: {', '.join(error.suggestions)}?" if error.suggestions else None
        out.error(f"Unknown algorithm: {error.name}", hint or "Run 'crcforge list' to browse the catalog.")
        return EXIT_USAGE
    except InputError as error:
        out.error(str(error), error.hint)
        return EXIT_USAGE
    except (ForgeError, CodegenError, ValueError) as error:
        out.error(str(error))
        return EXIT_USAGE
    except KeyboardInterrupt:
        out.error("Interrupted.")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
