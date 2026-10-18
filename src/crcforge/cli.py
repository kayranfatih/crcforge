"""Command-line interface for CRCForge."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__
from . import io as io_helpers
from .banner import render_banner, render_credit_lines
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
from .checksums import (
    CARRY_MODES,
    COMPLEMENT_MODES,
    ENDIAN_MODES,
    Adler32Checksum,
    BsdChecksum,
    FletcherChecksum,
    SumChecksum,
    SysvChecksum,
    XorChecksum,
)
from .codegen import LANGUAGES, CodegenError, generate
from .crc import CrcAlgorithm, calculate_residue, format_value, validate_params
from .forge import ForgeError, forge
from .frames import DEFAULT_FRAME_WIDTHS, POSITIONS, FrameLayout, candidate_layouts
from .helpfmt import CrcforgeParser
from .io import InputError, format_bytes, format_crc_result, parse_checksum_value, parse_value_spec
from .output import Output
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

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

MATCH_DISPLAY_LIMIT = 50
DERIVED_DISPLAY_LIMIT = 8
PARAM_LABELS = {
    "width": "Width",
    "poly": "Poly",
    "init": "Init",
    "refin": "RefIn",
    "refout": "RefOut",
    "xorout": "XorOut",
    "type": "Type",
    "word_bits": "Word bits",
}
GLOBAL_FLAGS = frozenset({"--json", "-q", "--quiet", "--no-color", "--show-banner"})


class CliError(InputError):
    pass


class UsageError(CliError):
    """Missing or extra positional values: reported with the command's usage lines and an example."""

    def __init__(self, message: str, command: str, hint: str | None = None) -> None:
        super().__init__(message, hint)
        self.command = command


# --- Presentation helpers ------------------------------------------------------------------


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


def report_skipped(out: Output, skipped: Sequence[tuple[str, str]]) -> None:
    """One compact note for byte-order modes that were not scanned."""
    if not skipped:
        return
    parts = []
    for mode, reason in skipped:
        length = re.match(r"Input length \((\d+) bytes\) must be a multiple of (\d+) bytes", reason)
        if length:
            reason = f"{length.group(1)} bytes is not a multiple of {length.group(2)}"
        parts.append(f"{mode} ({reason})")
    out.note("Skipped byte orders: " + "; ".join(parts) + ".")


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
    resolved = value + data_length if value < 0 else value
    if not 0 <= resolved <= data_length:
        raise CliError(
            f"--at {raw_value} is outside the data ({plural(data_length, 'byte')}).",
            hint=f"Use 0 to {data_length}, a negative offset from -{data_length} to -1, 'start' or 'end'.",
        )
    return resolved


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
            if getattr(args, "width", None) is not None:
                raise CliError("Custom CRC parameters need --poly.", hint="For example: --width 16 --poly 0x1021")
            raise UsageError(
                f"crcforge {command} needs an algorithm name.",
                command,
                hint="Run 'crcforge list' to browse the algorithms.",
            )
        algorithm = get_algorithm(items[0])
        rest = list(items[1:])
    required = len(rest_names) - optional
    if len(rest) > len(rest_names):
        if not rest_names:
            raise UsageError(
                f"crcforge {command} takes one algorithm name; unexpected value '{rest[0]}'.",
                command,
                hint=f"Run 'crcforge {command} -h' for all options and examples.",
            )
        expected = " ".join(f"<{name}>" for name in rest_names)
        raise UsageError(
            f"Too many values: expected {expected} after the algorithm, got {len(rest)}.",
            command,
            hint='Put quotes around data that contains spaces, for example "0x01 03 00 00".',
        )
    if len(rest) < required:
        missing = " ".join(f"<{name}>" for name in rest_names[len(rest) :])
        hint = f"Run 'crcforge {command} -h' for all options and examples."
        if command == "verify" and "checksum" in missing:
            hint = "If the checksum is stored inside the data, add --frame."
        raise UsageError(f"crcforge {command} needs {missing}.", command, hint=hint)
    return algorithm, rest


def load_data(args: argparse.Namespace) -> bytes:
    return io_helpers.load_input_bytes(args)


def data_summary(args: argparse.Namespace, data: bytes) -> str:
    return f"{io_helpers.detect_original_data_kind(args)}, {plural(len(data), 'byte')}"


def write_output(path: str, content: bytes) -> None:
    """Write ``-o`` output with a clear error instead of a traceback."""
    target = Path(path)
    if target.is_dir():
        raise CliError(f"{path} is a directory; give a file name for -o.")
    try:
        target.write_bytes(content)
    except OSError as error:
        raise CliError(f"Cannot write {path}: {error.strerror or error}.") from None


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
    for width, group, caption in group_by_width(algorithms):
        out.line()
        out.rule(f"{width}-bit", caption)
        crcs = [algorithm for algorithm in group if isinstance(algorithm, CrcAlgorithm)]
        checksums = [algorithm for algorithm in group if not isinstance(algorithm, CrcAlgorithm)]
        alias_header = ["Aliases"] if args.aliases else []

        def aliases(algorithm: Algorithm) -> list[str]:
            return [out.style(", ".join(algorithm.aliases), "dim")] if args.aliases else []

        if crcs:
            rows = [
                [
                    name_cell(out, crc),
                    out.style(format_value(crc.poly, width), "value"),
                    format_value(crc.init, width),
                    bool_cell(out, crc.refin),
                    bool_cell(out, crc.refout),
                    format_value(crc.xor_out, width),
                    out.style(format_value(crc.check or 0, width), "dim"),
                    *aliases(crc),
                ]
                for crc in crcs
            ]
            out.table(["CRC", "Poly", "Init", "RefIn", "RefOut", "XorOut", "Check", *alias_header], rows)
        if checksums:
            if crcs:
                out.line()
            rows = [
                [
                    name_cell(out, checksum),
                    out.style(kind_label(checksum), "dim"),
                    params_text(checksum),
                    out.style(format_value(checksum.check or 0, width), "dim"),
                    *aliases(checksum),
                ]
                for checksum in checksums
            ]
            out.table(["Checksum", "Type", "Definition", "Check", *alias_header], rows)
    out.line()
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
    if isinstance(algorithm, CrcAlgorithm):
        for key, value in algorithm.params().items():
            label = PARAM_LABELS.get(key, key.replace("_", " ").capitalize())
            out.kv(label, str(value).lower() if isinstance(value, bool) else value, status="text")
    else:
        out.kv("Type", kind_label(algorithm), status="text")
        out.kv("Width", str(algorithm.width), status="text")
        out.kv("Definition", params_text(algorithm), status="text")
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
    algorithm, rest = take_algorithm(args, args.calc_items, ("data",), command="calc")
    args.original_data = rest[0]
    data = transform_byte_order(load_data(args), args.byte_order)
    value = algorithm.compute(data)

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
    print_algorithm_line(out, algorithm)
    out.kv("Data", data_summary(args, data))
    print_byte_order_line(out, args.byte_order)
    result_text = format_crc_result(value, algorithm.width, args.output_format)
    main_value, _, decimal_part = result_text.partition(" ")
    shown = out.style(main_value, "value") + (f" {out.style(decimal_part, 'dim')}" if decimal_part else "")
    out.kv("Result", shown, status="success")
    emit_input_note(out, args)
    return EXIT_OK


def print_algorithm_line(out: Output, algorithm: Algorithm) -> None:
    """``crc-16-modbus  poly=0x8005 init=...`` with the name highlighted and the parameters muted."""
    name = "custom CRC" if algorithm.name == "custom" else "custom sum" if algorithm.name == "custom-sum" else ""
    label = name_cell(out, algorithm) if not name else out.style(name, "credit")
    out.kv("Algorithm", f"{label}  {out.style(params_text(algorithm) + f' ({algorithm.width}-bit)', 'dim')}")


def byte_order_text(mode: str) -> str:
    """``swap16 (reverse bytes inside each 16-bit word)``."""
    description = describe_byte_order(mode).rstrip(".")
    return f"{mode} ({description[0].lower()}{description[1:]})"


def print_byte_order_line(out: Output, mode: str) -> None:
    if mode != "native":
        out.kv("Byte order", byte_order_text(mode))


def calc_all(args: argparse.Namespace, out: Output) -> int:
    if len(args.calc_items) != 1:
        raise CliError("--all takes exactly one data value.", hint="Example: crcforge calc 123456789 --all")
    if using_custom_crc(args) or using_custom_sum(args) or args.algorithm:
        raise CliError("--all cannot be combined with --algorithm or custom parameters.")
    args.original_data = args.calc_items[0]
    data = transform_byte_order(load_data(args), args.byte_order)
    algorithms = get_algorithms(width=args.width, family=args.family)
    if not algorithms:
        widths = sorted({a.width for a in get_algorithms(family=args.family)})
        raise CliError(
            f"No {args.family or 'catalog'} algorithms have a width of {args.width} bits.",
            hint=f"Available widths: {', '.join(map(str, widths))}.",
        )
    results = [(algorithm, algorithm.compute(data)) for algorithm in algorithms]
    if out.json:
        out.emit_json(
            {
                "data_length": len(data),
                "results": [{"name": a.name, "width": a.width, **value_json(v, a.width)} for a, v in results],
            }
        )
        return EXIT_OK
    if out.quiet:
        for algorithm, value in results:
            out.value(f"{algorithm.name} {format_value(value, algorithm.width)}")
        return EXIT_OK

    crc_count = sum(1 for algorithm, _ in results if algorithm.family == "crc")
    out.section("All Catalog Results")
    out.kv("Data", data_summary(args, data))
    if args.byte_order != "native":
        out.kv("Byte order", byte_order_text(args.byte_order))
    out.kv(
        "Algorithms",
        f"{len(results)} ({plural(crc_count, 'CRC')}, {plural(len(results) - crc_count, 'checksum')})",
    )
    print_results_table(out, results, args.output_format)
    emit_input_note(out, args)
    return EXIT_OK


_KIND_LABELS: dict[type, str] = {
    CrcAlgorithm: "CRC",
    XorChecksum: "XOR",
    FletcherChecksum: "Fletcher",
    Adler32Checksum: "Adler",
    BsdChecksum: "BSD sum",
    SysvChecksum: "SysV sum",
}


def kind_label(algorithm: Algorithm) -> str:
    """A short type name for tables: CRC, sum, one's/two's complement, XOR, ..."""
    if isinstance(algorithm, SumChecksum):
        return {"ones": "one's compl.", "twos": "two's compl."}.get(algorithm.complement, "sum")
    return _KIND_LABELS.get(type(algorithm), "checksum")


def group_by_width(algorithms: Sequence[Algorithm]) -> list[tuple[int, list[Algorithm], str]]:
    """Algorithms per width (CRCs first, then checksums) with a "31 CRCs, 8 checksums" caption."""
    by_width: dict[int, list[Algorithm]] = {}
    for algorithm in algorithms:
        by_width.setdefault(algorithm.width, []).append(algorithm)
    groups = []
    for width, group in sorted(by_width.items()):
        group.sort(key=lambda algorithm: (algorithm.family != "crc", algorithm.name))
        crcs = sum(1 for algorithm in group if algorithm.family == "crc")
        counts = []
        if crcs:
            counts.append(plural(crcs, "CRC"))
        if len(group) > crcs:
            counts.append(plural(len(group) - crcs, "checksum"))
        groups.append((width, group, ", ".join(counts)))
    return groups


def name_cell(out: Output, algorithm: Algorithm) -> str:
    return out.style(algorithm.name, "text" if algorithm.family == "crc" else "credit")


def bool_cell(out: Output, value: bool) -> str:
    return out.style("true" if value else "false", "dim")


def params_text(algorithm: Algorithm) -> str:
    """Parameters in one compact line, without the width (shown elsewhere)."""
    if isinstance(algorithm, CrcAlgorithm):
        width = algorithm.width
        return (
            f"poly={format_value(algorithm.poly, width)} init={format_value(algorithm.init, width)} "
            f"refin={str(algorithm.refin).lower()} refout={str(algorithm.refout).lower()} "
            f"xorout={format_value(algorithm.xor_out, width)}"
        )
    return ", ".join(part for part in describe_params(algorithm).split(", ") if not part.startswith("width="))


def checksum_mode_cell(out: Output, crc_mode: str) -> str:
    return out.style("as given", "dim") if crc_mode == "as-given" else out.style("byte-swapped", "warn")


def print_results_table(out: Output, results: Sequence[tuple[Algorithm, int]], output_format: str) -> None:
    """Results grouped by width: CRCs first, then checksums, with aligned value columns."""
    values = {id(algorithm): value for algorithm, value in results}
    value_headers = {"hex": ["Hex"], "dec": ["Decimal"], "bin": ["Binary"]}.get(output_format, ["Hex", "Decimal"])
    for width, group, caption in group_by_width([algorithm for algorithm, _ in results]):
        out.line()
        out.rule(f"{width}-bit", caption)
        rows = []
        for algorithm in group:
            value = values[id(algorithm)]
            cells = {
                "Hex": out.style(format_value(value, width), "value"),
                "Decimal": out.style(str(value), "value" if output_format == "dec" else "dim"),
                "Binary": out.style(f"0b{value:0{width}b}", "value"),
            }
            rows.append(
                [name_cell(out, algorithm), out.style(kind_label(algorithm), "dim")] + [cells[h] for h in value_headers]
            )
        out.table(["Algorithm", "Type", *value_headers], rows, align="<<" + ">" * len(value_headers))


def print_matches_table(out: Output, matches: Sequence[tuple[str, str, Algorithm]], show_input: bool = False) -> None:
    """Catalog matches: algorithm, type, which byte order and checksum interpretation matched, parameters."""
    show_input = show_input or any(mode != "native" for mode, _, _ in matches)
    show_checksum = any(crc_mode != "as-given" for _, crc_mode, _ in matches)
    headers = ["Algorithm", "Type"] + (["Input"] if show_input else []) + (["Checksum"] if show_checksum else [])
    rows = []
    for mode, crc_mode, algorithm in matches:
        row = [name_cell(out, algorithm), out.style(kind_label(algorithm), "dim")]
        if show_input:
            row.append(mode)
        if show_checksum:
            row.append(checksum_mode_cell(out, crc_mode))
        row.append(out.style(params_text(algorithm), "dim"))
        rows.append(row)
    out.table([*headers, "Parameters"], rows)


def print_crc_table(out: Output, entries: Sequence[tuple[CrcMatch, str]]) -> None:
    """Custom CRC parameter sets: one row each, with an optional note (catalog name, equivalence)."""
    show_input = any(match.input_mode != "native" for match, _ in entries)
    show_checksum = any(match.crc_mode != "as-given" for match, _ in entries)
    headers = (["Input"] if show_input else []) + (["Checksum"] if show_checksum else [])
    headers += ["Poly", "Init", "RefIn", "RefOut", "XorOut", "Note"]
    rows = []
    for match, note in entries:
        algorithm = match.algorithm
        width = algorithm.width
        row = []
        if show_input:
            row.append(match.input_mode)
        if show_checksum:
            row.append(checksum_mode_cell(out, match.crc_mode))
        row += [
            out.style(format_value(algorithm.poly, width), "value"),
            format_value(algorithm.init, width),
            bool_cell(out, algorithm.refin),
            bool_cell(out, algorithm.refout),
            format_value(algorithm.xor_out, width),
            out.style(note, "success" if note.startswith("=") else "dim"),
        ]
        rows.append(row)
    out.table(headers, rows)


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
    out.kv("Data", data_summary(args, data))
    out.kv("Byte-order scan", "all supported modes" if args.scan_byte_order else args.byte_order)
    report_skipped(out, skipped)
    if not matches:
        out.kv("Matches", "0", status="warn")
        out.failure("No catalogued CRC or checksum algorithm matched.")
        hints = [] if args.scan_byte_order else ["--scan-byte-order"]
        hints += ["'crcforge brute' for custom parameters", "'crcforge solve' with more samples"]
        out.note("Try " + ", ".join(hints[:-1]) + " or " + hints[-1] + ".")
        emit_input_note(out, args)
        return EXIT_NO_MATCH
    out.kv("Matches", str(len(matches)), status="success")
    out.line()
    print_matches_table(out, matches, show_input=args.scan_byte_order or args.byte_order != "native")
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
    out.kv("Mode", "built-in catalog")
    out.kv("Checksum", format_value(checksum.value, display_width))
    out.kv("Data", data_summary(args, data))
    out.kv("Tried", f"{tested} algorithm/checksum combinations across {scanned} input byte-order modes.")
    out.kv("Algorithms", f"{len(algorithm_names)} built-in entries")
    report_skipped(out, skipped)
    if not matches:
        out.failure("Brute scan found no catalogued CRC or checksum match.")
        out.note("For a custom CRC, add --poly SPEC or --full-custom; with several samples use 'crcforge solve'.")
        emit_input_note(out, args)
        return EXIT_NO_MATCH
    out.kv("Matches", str(len(matches)), status="success")
    out.line()
    print_matches_table(out, matches, show_input=True)
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
        polys = range(1, mask + 1, 2)  # every odd polynomial; even ones are degenerate
    else:
        raise CliError(
            f"Scanning all 2^{width} polynomials is not practical.",
            hint="Pass --poly, or run 'crcforge solve' with two or more samples of the same length; "
            "it derives the polynomial directly.",
        )
    # Left out, init and xorout are free in --full-custom mode and try 0 and all-ones with --poly.
    conventional_values = sorted({0, mask})
    fallback = None if args.full_custom else conventional_values
    init_values = parse_value_spec(args.init, mask, "init") if args.init else fallback
    xorout_values = parse_value_spec(args.xor_out, mask, "xor-out") if args.xor_out else fallback
    defaulted = [name for name, given in (("init", args.init), ("xorout", args.xor_out)) if not given]
    conventional = False
    if init_values is None and xorout_values is None:
        init_values = xorout_values = conventional_values
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
    if not (init_count <= ENUMERATE_LIMIT or xorout_count <= ENUMERATE_LIMIT):
        solved = "init and xorout solved jointly"
    else:
        solved = ", ".join(
            f"{name}=solved directly"
            if values is None
            else f"{name}={len(values):,} value{'s' if len(values) != 1 else ''}"
            for name, values in (("init", init_values), ("xorout", xorout_values))
        )
    jobs = resolve_jobs(args.jobs, work * max(1, width // 4))

    if out.human:
        out.section("CRC Brute Scan")
        out.kv("Mode", "custom parameters")
        out.kv("Checksum", format_value(checksum.value, max(8, width)))
        out.kv("Width", f"{width} bits" + (f" ({width_note})" if width_note else ""))
        out.kv("Data", data_summary(args, data))
        out.kv("Search space", f"poly={len(polys)}, {solved}, refin/refout={len(reflections)}")
        out.kv(
            "Evaluations",
            f"{work:,} across {len(views)} input/checksum views" + (f", {jobs} workers" if jobs > 1 else ""),
        )
        report_skipped(out, skipped)
        if conventional:
            out.note(
                "With one sample, init and xorout are limited to 0 and all-ones. Use --init/--xor-out to change this."
            )
        elif defaulted and not args.full_custom:
            out.note(
                f"No --{' or --'.join(name.replace('xorout', 'xor-out') for name in defaulted)} given, so "
                f"{' and '.join(defaulted)} {'tries' if len(defaulted) == 1 else 'try'} 0 and all-ones."
            )
        if chance > 1:
            out.warn(
                f"About {chance:,.0f} matches are expected by chance. Narrow the search or use 'crcforge solve' "
                "with more samples."
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
    out.line()
    print_crc_table(out, [(match, f"= {match.catalog_name}" if match.catalog_name else "") for match in scan.matches])
    if scan.match_count > len(scan.matches):
        out.note(f"Showing {len(scan.matches)} of {scan.match_count:,} matches; raise --limit to see more.")
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
    groups = group_crc_matches(visible_crc_matches(result))
    catalog_checksums = [m for m in result.checksum_matches if not m.algorithm.name.startswith("custom")]
    layouts = [m for m in result.checksum_matches if m.algorithm.name.startswith("custom")]
    known = bool(result.catalog_matches or catalog_checksums)

    catalog = [(m.input_mode, m.crc_mode, m.algorithm) for m in [*result.catalog_matches, *catalog_checksums]]
    if catalog:
        out.line()
        out.success("Catalog matches")
        print_matches_table(out, catalog, show_input=show_modes)

    derived_notes: list[str] = []
    if groups and known and not all_solutions:
        out.line()
        out.kv(
            "Other fits",
            f"{plural(len(groups), 'other CRC parameter set')} also fit these samples; "
            "they are most likely coincidences.",
            status="dim",
        )
        out.detail("List them with --all-solutions or --json; more or longer samples rule them out.", status="dim")
    elif groups:
        derived_notes = result.derived_notes
        shown = groups if all_solutions else groups[:DERIVED_DISPLAY_LIMIT]
        entries: list[tuple[CrcMatch, str]] = []
        for group in shown:
            first = group[0]
            note = f"= {first.catalog_name}" if first.catalog_name else ""
            if first.free_bits and len(group) == 1:
                note = note or "init/xorout not unique"
            entries.append((first, note))
            entries.extend((twin, "equivalent to the row above") for twin in group[1:])
        out.line()
        out.success("Derived CRC parameters")
        print_crc_table(out, entries)
        out.kv("RevEng spec", shown[0][0].algorithm.reveng_spec(), status="dim")
        if len(groups) > len(shown):
            out.note(f"{len(groups) - len(shown)} more CRC parameter sets fit; use --all-solutions or --json.")
        if len(groups) > 1 and not known:
            out.warn("Several CRCs fit; add more samples to tell them apart.")

    if layouts and (known or groups) and not all_solutions:
        out.kv(
            "Other fits", f"{plural(len(layouts), 'sum/XOR layout')} also fit; most likely coincidences.", status="dim"
        )
    elif layouts:
        out.line()
        out.success("Sum and XOR layouts")
        shown_layouts = layouts if all_solutions else layouts[:DERIVED_DISPLAY_LIMIT]
        rows = [
            [out.style(kind_label(m.algorithm), "credit"), out.style(params_text(m.algorithm), "dim")]
            + ([m.input_mode] if show_modes else [])
            for m in shown_layouts
        ]
        out.table(["Type", "Layout"] + (["Input"] if show_modes else []), rows)
    if result.skipped_modes or result.notes or derived_notes:
        out.line()
    report_skipped(out, result.skipped_modes)
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
        "exhaustive": args.all_solutions,
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
    out.kv("Samples", f"{result.sample_count} ({lengths_text(result.lengths)})")
    out.kv("Width", f"{width} bits" + (f" ({width_note})" if width_note else ""))
    if not result.found:
        out.failure("No CRC or checksum fits every sample.")
        for note in result.notes:
            out.note(note)
        out.note("Try --scan-byte-order, another --width, or use --frames if the checksum is inside the data.")
        return EXIT_NO_MATCH
    if result.sample_count == 1:
        out.warn("Only one sample: matches may be coincidental. Add more samples to confirm.")
    print_solve_result(out, result, args.scan_byte_order, args.all_solutions)
    if result.sample_count == 2 and width <= 16:
        out.note("A third sample would confirm the result.")
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
    constant_layouts = 0
    for index, layout in enumerate(layouts):
        progress.update(index, len(layouts), layout.describe())
        try:
            samples = layout.split_all(frames)
        except ValueError:
            continue
        if any(not sample.data for sample in samples):
            continue
        if len(samples) > 1 and len({s.checksum for s in samples}) == 1 and len({s.data for s in samples}) > 1:
            # The same value in every frame (an address, a header byte) is not a checksum.
            constant_layouts += 1
            continue
        result = solve(samples, layout.width, swap_checksums=False, **solve_settings(args, layout.width))  # type: ignore[arg-type]
        if result.found:
            found.append((layout, result))
    progress.close()
    found.sort(key=layout_rank)
    best_tier = layout_rank(found[0])[0] if found else 0
    shown = (
        found
        if args.all_solutions
        else [item for item in found if layout_rank(item)[0] == best_tier][: 1 + 2 * (best_tier == 0)]
    )

    if out.json:
        out.emit_json(
            {
                "frames": len(frames),
                "layouts_tested": len(layouts),
                "constant_layouts_skipped": constant_layouts,
                "layouts": [{"layout": layout.as_dict(), **solve_json(result)} for layout, result in found],
            }
        )
        return EXIT_OK if found else EXIT_NO_MATCH
    if out.quiet:
        for layout, result in shown:
            out.value(f"# {layout.describe()}")
            quiet_solve(out, result, args.all_solutions)
        return EXIT_OK if found else EXIT_NO_MATCH

    out.section("CRC Solver (frames)")
    out.kv("Frames", f"{len(frames)} ({lengths_text(sorted({len(frame) for frame in frames}))})")
    out.kv("Layouts tested", str(len(layouts)))
    if len(frames) == 1:
        out.warn("Only one frame: matches may be coincidental. Add more frames to confirm.")
    if not found:
        out.failure("No layout produced a CRC or checksum that fits every frame.")
        out.note("Try --crc-at auto, --crc-endian auto, --skip auto, or a specific --width.")
        if constant_layouts:
            out.note(f"Skipped {plural(constant_layouts, 'layout')} whose checksum field is the same in every frame.")
        return EXIT_NO_MATCH
    for layout, result in shown:
        out.line()
        out.kv("Layout", out.style(layout.describe(), "frame"))
        print_solve_result(out, result, args.scan_byte_order, args.all_solutions)
    others = len(found) - len(shown)
    if others or constant_layouts:
        out.line()
    if others:
        out.note(
            f"{plural(others, 'other layout')} also fit (equivalent forms or coincidences); --all-solutions lists them."
        )
    if constant_layouts:
        out.note(f"Skipped {plural(constant_layouts, 'layout')} whose checksum field is the same in every frame.")
    return EXIT_OK


def layout_rank(item: tuple[FrameLayout, SolveResult]) -> tuple[int, int, int, bool, int]:
    """Catalog matches first, then layouts with fewer distinct CRC solutions, smaller skips, checksum at the end."""
    layout, result = item
    catalog = result.catalog_matches or [
        m for m in result.checksum_matches if not m.algorithm.name.startswith("custom")
    ]
    groups = len(group_crc_matches(visible_crc_matches(result))) or 99
    return (0 if catalog else 1, groups, layout.skip, layout.position != "end", layout.width)


def lengths_text(lengths: Sequence[int]) -> str:
    if len(lengths) == 1:
        return f"length {plural(lengths[0], 'byte')}"
    return f"lengths: {', '.join(map(str, lengths))} bytes"


def handle_verify(args: argparse.Namespace, out: Output) -> int:
    rest_names = ("data",) if args.frame else ("data", "checksum")
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
        if given > algorithm.mask:
            raise CliError(
                f"Checksum {format_value(given, max(width, given.bit_length()))} does not fit in {width} bits.",
                hint=f"{algorithm.name} produces {width}-bit values; check the checksum or the algorithm.",
            )
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
    print_algorithm_line(out, algorithm)
    out.kv("Data", data_summary(args, data))
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
    elif args.frame:
        out.note("Neither byte order of the stored checksum matches; check the algorithm, --crc-at and --skip.")
    return EXIT_NO_MATCH


def handle_forge(args: argparse.Namespace, out: Output) -> int:
    algorithm, rest = take_algorithm(args, args.items, ("data", "target"), command="forge")
    args.original_data = rest[0]
    data = load_data(args)
    target = parse_checksum_value(rest[1]).value
    offset = parse_offset(args.at, len(data)) if args.at is not None else None
    result = forge(data, algorithm, target, offset=offset, overwrite=args.overwrite, patch_size=args.patch_size)
    if args.output:
        write_output(args.output, result.data)

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
    print_algorithm_line(out, algorithm)
    out.kv("Data", data_summary(args, data))
    out.kv("Target", format_value(target, algorithm.width))
    action = "overwritten" if result.overwrite else "inserted"
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
        text = io_helpers.read_file_bytes(args.file).decode("ascii", errors="replace")
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
        write_output(args.output, code.encode("utf-8"))
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
    out.kv("Data", data_summary(args, data))
    out.kv("Byte order", byte_order_text(args.byte_order))
    out.kv("Result", format_bytes(transformed, args.output_format) if transformed else "(empty)", status="success")
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
    out.kv("Result", out.style("PASSED", "success"), status="success")
    out.kv("Algorithms", f"{len(FULL_CATALOG)} ({crc_count} CRCs, {len(FULL_CATALOG) - crc_count} checksums)")
    out.kv("Checked", f"every check value for the ASCII string {CHECK_INPUT.decode()}, plus the residue of each CRC")
    return EXIT_OK


def handle_banner(args: argparse.Namespace, out: Output) -> int:
    out.line(render_banner(out.color, out.unicode))
    out.line(render_credit_lines(out.color, out.unicode))
    return EXIT_OK


# --- Parser --------------------------------------------------------------------------------


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
        "--as-file", action="store_true", help="Treat the data argument as a file path ('-' reads stdin)."
    )
    group.add_argument(
        "--as-text", action="store_true", help="Treat the data as plain text, even if it starts with 0x or 0b."
    )
    group.add_argument(
        "--offset", type=int, default=0, metavar="N", help="Skip this many bytes at the start of the data."
    )
    group.add_argument("--length", type=int, metavar="N", help="Use only this many bytes (after --offset).")
    group.add_argument(
        "--exclude-tail",
        type=int,
        default=0,
        metavar="N",
        help="Ignore the last N bytes (for example a stored checksum).",
    )


DATA_HELP = "Data to process: 0x.. hex bytes, 0b.. binary bytes, '-' for stdin, otherwise text."
CHECKSUM_HELP = "Expected checksum: decimal, 0x hex or 0b binary."


def add_original_data(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("original_data", metavar="data", help=DATA_HELP)
    add_input_options(parser)


def add_byte_order(parser: argparse.ArgumentParser, allow_scan: bool = False, help_text: str = "") -> None:
    parser.add_argument(
        "--byte-order",
        default="native",
        choices=BYTE_ORDER_MODES,
        metavar="MODE",
        help=help_text or "Transform the data bytes first: native, reverse, swap16, swap32 or swap64.",
    )
    if allow_scan:
        parser.add_argument("--scan-byte-order", action="store_true", help="Also try every other byte-order mode.")


def add_crc_params(parser: argparse.ArgumentParser, spec: bool) -> None:
    group = parser.add_argument_group("custom CRC parameters" + (" (VALUE, A,B,C or START:END[:STEP])" if spec else ""))
    value = "SPEC" if spec else "VALUE"
    flag = "true|false|auto" if spec else "true|false"
    group.add_argument("--width", type=int, metavar="BITS", help="CRC width in bits.")
    group.add_argument(
        "--poly", metavar=value, help="Polynomial without the top bit" + (" (values to try)." if spec else ".")
    )
    group.add_argument(
        "--init",
        metavar=value,
        help="Initial register value in RevEng notation" + (" (values to try)." if spec else ". Default: 0."),
    )
    group.add_argument(
        "--xor-out",
        dest="xor_out",
        metavar=value,
        help="Final XOR value" + (" (values to try)." if spec else ". Default: 0."),
    )
    group.add_argument(
        "--refin", metavar=flag, help="Reflect each input byte." + (" Default: auto." if spec else " Default: false.")
    )
    group.add_argument(
        "--refout", metavar=flag, help="Reflect the result." + (" Default: auto." if spec else " Default: false.")
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
        help="Where the checksum sits in the frame. Default: end." + (" 'auto' tries both." if auto else ""),
    )
    group.add_argument(
        "--crc-endian",
        choices=("big", "little", "auto"),
        default="auto",
        help="Byte order of the stored checksum. Default: auto (try both).",
    )
    if auto:
        group.add_argument(
            "--skip",
            default="0",
            metavar="N|auto",
            help="Header bytes excluded from the data, or 'auto' (0-8). Default: 0.",
        )
    else:
        group.add_argument(
            "--skip", type=int, default=0, metavar="N", help="Header bytes excluded from the data. Default: 0."
        )


SUMMARY = f"{TOOL_NAME} calculates, identifies, reverse-engineers and forges CRCs and checksums."

COMMAND_GROUPS = [
    ("Identify", ["find", "solve", "brute"]),
    ("Calculate and check", ["calc", "verify", "records"]),
    ("Modify and generate", ["forge", "codegen", "transform"]),
    ("Catalog", ["list", "info", "self-test", "banner"]),
]

INPUT_RULES = [
    ("0xDEADBEEF", "hex bytes; spaces and underscores are ignored"),
    ("0b01001000", "binary bytes, 8 bits per byte"),
    ("-", "raw bytes from stdin"),
    ("FILE --as-file", "raw bytes of a file"),
    ("anything else", "UTF-8 text (--as-text forces text)"),
    ("checksums", "decimal by default: 19255, 0x4B37 and 0b0100101100110111 are equal"),
]

MAIN_EXAMPLES = [
    ("calculate a known CRC", "crcforge calc crc-16-modbus 123456789"),
    ("every 16-bit algorithm at once", "crcforge calc 123456789 --all --width 16"),
    ("which built-in algorithm made this checksum?", "crcforge find 0x4B37 123456789"),
    ("derive CRC parameters from samples", "crcforge solve PING:0x3197 PONG:0x16A1 PUSH:0x4524 STATUS:0x7CB4"),
    (
        "same, with the checksum inside each frame",
        "crcforge solve --frames 010300000001840A 010300000002C40B 01030000000AC5CD",
    ),
    ("append 4 bytes so the CRC becomes 0xDEADBEEF", 'crcforge forge crc-32 "hello world" 0xDEADBEEF'),
]


def build_parser() -> CrcforgeParser:
    parser = CrcforgeParser(prog="crcforge")
    parser.banner = lambda color, unicode: f"{render_banner(color, unicode)}\n{render_credit_lines(color, unicode)}"
    parser.summary = SUMMARY
    parser.command_groups = COMMAND_GROUPS
    parser.main_sections = [("Input", INPUT_RULES)]
    parser.examples = [command for _, command in MAIN_EXAMPLES]
    parser.example_comments = [comment for comment, _ in MAIN_EXAMPLES]
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_common_options(parser, suppress=False)
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", parser_class=CrcforgeParser)
    subparsers.required = True
    common = CrcforgeParser(add_help=False)
    add_common_options(common, suppress=True)

    def command(
        name: str,
        help_text: str,
        description: str,
        handler: Callable[..., int],
        usage: str,
        examples: Sequence[str] = (),
        notes: Sequence[str] = (),
        arguments: Sequence[tuple[str, str]] = (),
    ) -> CrcforgeParser:
        sub = subparsers.add_parser(name, help=help_text, description=description, usage=usage, parents=[common])
        sub.summary = help_text
        sub.examples = list(examples)
        sub.notes = list(notes)
        sub.argument_docs = list(arguments)
        parser.command_parsers[name] = sub
        parser.command_help[name] = help_text
        sub.set_defaults(handler=handler)
        return sub

    calc = command(
        "calc",
        "Calculate a CRC or checksum, or every algorithm at once.",
        "Calculate a built-in algorithm, custom CRC parameters, a custom sum checksum, or every catalog entry.",
        handle_calc,
        "crcforge calc <algorithm> <data> [options]\n"
        "crcforge calc <data> --all [--width BITS] [--family crc|checksum]\n"
        "crcforge calc <data> --width BITS --poly VALUE [--init VALUE] [--xor-out VALUE] [--refin ...]\n"
        "crcforge calc <data> --sum [--width BITS] [--word-size BITS] [--complement none|ones|twos]",
        examples=[
            "crcforge calc crc-16-modbus 123456789",
            "crcforge calc 123456789 --algorithm crc-32",
            "crcforge calc 0x313233 --width 16 --poly 0x1021 --init 0xFFFF",
            "crcforge calc 123456789 --sum --width 16 --word-size 16 --complement ones",
            "crcforge calc firmware.bin --as-file --all --family crc",
        ],
        arguments=[
            (
                "algorithm",
                "Built-in algorithm name or alias. Leave it out with --all, --algorithm or custom parameters.",
            ),
            ("data", DATA_HELP),
        ],
    )
    calc.add_argument("calc_items", nargs="+", metavar="data", help=DATA_HELP)
    calc.add_argument("--algorithm", metavar="NAME", help="Built-in algorithm name or alias.")
    calc.add_argument(
        "--all", action="store_true", help="Calculate every catalog algorithm (filter with --width/--family)."
    )
    calc.add_argument("--family", choices=FAMILIES, help="With --all: only CRCs or only checksums.")
    calc.add_argument(
        "--output-format",
        choices=("hex", "dec", "bin", "both"),
        default="both",
        metavar="FORMAT",
        help="Result format: hex, dec, bin or both. Default: both.",
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
        "crcforge find <checksum> <data> [options]",
        examples=[
            "crcforge find 0x4B37 123456789",
            "crcforge find 19255 123456789 --width 16",
            "crcforge find 0x1A47 0xA1B2C3D4 --scan-byte-order",
        ],
    )
    find.add_argument("checksum", help=CHECKSUM_HELP)
    add_original_data(find)
    find.add_argument("--width", type=int, metavar="BITS", help="Only algorithms of this width.")
    find.add_argument("--family", choices=FAMILIES, help="Only CRCs or only checksums.")
    find.add_argument("--algorithm", action="append", metavar="NAME", help="Only this algorithm (repeatable).")
    add_byte_order(find, allow_scan=True)

    brute = command(
        "brute",
        "Scan every byte order, or search custom CRC parameters.",
        "Without custom flags, try every catalog algorithm on every byte order. With --poly or --full-custom, "
        "search custom CRC parameters.",
        handle_brute,
        "crcforge brute <checksum> <data> [options]\n"
        "crcforge brute <checksum> <data> --width BITS --poly SPEC [--init SPEC] [--xor-out SPEC]\n"
        "crcforge brute <checksum> <data> --full-custom [--refin ...] [--refout ...]",
        examples=[
            "crcforge brute 0x4B37 123456789",
            "crcforge brute 0x4B37 123456789 --width 16 --poly 0x8005 --init 0:0xFFFF",
            "crcforge brute 0x31C3 123456789 --full-custom --refin false --refout false",
        ],
        notes=["With one sample, wide searches match by coincidence; 'crcforge solve' with more samples is exact."],
    )
    brute.add_argument("checksum", help=CHECKSUM_HELP)
    add_original_data(brute)
    brute.add_argument(
        "--algorithm", action="append", metavar="NAME", help="Catalog mode: only this algorithm (repeatable)."
    )
    brute.add_argument("--family", choices=FAMILIES, help="Catalog mode: only CRCs or only checksums.")
    add_crc_params(brute, spec=True)
    brute.add_argument(
        "--full-custom",
        action="store_true",
        help="Custom mode: search every odd poly (and free init/xorout); width defaults to the checksum's digits.",
    )
    brute.add_argument(
        "--max-combinations",
        type=int,
        default=50_000_000,
        metavar="N",
        help="Safety limit on evaluations. Default: 50,000,000.",
    )
    brute.add_argument("--limit", type=int, default=100, metavar="N", help="Maximum matches to show. Default: 100.")
    brute.add_argument("--jobs", type=int, default=0, metavar="N", help="Worker processes; 0 = automatic (default).")
    brute.add_argument(
        "--progress-interval",
        metavar="SECONDS",
        type=float,
        default=2.0,
        help="Seconds between progress lines when not on a terminal; 0 disables progress.",
    )

    solve_parser = command(
        "solve",
        "Derive CRC parameters from several samples or frames.",
        "Find the CRC or checksum behind several data/checksum samples. Samples of the same length give the "
        "polynomial; different lengths pin down init and xorout.",
        handle_solve,
        "crcforge solve <data:checksum> <data:checksum> ... [options]\n"
        "crcforge solve --frames <frame> <frame> ... [options]\n"
        "crcforge solve --samples-file FILE [--frames] [options]",
        examples=[
            "crcforge solve 0x0102:0xE181 0x0304:0x8300 0x050607:0xA323",
            "crcforge solve PING:0x3197 PONG:0x16A1 PUSH:0x4524 STATUS:0x7CB4",
            "crcforge solve --samples-file samples.txt --width 32",
            "crcforge solve --frames 010300000001840A 010300000002C40B 01030000000AC5CD",
            "crcforge solve --frames @frame1.bin @frame2.bin --crc-at auto --skip auto",
        ],
        notes=[
            "Sample syntax: DATA:CHECKSUM, where DATA follows the usual input rules or is @file. "
            "Samples files hold one sample (or frame) per line; # starts a comment."
        ],
        arguments=[
            ("data:checksum", "A sample: the data and its checksum, for example 0x313233:0x7A75 or hello:0x34F6."),
            ("frame", "With --frames: a whole captured frame in hex (or @file) with the checksum inside it."),
        ],
    )
    solve_parser.add_argument("samples", nargs="*", metavar="sample", help="DATA:CHECKSUM pairs, or frames.")
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
    solve_parser.add_argument(
        "--limit", type=int, default=64, metavar="N", help="Maximum CRC solutions to compute. Default: 64."
    )
    solve_parser.add_argument(
        "--all-solutions", action="store_true", help="List every fitting parameter set, not just the likely answer."
    )
    solve_parser.add_argument(
        "--progress-interval",
        metavar="SECONDS",
        type=float,
        default=2.0,
        help="Seconds between progress lines when not on a terminal; 0 disables progress.",
    )

    verify = command(
        "verify",
        "Check that data and a checksum belong together.",
        "Verify a checksum. With --frame, the checksum is read from the end (or start) of the data itself.",
        handle_verify,
        "crcforge verify <algorithm> <data> <checksum> [options]\n"
        "crcforge verify <algorithm> <frame> --frame [options]",
        examples=[
            "crcforge verify crc-16-modbus 123456789 0x4B37",
            "crcforge verify crc-16-modbus 0x010300000001840A --frame",
            "crcforge verify crc-32 firmware.bin 0xCBF43926 --as-file",
        ],
        arguments=[
            ("algorithm", "Built-in algorithm name or alias (or use --algorithm / custom parameters)."),
            ("data", DATA_HELP),
            ("checksum", CHECKSUM_HELP + " Leave it out with --frame."),
        ],
    )
    verify.add_argument("items", nargs="+", metavar="value", help="<algorithm> <data> <checksum>.")
    verify.required_text = "<algorithm> <data> <checksum>"
    verify.add_argument("--algorithm", metavar="NAME", help="Built-in algorithm name or alias.")
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
        "crcforge forge <algorithm> <data> <target> [--at POS] [--overwrite] [-o FILE]",
        examples=[
            'crcforge forge crc-32 "hello world" 0xDEADBEEF',
            "crcforge forge crc-16-modbus firmware.bin 0x0000 --as-file --at 0x100 --overwrite -o patched.bin",
            "crcforge forge sum-8 0x0102030405 0x00",
        ],
        arguments=[
            ("algorithm", "Built-in algorithm name or alias (or use --algorithm / custom parameters)."),
            ("data", DATA_HELP),
            ("target", "The checksum the patched data should produce."),
        ],
    )
    forge_parser.add_argument("items", nargs="+", metavar="value", help="<algorithm> <data> <target>.")
    forge_parser.required_text = "<algorithm> <data> <target>"
    forge_parser.add_argument("--algorithm", metavar="NAME", help="Built-in algorithm name or alias.")
    forge_parser.add_argument(
        "--at",
        metavar="POS",
        help="Patch position: byte offset, negative from the end, 'start' or 'end'. Default: end.",
    )
    forge_parser.add_argument("--overwrite", action="store_true", help="Overwrite bytes at --at instead of inserting.")
    forge_parser.add_argument(
        "--patch-size", type=int, metavar="BYTES", help="Patch length. Default: the checksum width."
    )
    forge_parser.add_argument("-o", "--output", metavar="FILE", help="Write the forged bytes to FILE.")
    forge_parser.add_argument(
        "--output-format",
        choices=("hex", "compact-hex", "text"),
        metavar="FORMAT",
        help="How to print the forged data: hex, compact-hex or text.",
    )
    add_crc_params(forge_parser, spec=False)
    add_sum_params(forge_parser)
    add_input_options(forge_parser)

    records = command(
        "records",
        "Validate an Intel HEX or Motorola S-record file.",
        "Check every record checksum, record length and structure in an Intel HEX or S-record file.",
        handle_records,
        "crcforge records <file> [options]",
        examples=["crcforge records firmware.hex", "crcforge records firmware.s19 --json"],
    )
    records.add_argument("file", help="Path to the .hex/.s19/.srec file ('-' for stdin).")

    codegen = command(
        "codegen",
        "Generate CRC source code in C, Python or Rust.",
        "Generate a table-driven implementation of a built-in or custom CRC.",
        handle_codegen,
        "crcforge codegen <algorithm> [--lang c|python|rust] [--name NAME] [-o FILE]\n"
        "crcforge codegen --width BITS --poly VALUE [--init VALUE] ... [--lang c|python|rust]",
        examples=[
            "crcforge codegen crc-16-modbus --lang c -o crc16_modbus.c",
            "crcforge codegen --width 16 --poly 0x1021 --init 0xFFFF --lang python",
        ],
        arguments=[("algorithm", "Built-in algorithm name or alias. Leave it out with custom parameters.")],
    )
    codegen.add_argument("items", nargs="*", metavar="algorithm", help="Built-in algorithm name.")
    codegen.add_argument("--algorithm", help=argparse.SUPPRESS)
    codegen.add_argument("--lang", choices=LANGUAGES, default="c", help="Target language. Default: c.")
    codegen.add_argument("--name", help="Function name. Default: derived from the algorithm name.")
    codegen.add_argument("-o", "--output", metavar="FILE", help="Write the code to FILE.")
    add_crc_params(codegen, spec=False)

    transform = command(
        "transform",
        "Reverse or swap the byte order of data.",
        "Transform the data's byte order and print the result, without any checksum work.",
        handle_transform,
        "crcforge transform <data> --byte-order MODE [--output-format hex|compact-hex|text]",
        examples=[
            "crcforge transform 0xA9BB7BFD --byte-order swap32",
            "crcforge transform 0x48656C6C6F --output-format text",
        ],
    )
    add_original_data(transform)
    add_byte_order(transform, help_text="native, reverse, swap16, swap32 or swap64. Default: native.")
    transform.add_argument(
        "--output-format",
        choices=("hex", "compact-hex", "text"),
        default="hex",
        metavar="FORMAT",
        help="Render as hex (spaced), compact-hex or text (UTF-8). Default: hex.",
    )

    list_parser = command(
        "list",
        f"List the {len(FULL_CATALOG)} built-in algorithms.",
        "List the built-in CRC and checksum catalog, grouped by width.",
        handle_list,
        "crcforge list [--width BITS] [--family crc|checksum] [--search TEXT] [--aliases]",
        examples=[
            "crcforge list --width 16",
            "crcforge list --family checksum",
            "crcforge list --search modbus --aliases",
        ],
    )
    list_parser.add_argument("--width", type=int, metavar="BITS", help="Only this width.")
    list_parser.add_argument("--family", choices=FAMILIES, help="Only CRCs or only checksums.")
    list_parser.add_argument("--search", metavar="TEXT", help="Only names or aliases containing this text.")
    list_parser.add_argument("--aliases", action="store_true", help="Also print alias names.")

    info = command(
        "info",
        "Show one algorithm in detail.",
        "Show parameters, aliases, check value, residue and reference.",
        handle_info,
        "crcforge info <algorithm>",
        examples=["crcforge info crc-16-modbus", "crcforge info CRC-32/ISCSI", "crcforge info internet-checksum"],
    )
    info.add_argument("name", metavar="algorithm", help="Algorithm name or alias.")

    command(
        "self-test",
        "Check every algorithm against its published check value.",
        "Check every catalog entry against its published check value and residue.",
        handle_self_test,
        "crcforge self-test",
        examples=["crcforge self-test"],
    )
    command(
        "banner",
        "Print the banner.",
        "Print the banner, author credit and X/Twitter handle.",
        handle_banner,
        "crcforge banner",
        examples=["crcforge banner"],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    CrcforgeParser.no_color = "--no-color" in arguments
    CrcforgeParser.json_errors = "--json" in arguments
    if all(argument in GLOBAL_FLAGS for argument in arguments):
        parser.print_help()
        return EXIT_OK
    words = [argument for argument in arguments if argument not in GLOBAL_FLAGS]
    if words and words[0] == "help":
        if len(words) == 1:
            parser.print_help()
            return EXIT_OK
        if words[1] in parser.command_parsers:
            parser.command_parsers[words[1]].print_help()
            return EXIT_OK
        parser.parse_args([words[1]])  # reports the unknown command with suggestions
    args = parser.parse_args(arguments)
    out = Output(json_mode=args.json, quiet=args.quiet, color=False if args.no_color else None)
    try:
        if args.show_banner and out.human:
            out.line(render_banner(out.color, out.unicode))
            out.line(render_credit_lines(out.color, out.unicode))
            out.line()
        return int(args.handler(args, out))
    except UnknownAlgorithmError as error:
        hint = f"Did you mean: {', '.join(error.suggestions)}?" if error.suggestions else None
        out.error(f"Unknown algorithm: {error.name}", hint or "Run 'crcforge list' to browse the catalog.")
        return EXIT_USAGE
    except UsageError as error:
        parser.command_parsers[error.command].report(str(error), error.hint, with_usage=True)
        return EXIT_USAGE
    except InputError as error:
        out.error(str(error), error.hint)
        return EXIT_USAGE
    except (ForgeError, CodegenError, ValueError) as error:
        out.error(str(error))
        return EXIT_USAGE
    except OSError as error:
        where = f" ({error.filename})" if getattr(error, "filename", None) else ""
        out.error(f"{error.strerror or error}{where}.")
        return EXIT_USAGE
    except KeyboardInterrupt:
        out.error("Interrupted.")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
