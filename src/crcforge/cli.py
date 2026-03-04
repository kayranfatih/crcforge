from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import io as io_helpers
from .algorithms import (
    CrcAlgorithm,
    calculate_crc,
    describe_algorithm,
    format_crc,
    get_algorithm,
    get_algorithms,
    validate_catalog,
)
from .byteorder import BYTE_ORDER_MODES, describe_byte_order, transform_byte_order


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

ANSI_RESET = "\033[0m"
ANSI_FRAME = "\033[38;5;39m"
ANSI_TEXT = "\033[38;5;51m"
ANSI_CREDIT = "\033[38;5;220m"
ANSI_INFO = "\033[38;5;45m"
ANSI_WARN = "\033[38;5;214m"
ANSI_ERROR = "\033[38;5;196m"
ANSI_SUCCESS = "\033[38;5;82m"


@dataclass
class ModeReport:
    mode: str
    tested_count: int
    skipped_reason: Optional[str] = None


@dataclass
class MatchReport:
    input_mode: str
    crc_mode: str
    description: str


@dataclass(frozen=True)
class ParsedChecksum:
    value: int
    width_hint: int


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


def supports_color(stream: Optional[object] = None) -> bool:
    target = stream if stream is not None else sys.stdout
    if os.getenv("NO_COLOR") is not None:
        return False
    if os.getenv("FORCE_COLOR") is not None:
        return True
    return bool(getattr(target, "isatty", lambda: False)())


def colorize(text: str, ansi_code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{ansi_code}{text}{ANSI_RESET}"


def style(text: str, ansi_code: str, stream: Optional[object] = None) -> str:
    return colorize(text, ansi_code, supports_color(stream))


def print_info(text: str) -> None:
    print(style(text, ANSI_INFO))


def print_success(text: str) -> None:
    print(style(text, ANSI_SUCCESS))


def print_warning(text: str) -> None:
    print(style(text, ANSI_WARN))


def print_error(text: str) -> None:
    print(style(text, ANSI_ERROR, sys.stderr), file=sys.stderr)


def print_section(title: str) -> None:
    print(style(title, ANSI_FRAME))


def print_kv(label: str, value: str, status: str = "info") -> None:
    palette = {
        "info": ANSI_INFO,
        "success": ANSI_SUCCESS,
        "warn": ANSI_WARN,
        "error": ANSI_ERROR,
        "text": ANSI_TEXT,
    }
    colored_label = style(f"{label:16}", palette.get(status, ANSI_INFO))
    print(f"{colored_label} {value}")


def render_banner() -> str:
    colored = supports_color()
    lines = []
    for line in PLAIN_BANNER.splitlines():
        if line.startswith("+") or line.startswith("|"):
            lines.append(colorize(line, ANSI_FRAME, colored))
        else:
            lines.append(colorize(line, ANSI_TEXT, colored))
    return "\n".join(lines)


def render_credit_lines() -> str:
    colored = supports_color()
    return "\n".join(
        (
            colorize(AUTHOR_LINE, ANSI_CREDIT, colored),
            colorize(SOCIAL_LINE, ANSI_CREDIT, colored),
        )
    )


def parse_crc_value(raw_value: str) -> int:
    return io_helpers.parse_crc_value(raw_value)


def parse_checksum_value(raw_value: str) -> ParsedChecksum:
    parsed = io_helpers.parse_checksum_value(raw_value)
    return ParsedChecksum(parsed.value, parsed.width_hint)


def normalize_literal(raw_value: str) -> str:
    return io_helpers.normalize_literal(raw_value)


def parse_hex_bytes(raw_value: str) -> bytes:
    return io_helpers.parse_hex_bytes(raw_value)


def parse_binary_bytes(raw_value: str) -> bytes:
    return io_helpers.parse_binary_bytes(raw_value)


def is_hex_literal(raw_value: str) -> bool:
    return io_helpers.is_hex_literal(raw_value)


def is_binary_literal(raw_value: str) -> bool:
    return io_helpers.is_binary_literal(raw_value)


def load_input_bytes(args: argparse.Namespace) -> bytes:
    return io_helpers.load_input_bytes(args)


def parse_spec_value(raw_value: str) -> int:
    return parse_crc_value(raw_value)


def parse_value_spec(raw_value: str, mask: int, label: str) -> List[int]:
    values: List[int] = []
    seen = set()
    for part in raw_value.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            bounds = [item.strip() for item in token.split(":")]
            if len(bounds) not in (2, 3):
                raise ValueError(
                    f"Invalid {label} range '{token}'. Use START:END or START:END:STEP."
                )
            start = parse_spec_value(bounds[0])
            end = parse_spec_value(bounds[1])
            step = parse_spec_value(bounds[2]) if len(bounds) == 3 else 1
            if step <= 0:
                raise ValueError(f"{label} range step must be positive.")
            if start > end:
                raise ValueError(f"{label} range start must be <= end.")
            candidates = range(start, end + 1, step)
        else:
            candidates = [parse_spec_value(token)]

        for value in candidates:
            if value < 0 or value > mask:
                raise ValueError(
                    f"{label} value {format_crc(value, max(1, mask.bit_length()))} is outside "
                    f"the selected width."
                )
            if value not in seen:
                values.append(value)
                seen.add(value)

    if not values:
        raise ValueError(f"{label} specification is empty.")
    return values


def parse_reflect_option(raw_value: str) -> List[bool]:
    normalized = raw_value.strip().lower()
    if normalized == "auto":
        return [False, True]
    if normalized in ("true", "1", "yes", "on"):
        return [True]
    if normalized in ("false", "0", "no", "off"):
        return [False]
    raise ValueError("Reflection option must be one of: auto, true, false.")


def parse_reflect_value(
    raw_value: Optional[str],
    label: str,
    default: bool = False,
) -> bool:
    if raw_value is None:
        return default
    values = parse_reflect_option(raw_value)
    if len(values) != 1:
        raise ValueError(f"{label} must be true or false for direct calculation.")
    return values[0]


def describe_manual_params(
    width: int,
    poly: int,
    init: int,
    refin: bool,
    refout: bool,
    xor_out: int,
) -> str:
    return (
        f"width={width}, "
        f"poly={format_crc(poly, width)}, "
        f"init={format_crc(init, width)}, "
        f"refin={refin}, "
        f"refout={refout}, "
        f"xor_out={format_crc(xor_out, width)}"
    )


def using_custom_crc_params(args: argparse.Namespace) -> bool:
    return any(
        getattr(args, field, None) is not None
        for field in ("poly", "init", "xor_out", "refin", "refout")
    )


def using_full_custom_brute(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "full_custom", False))


def detect_original_data_kind(args: argparse.Namespace) -> str:
    return io_helpers.detect_original_data_kind(args)


def format_crc_result(value: int, width: int, output_format: str) -> str:
    return io_helpers.format_crc_result(value, width, output_format)


def format_bytes(data: bytes, output_format: str) -> str:
    return io_helpers.format_bytes(data, output_format)


def unique_modes(primary_mode: str, scan_all: bool) -> List[str]:
    if not scan_all:
        return [primary_mode]

    ordered_modes = [primary_mode]
    for mode in BYTE_ORDER_MODES:
        if mode != primary_mode:
            ordered_modes.append(mode)
    return ordered_modes


def maybe_print_banner(args: argparse.Namespace) -> None:
    if getattr(args, "show_banner", False):
        print(render_banner())
        print(render_credit_lines())


def checksum_variants(expected_crc: int, width: int) -> List[Tuple[str, int]]:
    variants = [("as-given", expected_crc)]
    if width <= 8 or width % 8 != 0:
        return variants

    byte_length = width // 8
    swapped = int.from_bytes(expected_crc.to_bytes(byte_length, byteorder="big"), "little")
    if swapped != expected_crc:
        variants.append(("byte-swapped", swapped))
    return variants


def search_catalog(
    raw_data: bytes,
    expected_crc: int,
    algorithm_names: Optional[Sequence[str]],
    width: Optional[int],
    modes: Sequence[str],
) -> Tuple[List[MatchReport], List[ModeReport], List[str]]:
    mode_matches: List[MatchReport] = []
    mode_reports: List[ModeReport] = []
    selected_algorithms = get_algorithms(names=algorithm_names, width=width)
    algorithm_names_used = [algorithm.name for algorithm in selected_algorithms]

    for mode in modes:
        try:
            data = transform_byte_order(raw_data, mode)
        except ValueError as error:
            mode_reports.append(ModeReport(mode=mode, tested_count=0, skipped_reason=str(error)))
            continue

        tested_count = 0
        for algorithm in selected_algorithms:
            if expected_crc > algorithm.mask:
                continue
            calculated = calculate_crc(data, algorithm)
            for crc_mode, candidate_crc in checksum_variants(expected_crc, algorithm.width):
                tested_count += 1
                if calculated == candidate_crc:
                    mode_matches.append(
                        MatchReport(
                            input_mode=mode,
                            crc_mode=crc_mode,
                            description=describe_algorithm(algorithm),
                        )
                    )
        mode_reports.append(ModeReport(mode=mode, tested_count=tested_count))

    return mode_matches, mode_reports, algorithm_names_used


def handle_list(args: argparse.Namespace) -> int:
    algorithms = get_algorithms(width=args.width)
    if not algorithms:
        print_error("No algorithms matched the requested filters.")
        return 1

    total_aliases = sum(len(algorithm.aliases) for algorithm in algorithms)

    maybe_print_banner(args)
    print_section("Built-in CRC Catalog")
    print_kv("Algorithms", str(len(algorithms)))
    print_kv("Aliases", str(total_aliases))
    print_kv("Names total", str(len(algorithms) + total_aliases))
    if args.width:
        print_kv("Width filter", str(args.width))
    for algorithm in algorithms:
        print_kv("Algorithm", describe_algorithm(algorithm), status="text")
        if args.aliases and algorithm.aliases:
            print_kv("Aliases", ", ".join(algorithm.aliases), status="text")
    return 0


def handle_calc(args: argparse.Namespace) -> int:
    custom_requested = using_custom_crc_params(args) or args.width is not None
    if args.algorithm and custom_requested:
        raise ValueError(
            "Do not combine --algorithm with custom CRC parameters. "
            "Use either a built-in algorithm or explicit custom parameters."
        )

    if args.algorithm:
        if len(args.calc_items) != 1:
            raise ValueError(
                "When using --algorithm, provide exactly one original-data value."
            )
        args.original_data = args.calc_items[0]
        algorithm = get_algorithm(args.algorithm)
    elif custom_requested:
        if len(args.calc_items) != 1:
            raise ValueError(
                "Custom calculation expects exactly one original-data value."
            )
        if args.width is None:
            raise ValueError("Custom calculation requires --width.")
        if args.poly is None:
            raise ValueError("Custom calculation requires --poly.")
        args.original_data = args.calc_items[0]
        width = args.width
        if width <= 0:
            raise ValueError("Width must be positive.")
        mask = (1 << width) - 1
        poly = parse_value_spec(args.poly, mask, "poly")
        if len(poly) != 1:
            raise ValueError("Custom calculation requires exactly one poly value.")
        init = parse_value_spec(args.init or "0x0", mask, "init")
        if len(init) != 1:
            raise ValueError("Custom calculation requires exactly one init value.")
        xor_out = parse_value_spec(args.xor_out or "0x0", mask, "xor-out")
        if len(xor_out) != 1:
            raise ValueError("Custom calculation requires exactly one xor-out value.")
        refin = parse_reflect_value(args.refin, "RefIn", default=False)
        refout = parse_reflect_value(args.refout, "RefOut", default=False)
        algorithm = CrcAlgorithm(
            name="manual",
            width=width,
            poly=poly[0],
            init=init[0],
            xor_out=xor_out[0],
            refin=refin,
            refout=refout,
        )
    else:
        if len(args.calc_items) != 2:
            raise ValueError(
                "Built-in calculation expects 'calc <algorithm> <original-data>'. "
                "For custom calculation use 'calc <original-data> --poly ... --width ...'."
            )
        algorithm = get_algorithm(args.calc_items[0])
        args.original_data = args.calc_items[1]

    data = transform_byte_order(load_input_bytes(args), args.byte_order)
    value = calculate_crc(data, algorithm)

    maybe_print_banner(args)
    print_section("CRC Calculation")
    print_kv("Mode", "custom-parameters" if algorithm.name == "manual" else "built-in")
    print_kv("Original data", detect_original_data_kind(args))
    print_kv("Data length", f"{len(data)} bytes")
    print_kv("Byte order", f"{args.byte_order} ({describe_byte_order(args.byte_order)})")
    if algorithm.name == "manual":
        print_kv(
            "Parameters",
            describe_manual_params(
                algorithm.width,
                algorithm.poly,
                algorithm.init,
                algorithm.refin,
                algorithm.refout,
                algorithm.xor_out,
            ),
        )
    else:
        print_kv("Algorithm", describe_algorithm(algorithm))
    print_kv(
        "Result",
        format_crc_result(value, algorithm.width, args.output_format),
        status="success",
    )
    return 0


def handle_find(args: argparse.Namespace) -> int:
    raw_data = load_input_bytes(args)
    checksum = parse_checksum_value(args.checksum)
    expected_crc = checksum.value
    checksum_width = max(8, args.width or checksum.width_hint)
    mode_matches, mode_reports, _ = search_catalog(
        raw_data=raw_data,
        expected_crc=expected_crc,
        algorithm_names=args.algorithm,
        width=args.width,
        modes=unique_modes(args.byte_order, args.scan_byte_order),
    )
    skipped_modes = [report for report in mode_reports if report.skipped_reason]

    if not mode_matches:
        maybe_print_banner(args)
        print_section("CRC Match Search")
        print_kv("Checksum", format_crc(expected_crc, checksum_width))
        print_kv("Original data", detect_original_data_kind(args))
        print_kv("Byte-order scan", "all supported modes" if args.scan_byte_order else args.byte_order)
        print_kv("Matches", "0", status="warn")
        print_error("No catalogued CRC algorithm matched.")
        for report in skipped_modes:
            print_warning(f"Skipped [{report.mode}]: {report.skipped_reason}")
        return 1

    maybe_print_banner(args)
    print_section("CRC Match Search")
    print_kv("Checksum", format_crc(expected_crc, checksum_width))
    print_kv("Original data", detect_original_data_kind(args))
    print_kv("Byte-order scan", "all supported modes" if args.scan_byte_order else args.byte_order)
    print_kv("Matches", str(len(mode_matches)), status="success")
    for report in skipped_modes:
        print_warning(f"Skipped [{report.mode}]: {report.skipped_reason}")

    show_mode = args.scan_byte_order or args.byte_order != "native"
    for match in mode_matches:
        if show_mode:
            print_kv(
                "Match",
                f"[input:{match.input_mode}][crc:{match.crc_mode}] {match.description}",
                status="success",
            )
        else:
            print_kv("Match", f"[crc:{match.crc_mode}] {match.description}", status="success")
    return 0


def handle_brute(args: argparse.Namespace) -> int:
    if using_custom_crc_params(args) or using_full_custom_brute(args):
        if args.algorithm:
            raise ValueError(
                "Do not combine --algorithm with custom brute parameters. "
                "Use either built-in catalog brute or custom brute parameters."
            )
        return run_custom_brute(args)

    raw_data = load_input_bytes(args)
    checksum = parse_checksum_value(args.checksum)
    expected_crc = checksum.value
    checksum_width = max(8, args.width or checksum.width_hint)
    mode_matches, mode_reports, algorithm_names_used = search_catalog(
        raw_data=raw_data,
        expected_crc=expected_crc,
        algorithm_names=args.algorithm,
        width=args.width,
        modes=list(BYTE_ORDER_MODES),
    )
    skipped_modes = [report for report in mode_reports if report.skipped_reason]
    tested_modes = [report for report in mode_reports if report.skipped_reason is None]
    total_tested = sum(report.tested_count for report in tested_modes)

    if not mode_matches:
        maybe_print_banner(args)
        print_section("CRC Brute Scan")
        print_kv("Mode", "built-in-catalog")
        print_kv("Checksum", format_crc(expected_crc, checksum_width))
        print_kv("Original data", detect_original_data_kind(args))
        print_kv(
            "Tried",
            f"{total_tested} algorithm/checksum combinations across {len(tested_modes)} input byte-order modes.",
        )
        print_error("Brute scan found no catalogued CRC match.")
        if algorithm_names_used:
            print_kv("Algorithms", f"{len(algorithm_names_used)} built-in entries")
            print_kv("Algorithm set", ", ".join(algorithm_names_used), status="text")
        for report in tested_modes:
            print_kv(
                "Scanned",
                f"[input:{report.mode}] tested {report.tested_count} algorithm/checksum combinations",
            )
        for report in skipped_modes:
            print_warning(f"Skipped [{report.mode}]: {report.skipped_reason}")
        return 1

    maybe_print_banner(args)
    print_section("CRC Brute Scan")
    print_kv("Mode", "custom-parameters" if using_custom_crc_params(args) else "built-in-catalog")
    print_kv("Checksum", format_crc(expected_crc, checksum_width))
    print_kv("Original data", detect_original_data_kind(args))
    print_kv(
        "Tried",
        f"{total_tested} algorithm/checksum combinations across {len(tested_modes)} input byte-order modes.",
    )
    if algorithm_names_used:
        print_kv(
            "Algorithms",
            f"{len(algorithm_names_used)} built-in entries",
        )
        print_kv("Algorithm set", ", ".join(algorithm_names_used), status="text")
    for report in tested_modes:
        print_kv(
            "Scanned",
            f"[input:{report.mode}] tested {report.tested_count} algorithm/checksum combinations",
        )
    for report in skipped_modes:
        print_warning(f"Skipped [{report.mode}]: {report.skipped_reason}")
    print_kv("Matches", str(len(mode_matches)), status="success")
    for match in mode_matches:
        print_kv(
            "Match",
            f"[input:{match.input_mode}][crc:{match.crc_mode}] {match.description}",
            status="success",
        )
    return 0


def run_custom_brute(args: argparse.Namespace) -> int:
    if args.progress_interval < 0:
        raise ValueError("--progress-interval must be 0 or greater.")

    checksum = parse_checksum_value(args.checksum)
    expected_crc = checksum.value
    width = args.width
    width_inferred = False
    if width is None:
        if not using_full_custom_brute(args):
            raise ValueError("Custom brute mode requires --width unless --full-custom is used.")
        width = checksum.width_hint
        width_inferred = True
    if width <= 0:
        raise ValueError("Width must be positive.")

    raw_data = load_input_bytes(args)
    mask = (1 << width) - 1

    full_custom = using_full_custom_brute(args)
    if args.poly is None and not full_custom:
        raise ValueError("Custom brute mode requires --poly unless --full-custom is used.")

    poly_count = len(parse_value_spec(args.poly, mask, "poly")) if args.poly is not None else mask + 1
    init_count = len(parse_value_spec(args.init, mask, "init")) if args.init is not None else (mask + 1 if full_custom else 1)
    xor_count = len(parse_value_spec(args.xor_out, mask, "xor-out")) if args.xor_out is not None else (mask + 1 if full_custom else 1)
    refin_values = parse_reflect_option(args.refin or "auto")
    refout_values = parse_reflect_option(args.refout or "auto")
    crc_variants = checksum_variants(expected_crc, width)

    prepared_modes: List[Tuple[str, bytes]] = []
    skipped_modes: List[ModeReport] = []
    for mode in BYTE_ORDER_MODES:
        try:
            prepared_modes.append((mode, transform_byte_order(raw_data, mode)))
        except ValueError as error:
            skipped_modes.append(ModeReport(mode=mode, tested_count=0, skipped_reason=str(error)))

    configs_per_mode = (
        poly_count
        * init_count
        * xor_count
        * len(refin_values)
        * len(refout_values)
    )
    estimated = len(prepared_modes) * configs_per_mode * len(crc_variants)
    if estimated > args.max_combinations:
        width_note = f" Inferred width is {width} bits from the checksum." if width_inferred else ""
        raise ValueError(
            "Custom brute search is too large. "
            f"Estimated {estimated} combinations exceeds limit {args.max_combinations}."
            f"{width_note} Narrow --poly/--init/--xor-out, use a smaller width, or raise --max-combinations."
        )

    poly_values = (
        parse_value_spec(args.poly, mask, "poly")
        if args.poly is not None
        else range(mask + 1)
    )
    init_values = (
        parse_value_spec(args.init, mask, "init")
        if args.init is not None
        else (range(mask + 1) if full_custom else [0])
    )
    xor_values = (
        parse_value_spec(args.xor_out, mask, "xor-out")
        if args.xor_out is not None
        else (range(mask + 1) if full_custom else [0])
    )

    matches: List[MatchReport] = []
    mode_reports: List[ModeReport] = []
    progress_enabled = estimated > 0 and args.progress_interval > 0
    processed_tests = 0
    start_time = time.monotonic()
    last_progress_time = start_time
    last_reported_tests = -1

    maybe_print_banner(args)
    print_section("CRC Brute Scan")
    print_kv("Mode", "custom-parameters")
    print_kv("Checksum", format_crc(expected_crc, max(8, width)))
    if width_inferred:
        print_kv("Width", f"{width} bits (inferred from checksum)")
    else:
        print_kv("Width", f"{width} bits")
    print_kv("Original data", detect_original_data_kind(args))
    print_kv(
        "Parameter space",
        f"poly={poly_count}, init={init_count}, xor_out={xor_count}, "
        f"refin={len(refin_values)}, refout={len(refout_values)}, crc_variants={len(crc_variants)}",
    )
    print_kv(
        "Estimated",
        f"{estimated} custom parameter/checksum combinations across {len(prepared_modes)} input byte-order modes.",
    )
    for report in skipped_modes:
        print_warning(f"Skipped [{report.mode}]: {report.skipped_reason}")
    if progress_enabled:
        print_info(
            f"Progress tracking enabled. Updates every {args.progress_interval:g}s while the brute scan is running."
        )

    def maybe_report_progress(mode: str, force: bool = False) -> None:
        nonlocal last_progress_time
        nonlocal last_reported_tests
        if not progress_enabled or estimated == 0:
            return
        now = time.monotonic()
        if not force and (now - last_progress_time) < args.progress_interval:
            return
        if force and last_reported_tests == processed_tests:
            return
        last_progress_time = now
        last_reported_tests = processed_tests
        percent = (processed_tests / estimated) * 100
        elapsed = now - start_time
        rate = processed_tests / elapsed if elapsed > 0 else 0.0
        print_info(
            f"Progress: {percent:6.2f}% ({processed_tests}/{estimated}) "
            f"[input:{mode}] at {rate:,.0f} combos/s"
        )

    for mode, data in prepared_modes:

        tested_count = 0
        for poly in poly_values:
            for init in init_values:
                for xor_out in xor_values:
                    for refin in refin_values:
                        for refout in refout_values:
                            algorithm = CrcAlgorithm(
                                name="manual",
                                width=width,
                                poly=poly,
                                init=init,
                                xor_out=xor_out,
                                refin=refin,
                                refout=refout,
                            )
                            calculated = calculate_crc(data, algorithm)
                            description = describe_manual_params(
                                width=width,
                                poly=poly,
                                init=init,
                                refin=refin,
                                refout=refout,
                                xor_out=xor_out,
                            )
                            for crc_mode, candidate_crc in crc_variants:
                                if calculated == candidate_crc:
                                    matches.append(
                                        MatchReport(
                                            input_mode=mode,
                                            crc_mode=crc_mode,
                                            description=description,
                                        )
                                    )
                            tested_count += len(crc_variants)
                            processed_tests += len(crc_variants)
                            maybe_report_progress(mode)
        mode_reports.append(ModeReport(mode=mode, tested_count=tested_count))
        maybe_report_progress(mode, force=True)

    tested_modes = [report for report in mode_reports if report.skipped_reason is None]
    total_tested = sum(report.tested_count for report in tested_modes)
    maybe_report_progress(tested_modes[-1].mode if tested_modes else "none", force=True)
    print_kv(
        "Tried",
        f"{total_tested} custom parameter/checksum combinations across {len(tested_modes)} input byte-order modes.",
    )
    for report in tested_modes:
        print_kv(
            "Scanned",
            f"[input:{report.mode}] tested {report.tested_count} custom parameter/checksum combinations",
        )

    if not matches:
        print_error("Custom brute scan found no custom CRC match.")
        return 1

    print_kv("Matches", str(len(matches)), status="success")
    for match in matches[: args.limit]:
        print_kv(
            "Match",
            f"[input:{match.input_mode}][crc:{match.crc_mode}] {match.description}",
            status="success",
        )
    if len(matches) > args.limit:
        print_kv("Output", f"Limited to first {args.limit} matches.")
    return 0


def handle_transform(args: argparse.Namespace) -> int:
    data = load_input_bytes(args)
    transformed = transform_byte_order(data, args.byte_order)

    maybe_print_banner(args)
    print_section("Byte Transform")
    print_kv("Original data", detect_original_data_kind(args))
    print_kv("Input length", f"{len(data)} bytes")
    print_kv("Byte order", f"{args.byte_order} ({describe_byte_order(args.byte_order)})")
    print_kv("Result", format_bytes(transformed, args.output_format), status="success")
    return 0


def handle_self_test(args: argparse.Namespace) -> int:
    failures = validate_catalog()
    if failures:
        for failure in failures:
            print_error(failure)
        return 1

    maybe_print_banner(args)
    print_section("Self Test")
    print_kv("Status", "CRC catalog validation passed.", status="success")
    return 0


def handle_banner(_: argparse.Namespace) -> int:
    print(render_banner())
    print(render_credit_lines())
    return 0


def add_input_mode_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--as-text",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--as-file",
        action="store_true",
        help="Treat INPUT as a file path and read raw bytes from it.",
    )


def add_input_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "original_data",
        metavar="original-data",
        help=(
            "Original data (the raw text or raw bytes whose CRC you want to test). "
            "Values starting with 0x are treated as hex bytes, values starting with 0b are treated as binary bytes, "
            "and all others are treated as text unless --as-file is used."
        ),
    )
    add_input_mode_flags(parser)


def add_byte_order_arguments(parser: argparse.ArgumentParser, allow_scan: bool = False) -> None:
    parser.add_argument(
        "--byte-order",
        default="native",
        choices=BYTE_ORDER_MODES,
        help=(
            "Transform bytes before processing. "
            "native=as-is, reverse=reverse stream, "
            "swap16/swap32/swap64=swap bytes inside each word."
        ),
    )
    if allow_scan:
        parser.add_argument(
            "--scan-byte-order",
            action="store_true",
            help="Try the selected byte order first, then all other byte-order modes.",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crcforge",
        formatter_class=HelpFormatter,
        description=(
            f"{render_banner()}\n"
            f"{render_credit_lines()}\n\n"
            f"{TOOL_NAME} calculates CRCs, matches known CRC algorithms, and handles endian transforms.\n\n"
            "Input auto-detection:\n"
            "- values starting with 0x are parsed as the original data bytes in hex\n"
            "- values starting with 0b are parsed as the original data bytes in binary\n"
            "- all other values are parsed as the original plain-text data\n"
            "- checksum arguments use decimal by default; use 0x for hexadecimal or 0b for binary\n"
            "- the CRC value may also match after its own byte order is swapped\n"
            "- use --as-file for file paths\n"
            "Running just 'python crcforge.py' prints this help screen."
        ),
        epilog=(
            "Examples:\n"
            "  python crcforge.py list --width 16\n"
            "  python crcforge.py calc crc-16-modbus 0x313233343536373839\n"
            "  python crcforge.py calc crc-16-modbus 0b0011000100110010\n"
            "  python crcforge.py calc 123456789 --algorithm crc-16-modbus\n"
            "  python crcforge.py calc 123456789 --width 16 --poly 0x1021 --init 0xFFFF --refin false --refout false\n"
            "  python crcforge.py calc crc-16-modbus 123456789\n"
            "  python crcforge.py find 0x4B37 0x313233343536373839 --width 16\n"
            "  python crcforge.py find 0b0100101100110111 123456789 --width 16\n"
            "  python crcforge.py brute 0x4B37 asadfdsaaa\n"
            "  python crcforge.py brute 0x4B37 123456789 --full-custom --max-combinations 100000\n"
            "  python crcforge.py brute 0x29B1 123456789 --width 16 --poly 0x1021 --init 0xFFFF --refin false --refout false\n"
            "  python crcforge.py transform 0xA9BB7BFD --byte-order swap32\n"
            "  python crcforge.py banner"
        ),
    )
    parser.add_argument(
        "--show-banner",
        action="store_true",
        help="Print the banner and credits before command output.",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    list_parser = subparsers.add_parser(
        "list",
        help="List built-in CRC algorithms.",
        description="List the built-in CRC catalog with all parameters.",
    )
    list_parser.add_argument("--width", type=int, help="Filter by CRC width in bits.")
    list_parser.add_argument(
        "--aliases",
        action="store_true",
        help="Also print alias names for each built-in algorithm.",
    )
    list_parser.set_defaults(handler=handle_list)

    calc_parser = subparsers.add_parser(
        "calc",
        help="Calculate a CRC using a built-in algorithm or custom parameters.",
        description=(
            "Calculate the CRC of the given original data. "
            "Use either a built-in algorithm name or provide custom CRC parameters."
        ),
    )
    calc_parser.add_argument(
        "calc_items",
        nargs="+",
        metavar="value",
        help=(
            "Legacy built-in form: <algorithm> <original-data>. "
            "Alternative form: <original-data> with --algorithm <name> or custom parameters."
        ),
    )
    calc_parser.add_argument(
        "--algorithm",
        help="Built-in algorithm name or alias.",
    )
    calc_parser.add_argument(
        "--width",
        type=int,
        help="Custom mode: CRC width in bits.",
    )
    calc_parser.add_argument(
        "--poly",
        help="Custom mode: single polynomial value.",
    )
    calc_parser.add_argument(
        "--init",
        help="Custom mode: single init value. Default: 0x0.",
    )
    calc_parser.add_argument(
        "--xor-out",
        dest="xor_out",
        help="Custom mode: single xor-out value. Default: 0x0.",
    )
    calc_parser.add_argument(
        "--refin",
        help="Custom mode: true or false. Default: false.",
    )
    calc_parser.add_argument(
        "--refout",
        help="Custom mode: true or false. Default: false.",
    )
    add_input_mode_flags(calc_parser)
    add_byte_order_arguments(calc_parser)
    calc_parser.add_argument(
        "--output-format",
        choices=("hex", "dec", "both"),
        default="both",
        help="Output as hexadecimal, decimal, or both.",
    )
    calc_parser.set_defaults(handler=handle_calc)

    find_parser = subparsers.add_parser(
        "find",
        help="Find matching built-in CRC algorithms.",
        description=(
            "Find matching known CRC algorithms. "
            "This checks one byte-order mode by default, or all modes with --scan-byte-order."
        ),
    )
    find_parser.add_argument(
        "checksum",
        metavar="checksum",
        help="Target checksum value to match. Use decimal digits, 0x-prefixed hex, or 0b-prefixed binary.",
    )
    add_input_source_arguments(find_parser)
    find_parser.add_argument(
        "--width",
        type=int,
        help="Limit the search to a specific CRC width in bits.",
    )
    find_parser.add_argument(
        "--algorithm",
        action="append",
        help="Optional algorithm filter. Repeat to narrow the search.",
    )
    add_byte_order_arguments(find_parser, allow_scan=True)
    find_parser.set_defaults(handler=handle_find)

    brute_parser = subparsers.add_parser(
        "brute",
        help="Scan built-in algorithms or custom CRC parameters across byte-order modes.",
        description=(
            "Brute mode scans the full built-in catalog against all supported byte-order "
            "transforms, or switches into custom parameter brute mode when --poly or --full-custom is provided."
        ),
    )
    brute_parser.add_argument(
        "checksum",
        metavar="checksum",
        help="Target checksum value to match. Use decimal digits, 0x-prefixed hex, or 0b-prefixed binary.",
    )
    add_input_source_arguments(brute_parser)
    brute_parser.add_argument(
        "--width",
        type=int,
        help="Optional width filter to reduce the brute scan.",
    )
    brute_parser.add_argument(
        "--algorithm",
        action="append",
        help="Optional algorithm filter. Repeat to limit the scan.",
    )
    brute_parser.add_argument(
        "--poly",
        help=(
            "Custom brute mode: poly spec. Supports VALUE, comma lists, or inclusive ranges "
            "like 0x1021,0x8005 or 0x1000:0x10FF[:STEP]."
        ),
    )
    brute_parser.add_argument(
        "--full-custom",
        action="store_true",
        help=(
            "Custom brute mode: expand omitted poly/init/xor-out specs to the full value range. "
            "If --width is omitted, it is inferred from the checksum."
        ),
    )
    brute_parser.add_argument(
        "--init",
        help="Custom brute mode: init spec. Default: 0x0.",
    )
    brute_parser.add_argument(
        "--xor-out",
        dest="xor_out",
        help="Custom brute mode: xor-out spec. Default: 0x0.",
    )
    brute_parser.add_argument(
        "--refin",
        help="Custom brute mode: RefIn mode: auto, true, or false. Default: auto.",
    )
    brute_parser.add_argument(
        "--refout",
        help="Custom brute mode: RefOut mode: auto, true, or false. Default: auto.",
    )
    brute_parser.add_argument(
        "--max-combinations",
        type=int,
        default=2000000,
        help="Custom brute mode: safety limit for total combinations. Default: 2000000.",
    )
    brute_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Custom brute mode: maximum number of matches to print. Default: 100.",
    )
    brute_parser.add_argument(
        "--progress-interval",
        type=float,
        default=2.0,
        help=(
            "Custom brute mode: live progress update interval in seconds. "
            "Use 0 to disable progress output. Default: 2.0."
        ),
    )
    brute_parser.set_defaults(handler=handle_brute)

    transform_parser = subparsers.add_parser(
        "transform",
        help="Apply endian/byte-order transforms without CRC.",
        description=(
            "Transform input bytes and print the result. "
            "Inputs starting with 0x are hex, inputs starting with 0b are binary, "
            "and other values are text unless --as-file is used."
        ),
    )
    add_input_source_arguments(transform_parser)
    add_byte_order_arguments(transform_parser)
    transform_parser.add_argument(
        "--output-format",
        choices=("hex", "compact-hex", "text"),
        default="hex",
        help="Render transformed bytes as spaced hex, compact hex, or UTF-8 text.",
    )
    transform_parser.set_defaults(handler=handle_transform)

    self_test_parser = subparsers.add_parser(
        "self-test",
        help="Validate the built-in CRC catalog.",
        description="Run the catalog self-test using the standard payload 123456789.",
    )
    self_test_parser.set_defaults(handler=handle_self_test)

    banner_parser = subparsers.add_parser(
        "banner",
        help="Print the tool banner and credits.",
        description="Print the banner, author credit, and X/Twitter handle.",
    )
    banner_parser.set_defaults(handler=handle_banner)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except FileNotFoundError as error:
        print_error(f"File not found: {error}")
        return 1
    except KeyError as error:
        print_error(str(error))
        return 1
    except ValueError as error:
        print_error(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
