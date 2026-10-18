from __future__ import annotations

import os
import re
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _support import run_cli
from crcforge import __version__
from crcforge.banner import render_banner, render_credit_lines
from crcforge.cli import build_parser
from crcforge.output import supports_unicode
from crcforge.solver import ScanResult

MODBUS_FRAMES = ["010300000001840A", "010300000002C40B", "01030000000AC5CD", "0106000100FF984A"]


class GeneralTest(unittest.TestCase):
    def test_no_arguments_prints_help(self) -> None:
        result = run_cli()
        self.assertEqual(result.code, 0)
        for heading in ("Usage", "Identify", "Calculate and check", "Modify and generate", "Catalog", "Examples"):
            self.assertIn(heading, result.stdout)
        self.assertNotIn("Exit codes", result.stdout)
        self.assertEqual(run_cli("-h").stdout, result.stdout)
        self.assertEqual(run_cli("help").stdout, result.stdout)

    def test_friendly_errors_for_wrong_commands_and_options(self) -> None:
        cases = {
            ("slove", "x"): ("Unknown command 'slove'.", "Did you mean 'solve'?"),
            ("find", "0x4B37"): ("crcforge find needs <data>.", "example: crcforge find 0x4B37 123456789"),
            ("calc", "crc-32", "x", "--widht", "3"): ("Unknown option --widht for 'crcforge calc'.", "--width"),
            ("find", "0x1", "x", "--byte-order", "swap128"): (
                "'swap128' is not a valid value for --byte-order.",
                "Choose one of: native, reverse, swap16, swap32, swap64.",
            ),
            ("list", "--width", "abc"): ("--width needs a whole number, not 'abc'.", "crcforge list -h"),
            ("calc", "x", "--algorithm"): ("--algorithm needs a value.", "--algorithm NAME"),
            ("find", "0x1", "x", "--s"): ("--s is ambiguous.", "--scan-byte-order"),
            ("help", "nosuch"): ("Unknown command 'nosuch'.", "crcforge -h"),
        }
        for args, (error, hint) in cases.items():
            with self.subTest(args):
                result = run_cli(*args)
                self.assertEqual(result.code, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(f"error: {error}", result.stderr)
                self.assertIn(hint, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("choose from", result.stderr)  # no raw argparse output

    def test_missing_arguments_show_usage_and_example(self) -> None:
        lines = run_cli("transform").stderr.splitlines()
        self.assertEqual(lines[0], "error: crcforge transform needs <data>.")
        self.assertTrue(lines[1].startswith("usage:   crcforge transform <data>"))
        self.assertTrue(lines[2].startswith("example: crcforge transform "))
        self.assertTrue(lines[3].startswith("hint:    Run 'crcforge transform -h'"))

    def test_errors_in_json_mode(self) -> None:
        result = run_cli("--json", "slove")
        self.assertEqual(result.code, 2)
        self.assertEqual(result.json()["error"], "Unknown command 'slove'.")
        missing = run_cli("find", "0x1", "--json").json()
        self.assertEqual(missing["usage"], "crcforge find <checksum> <data> [options]")
        self.assertEqual(missing["example"], "crcforge find 0x4B37 123456789")

    def test_help_command_and_command_help_layout(self) -> None:
        result = run_cli("help", "solve")
        self.assertEqual(result.code, 0)
        self.assertEqual(result.stdout, run_cli("solve", "-h").stdout)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith("crcforge solve"))
        for heading in ("Usage", "Arguments", "Options", "Frame layout", "Output options", "Examples"):
            self.assertIn(heading, lines)
        self.assertLess(lines.index("Frame layout"), lines.index("Output options"))  # shared options go last
        self.assertIn("  --crc-at end|start|auto", result.stdout)
        self.assertIn("Docs: https://kayranfatih.github.io/crcforge/commands/#solve", result.stdout)

    def test_version(self) -> None:
        result = run_cli("--version")
        self.assertEqual(result.code, 0)
        self.assertIn("1.1.0", result.stdout)

    def test_every_command_has_help_with_examples(self) -> None:
        for command in ("calc", "find", "brute", "solve", "verify", "forge", "records", "codegen", "transform", "list"):
            with self.subTest(command):
                result = run_cli(command, "--help")
                self.assertEqual(result.code, 0)
                self.assertIn("\nExamples\n", result.stdout)

    def test_every_help_example_runs(self) -> None:
        """Examples shown in --help must keep working (files they mention are skipped)."""
        parser = build_parser()
        examples = list(parser.examples)
        for sub in parser.command_parsers.values():
            examples.extend(sub.examples)
        commands = [example for example in examples if not re.search(r"@|\.(bin|hex|s19|txt|c)\b", example)]
        self.assertGreater(len(commands), 25)
        original_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)  # examples that write files must not touch the repository
            try:
                for command in commands:
                    args = shlex.split(command)[1:]
                    if args[0] == "brute" and ("--poly" in args or "--full-custom" in args):
                        args.extend(["--jobs", "1"])
                    with self.subTest(command):
                        result = run_cli(*args)
                        self.assertIn(result.code, (0, 1), result)
                        self.assertNotIn("Traceback", result.stderr)
            finally:
                os.chdir(original_directory)

    def test_output_flags_work_before_and_after_the_command(self) -> None:
        after = run_cli("calc", "crc-32", "123456789", "--json").json()
        before = run_cli("--json", "calc", "crc-32", "123456789").json()
        self.assertEqual(after, before)
        self.assertEqual(after["hex"], "0xCBF43926")

    def test_usage_errors_exit_with_2(self) -> None:
        self.assertEqual(run_cli("calc").code, 2)
        self.assertEqual(run_cli("nonsense").code, 2)
        self.assertEqual(run_cli("transform", "0x00", "--byte-order", "swap128").code, 2)

    def test_keyboard_interrupt_exits_130(self) -> None:
        with mock.patch("crcforge.cli.handle_calc", side_effect=KeyboardInterrupt):
            result = run_cli("calc", "crc-32", "x")
        self.assertEqual(result.code, 130)
        self.assertIn("Interrupted", result.stderr)

    def test_banner(self) -> None:
        result = run_cli("banner")
        self.assertEqual(result.code, 0)
        self.assertIn("Fatih Kayran", result.stdout)
        self.assertIn("Fatih Kayran", run_cli("--show-banner", "list", "--width", "3").stdout)

    def test_banner_styles(self) -> None:
        fancy = render_banner(color=False, unicode=True)
        self.assertIn("██████╗", fancy)
        self.assertIn(f"v{__version__}", fancy)
        logo = fancy.splitlines()[:6]
        self.assertEqual(len(logo), 6)
        self.assertLessEqual(max(len(line) for line in logo), 72)  # fits an 80-column terminal
        plain = render_banner(color=False, unicode=False)
        plain.encode("ascii")  # the fallback is pure ASCII
        self.assertIn("____", plain)
        self.assertIn("\033[", render_banner(color=True, unicode=True))
        self.assertNotIn("\033[", render_banner(color=False, unicode=True))
        self.assertEqual(
            render_credit_lines(color=False, unicode=False).encode("ascii").decode(),
            "by Fatih Kayran\nX/Twitter: @kayranfatih",
        )

    def test_unicode_detection(self) -> None:
        class Stream:
            def __init__(self, encoding: str | None) -> None:
                self.encoding = encoding

        self.assertTrue(supports_unicode(Stream("utf-8")))
        self.assertFalse(supports_unicode(Stream("cp1252")))
        self.assertFalse(supports_unicode(Stream(None)))
        self.assertFalse(supports_unicode(Stream("no-such-codec")))


class EdgeCaseTest(unittest.TestCase):
    """Regressions found by manually running every command with odd and wrong input."""

    def assert_error(self, args: tuple[str, ...], error: str, detail: str = "") -> None:
        result = run_cli(*args)
        self.assertEqual(result.code, 2, result.stderr)
        self.assertTrue(result.stderr.startswith("error: "), result.stderr)
        self.assertIn(error, result.stderr)
        self.assertIn(detail, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_positional_errors_show_usage_and_example(self) -> None:
        cases = {
            ("verify",): ("crcforge verify needs <algorithm> <data> <checksum>.", "example: crcforge verify "),
            ("verify", "crc-16-modbus", "123"): ("crcforge verify needs <checksum>.", "add --frame"),
            ("forge", "crc-32"): ("crcforge forge needs <data> <target>.", "usage:   crcforge forge <algorithm>"),
            ("forge", "crc-32", "a", "b", "c"): ("Too many values", "Put quotes"),
            ("codegen", "crc-32", "extra"): ("crcforge codegen takes one algorithm name", "crcforge codegen -h"),
            ("-V",): ("Unknown option -V.", "Did you mean --version?"),
        }
        for args, (error, detail) in cases.items():
            with self.subTest(args):
                self.assert_error(args, error, detail)

    def test_value_errors_name_the_option_and_the_fix(self) -> None:
        cases = {
            ("calc", "1", "--width", "16", "--poly", "0x18005"): (
                "--poly 0x18005 does not fit in 16 bits.",
                "without its top bit: 0x8005",
            ),
            ("calc", "1", "--width", "16", "--poly", "0x8005", "--xor-out", "-1"): ("--xor-out cannot be negative",),
            ("brute", "1", "1", "--width", "16", "--poly", "0x8005", "--init", "5:1"): (
                "--init range '5:1' is empty",
                "Write it as 1:5.",
            ),
            ("verify", "crc-16-modbus", "123456789", "0x1FFFF"): ("does not fit in 16 bits.", "16-bit values"),
            ("forge", "crc-32", "hello", "0x1", "--at", "99"): ("--at 99 is outside the data (5 bytes).",),
            ("calc", "1", "--all", "--width", "999"): ("No catalog algorithms have a width of 999", "16, 17"),
            ("solve", "abc:", "x:1"): ("Sample 'abc:' has no checksum after the ':'.",),
            ("solve", "--frames", "PING:0x3197"): ("looks like a DATA:CHECKSUM sample.", "drop --frames"),
            ("solve", "--frames", "010"): ("Frame '010': Hex input has an odd number of digits (3).", "@file"),
        }
        for args, (error, *detail) in cases.items():
            with self.subTest(args):
                self.assert_error(args, error, *detail)

    def test_directories_are_rejected_politely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assert_error(("forge", "crc-32", "hi", "0x1", "-o", directory), "is a directory", "file name for -o")
            self.assert_error(("codegen", "crc-32", "-o", directory), "is a directory", "file name for -o")
            self.assert_error(("records", directory), "is a directory, not a file.")
            self.assert_error(("calc", "crc-32", directory, "--as-file"), "is a directory, not a file.")

    def test_compact_algorithm_names_resolve(self) -> None:
        self.assertIn("crc-16-modbus:", run_cli("info", "crc16modbus", "-q").stdout)
        self.assertEqual(run_cli("calc", "CRC16MODBUS", "123456789", "-q").stdout.strip(), "0x4B37")

    def test_brute_with_a_polynomial_tries_conventional_init_and_xorout(self) -> None:
        result = run_cli("brute", "0x4B37", "123456789", "--width", "16", "--poly", "0x8005", "--jobs", "1")
        self.assertEqual(result.code, 0)
        self.assertIn("init=2 values, xorout=2 values", result.stdout)
        self.assertIn("init and xorout try 0 and all-ones", result.stdout)
        self.assertIn("= crc-16-modbus", result.stdout)

    def test_brute_limit_keeps_the_catalog_match_first(self) -> None:
        result = run_cli(
            "brute", "0x4B37", "123456789", "--full-custom", "--refin", "true", "--refout", "true", "--limit", "2"
        )
        rows = [line for line in result.stdout.splitlines() if line.startswith("  0x")]
        self.assertTrue(rows[0].endswith("= crc-16-modbus"))
        self.assertEqual(len(rows), 3)  # the catalog match plus --limit others
        self.assertIn("Showing 3 of ", result.stdout)

    def test_solve_without_a_match_explains_why_first(self) -> None:
        lines = run_cli("solve", "0x0102:0x10", "0x0102:0x10", "--width", "8").stdout.splitlines()
        failure = lines.index("No CRC or checksum fits every sample.")
        self.assertEqual(lines[failure + 1], "note: Ignored 1 duplicate sample.")
        self.assertIn("only one sample", lines[failure + 2])
        self.assertNotIn("warning: Only one sample", "\n".join(lines))

    def test_frames_show_only_the_best_layout(self) -> None:
        result = run_cli("solve", "--frames", *MODBUS_FRAMES[:3], "--crc-at", "auto", "--skip", "auto")
        self.assertEqual(result.code, 0)
        self.assertIn("crc-16-modbus", result.stdout)
        self.assertEqual(result.stdout.count("Layout           "), 1)
        self.assertIn("other layouts also fit", result.stdout)
        self.assertIn("same in every frame", result.stdout)
        self.assertIn("16-bit checksum at the end, little-endian", result.stdout)

    def test_verify_frame_mismatch_explains_both_byte_orders(self) -> None:
        result = run_cli("verify", "crc-16-modbus", "0x010300000001840B", "--frame")
        self.assertEqual(result.code, 1)
        self.assertIn("Neither byte order of the stored checksum matches", result.stdout)

    def test_algorithm_line_is_the_same_everywhere(self) -> None:
        expected = "crc-16-modbus  poly=0x8005 init=0xFFFF refin=true refout=true xorout=0x0000 (16-bit)"
        for args in (
            ("calc", "crc-16-modbus", "123456789"),
            ("verify", "crc-16-modbus", "123456789", "0x4B37"),
            ("forge", "crc-16-modbus", "0x0103", "0x1234"),
        ):
            with self.subTest(args):
                self.assertIn(f"Algorithm        {expected}", run_cli(*args).stdout)

    def test_transform_of_nothing_says_empty(self) -> None:
        self.assertIn("Result           (empty)", run_cli("transform", "").stdout)
        self.assertIn(
            "swap16 (reverse bytes inside each 16-bit word)",
            run_cli("transform", "0x1122", "--byte-order", "swap16").stdout,
        )


class CalcTest(unittest.TestCase):
    def test_built_in_forms(self) -> None:
        legacy = run_cli("calc", "crc-16-modbus", "123456789")
        self.assertEqual(legacy.code, 0)
        self.assertIn("0x4B37 (19255)", legacy.stdout)
        self.assertIn("CRC Calculation", legacy.stdout)
        option = run_cli("calc", "123456789", "--algorithm", "modbus", "-q")
        self.assertEqual(option.stdout.strip(), "0x4B37")

    def test_output_formats(self) -> None:
        self.assertEqual(run_cli("calc", "crc-8", "123456789", "-q", "--output-format", "dec").stdout.strip(), "244")
        self.assertEqual(
            run_cli("calc", "crc-8", "123456789", "-q", "--output-format", "bin").stdout.strip(), "0b11110100"
        )

    def test_custom_crc_uses_reveng_notation(self) -> None:
        result = run_cli(
            "calc",
            "123456789",
            "--width",
            "16",
            "--poly",
            "0x1021",
            "--init",
            "0x89EC",
            "--refin",
            "true",
            "--refout",
            "true",
            "-q",
        )
        self.assertEqual(result.stdout.strip(), "0x26B1")

    def test_custom_sum(self) -> None:
        result = run_cli(
            "calc",
            "0x450000730000400040110000c0a80001c0a800c7",
            "--sum",
            "--width",
            "16",
            "--word-size",
            "16",
            "--carry",
            "end-around",
            "--complement",
            "ones",
            "-q",
        )
        self.assertEqual(result.stdout.strip(), "0xB861")
        human = run_cli("calc", "123456789", "--sum")
        self.assertIn("Checksum Calculation", human.stdout)
        self.assertIn("0xDD", human.stdout)

    def test_complement_checksum_by_name(self) -> None:
        result = run_cli("calc", "internet-checksum", "0x450000730000400040110000C0A80001C0A800C7")
        self.assertIn("Checksum Calculation", result.stdout)
        self.assertIn("0xB861", result.stdout)

    def test_all(self) -> None:
        quiet = run_cli("calc", "123456789", "--all", "--width", "16", "--family", "crc", "-q")
        self.assertIn("crc-16-modbus 0x4B37", quiet.stdout)
        self.assertNotIn("sum-16", quiet.stdout)
        data = run_cli("calc", "123456789", "--all", "--json").json()
        self.assertEqual(len(data["results"]), 134)
        human = run_cli("calc", "123456789", "--all", "--width", "8")
        self.assertIn("All Catalog Results", human.stdout)

    def test_all_is_grouped_by_width_with_types(self) -> None:
        result = run_cli("calc", "0x010300000001", "--all")
        lines = result.stdout.splitlines()
        self.assertIn("134 (113 CRCs, 21 checksums)", result.stdout)
        header = next(line for line in lines if " 16-bit " in line)
        self.assertTrue(header.startswith("-- 16-bit "))  # ASCII rule: the captured stream has no encoding
        self.assertTrue(header.endswith("31 CRCs, 8 checksums"))
        table_header, underline = lines[lines.index(header) + 1 : lines.index(header) + 3]
        self.assertEqual(table_header.split(), ["Algorithm", "Type", "Hex", "Decimal"])
        self.assertTrue(set(underline.strip()) <= {"-", " "})
        group = lines[lines.index(header) + 3 : lines.index(header) + 42]
        self.assertTrue(group[0].split()[0].startswith("crc-16-"))  # CRCs first ...
        self.assertEqual(group[-1].split()[0], "xor-16")  # ... then checksums
        modbus = next(line for line in group if line.split()[0] == "crc-16-modbus")
        self.assertEqual(modbus.split()[1:], ["CRC", "0x0A84", "2692"])
        self.assertIn("one's compl.", result.stdout)
        widths = [int(line.split("-bit")[0].split()[-1]) for line in lines if line.startswith("-- ")]
        self.assertEqual(widths, sorted(widths))
        hex_only = run_cli("calc", "0x010300000001", "--all", "--width", "16", "--output-format", "hex")
        self.assertIn("crc-16-modbus", hex_only.stdout)
        self.assertNotIn("2692", hex_only.stdout)

    def test_hex_looking_text_gets_a_note(self) -> None:
        result = run_cli("calc", "crc-16-modbus", "010300000001")
        self.assertIn("write 0x010300000001", result.stdout)
        self.assertNotIn("write 0x", run_cli("calc", "crc-16-modbus", "123456789").stdout)

    def test_stdin_and_files_and_ranges(self) -> None:
        self.assertEqual(run_cli("calc", "crc-32", "-", "-q", stdin=b"123456789").stdout.strip(), "0xCBF43926")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blob.bin"
            path.write_bytes(b"XX123456789YYY")
            result = run_cli("calc", "crc-32", str(path), "--as-file", "--offset", "2", "--exclude-tail", "3", "-q")
            self.assertEqual(result.stdout.strip(), "0xCBF43926")
            note = run_cli("calc", "crc-32", str(path))
            self.assertIn("add --as-file", note.stdout)

    def test_byte_order(self) -> None:
        self.assertEqual(
            run_cli("calc", "crc-32", "0x34333231", "--byte-order", "reverse", "-q").stdout.strip(),
            run_cli("calc", "crc-32", "1234", "-q").stdout.strip(),
        )

    def test_errors_have_hints(self) -> None:
        cases = {
            ("calc", "crc16-modbs", "123"): "Did you mean: crc-16-modbus",
            ("calc", "crc-32", "0xygen"): "--as-text",
            ("calc", "123", "--width", "16"): "--poly",
            ("calc", "123", "--poly", "0x1021"): "--width",
            ("calc", "123", "--algorithm", "crc-32", "--poly", "0x1", "--width", "8"): "not both",
            ("calc", "crc-32"): "crcforge calc needs <data>",
            ("calc", "123", "--width", "8", "--poly", "0x1FF"): "does not fit",
            ("calc", "123", "--all", "--algorithm", "crc-32"): "--all cannot",
        }
        for args, expected in cases.items():
            with self.subTest(args):
                result = run_cli(*args)
                self.assertEqual(result.code, 2)
                self.assertIn(expected, result.stderr)

    def test_errors_in_json_mode_are_json(self) -> None:
        result = run_cli("calc", "nope", "1", "--json")
        self.assertEqual(result.code, 2)
        self.assertIn("Unknown algorithm", result.json()["error"])


class FindTest(unittest.TestCase):
    def test_match(self) -> None:
        result = run_cli("find", "0x4B37", "123456789")
        self.assertEqual(result.code, 0)
        self.assertIn("crc-16-modbus", result.stdout)
        self.assertEqual(run_cli("find", "19255", "123456789", "-q").stdout.split(), ["crc-16-modbus"])

    def test_json_and_byte_swapped_checksum(self) -> None:
        data = run_cli("find", "0x374B", "123456789", "--json").json()
        self.assertIn(
            ("byte-swapped", "crc-16-modbus"), [(m["crc_mode"], m["algorithm"]["name"]) for m in data["matches"]]
        )

    def test_no_match_exits_1_with_hints(self) -> None:
        result = run_cli("find", "0x1234", "123456789", "--width", "16")
        self.assertEqual(result.code, 1)
        self.assertIn("--scan-byte-order", result.stdout)
        self.assertIn("crcforge solve", result.stdout)

    def test_scan_byte_order_reports_skipped_and_duplicate_modes(self) -> None:
        result = run_cli("find", "0x0", "0x11223344", "--scan-byte-order", "--width", "32")
        self.assertIn("same bytes as reverse", result.stdout)
        self.assertIn("multiple of 8", result.stdout)

    def test_checksum_without_prefix_hint(self) -> None:
        result = run_cli("find", "4B37", "123456789")
        self.assertEqual(result.code, 2)
        self.assertIn("0x4B37", result.stderr)

    def test_family_filter(self) -> None:
        result = run_cli("find", "0xDD", "123456789", "--family", "checksum", "-q")
        self.assertEqual(result.stdout.split(), ["sum-8"])


class BruteTest(unittest.TestCase):
    def test_catalog_mode(self) -> None:
        result = run_cli("brute", "0x4B37", "123456789", "--width", "16")
        self.assertEqual(result.code, 0)
        self.assertIn("built-in catalog", result.stdout)
        row = next(line for line in result.stdout.splitlines() if line.split()[:1] == ["crc-16-modbus"])
        self.assertEqual(row.split()[1:3], ["CRC", "native"])

    def test_little_endian_word_checksum(self) -> None:
        result = run_cli("brute", "0xB861", "0x004573000000004011400000A8C00100A8C0C700", "--width", "16")
        self.assertEqual(result.code, 0)
        rows = [line.split() for line in result.stdout.splitlines() if line.split()[:1] == ["ones-complement-16"]]
        self.assertTrue(any("swap16" in row and "as" in row and "given" in row for row in rows), rows)

    def test_custom_mode_solves_xorout(self) -> None:
        result = run_cli(
            "brute",
            "0x4B37",
            "123456789",
            "--width",
            "16",
            "--poly",
            "0x8005",
            "--init",
            "0:0xFFFF",
            "--refin",
            "true",
            "--refout",
            "true",
            "--jobs",
            "1",
        )
        self.assertEqual(result.code, 0)
        self.assertIn("init=65,536 values, xorout=2 values", result.stdout)
        self.assertIn("= crc-16-modbus", result.stdout)
        narrow = run_cli("brute", "0x4B37", "123456789", "--width", "16", "--poly", "0x8000:0x80FF", "--init", "0xFFFF")
        self.assertIn("init=1 value, xorout=2 values", narrow.stdout)
        self.assertIn("xorout tries 0 and all-ones", narrow.stdout)
        self.assertIn("= crc-16-modbus", narrow.stdout)

    def test_full_custom_explains_conventional_limits_and_coincidences(self) -> None:
        result = run_cli(
            "brute", "19255", "123456789", "--full-custom", "--refin", "true", "--refout", "true", "--jobs", "1"
        )
        self.assertEqual(result.code, 0)
        self.assertIn("inferred from the decimal value", result.stdout)
        self.assertIn("limited to 0 and all-ones", result.stdout)
        self.assertIn("expected by chance", result.stdout)
        lines = result.stdout.splitlines()
        header = next(index for index, line in enumerate(lines) if line.split()[:2] == ["Input", "Checksum"])
        first_match = lines[header + 2]  # after the header and its underline
        self.assertIn("0x8005", first_match)
        self.assertIn("= crc-16-modbus", first_match)

    def test_quiet_and_json(self) -> None:
        args = (
            "brute",
            "0x31C3",
            "123456789",
            "--width",
            "16",
            "--poly",
            "0x1021",
            "--refin",
            "false",
            "--refout",
            "false",
        )
        self.assertIn("poly=0x1021", run_cli(*args, "-q").stdout)
        data = run_cli(*args, "--json").json()
        self.assertEqual(data["matches"][0]["catalog_name"], "crc-16-xmodem")

    def test_limits_and_errors(self) -> None:
        wide = run_cli("brute", "0xCBF43926", "123456789", "--width", "32", "--full-custom")
        self.assertEqual(wide.code, 2)
        self.assertIn("crcforge solve", wide.stderr)
        capped = run_cli(
            "brute", "0x4B37", "123456789", "--width", "16", "--poly", "0:0xFFFF", "--max-combinations", "10"
        )
        self.assertEqual(capped.code, 2)
        self.assertIn("--max-combinations", capped.stderr)
        self.assertEqual(run_cli("brute", "0x4B37", "123456789", "--poly", "0x8005").code, 2)

    def test_interrupted_scan_shows_partial_results(self) -> None:
        with mock.patch("crcforge.cli.scan_custom", return_value=ScanResult(interrupted=True)):
            result = run_cli("brute", "0x4B37", "123456789", "--width", "16", "--poly", "0x8005")
        self.assertEqual(result.code, 130)
        self.assertIn("Interrupted", result.stdout)


class SolveTest(unittest.TestCase):
    def test_samples(self) -> None:
        samples = [
            "0x313233343536373839:0x4B37",
            "0x0102030405060708:0xCFB0",
            "0xA1A2A3A4A5A6A7A8:0x5ED8",
            "0xDEADBEEF:0xC19B",
            "hello:0x34F6",
        ]
        result = run_cli("solve", *samples)
        self.assertEqual(result.code, 0, result)
        self.assertIn("Catalog matches", result.stdout)
        self.assertIn("crc-16-modbus", result.stdout)
        self.assertEqual(run_cli("solve", *samples, "-q").stdout.split()[0], "crc-16-modbus")
        data = run_cli("solve", *samples, "--json").json()
        self.assertEqual(data["width"], 16)
        self.assertEqual(data["catalog"][0]["algorithm"]["name"], "crc-16-modbus")

    def test_derives_unknown_crc(self) -> None:
        # width=16 poly=0x1234 init=0x0101 refin=false refout=false xorout=0x00FF (not in any catalog)
        import crcforge

        secret = crcforge.CrcAlgorithm("secret", 16, 0x1234 | 1, 0x0101, 0x00FF, False, False)
        datas = ["0x" + bytes([i, i + 1, i + 2, i + 3, i + 4]).hex() for i in (1, 50, 99)] + [
            "0x" + bytes(range(17)).hex()
        ]
        samples = [f"{d}:0x{secret.compute(bytes.fromhex(d[2:])):04X}" for d in datas]
        result = run_cli("solve", *samples)
        self.assertEqual(result.code, 0, result)
        self.assertIn("Derived CRC", result.stdout)
        self.assertIn(secret.reveng_spec(), result.stdout)

    def test_frames(self) -> None:
        result = run_cli("solve", "--frames", *MODBUS_FRAMES)
        self.assertEqual(result.code, 0, result)
        self.assertIn("16-bit checksum at the end, little-endian", result.stdout)
        self.assertIn("crc-16-modbus", result.stdout)
        data = run_cli("solve", "--frames", *MODBUS_FRAMES, "--json").json()
        self.assertEqual(data["layouts"][0]["layout"], {"width": 16, "position": "end", "endian": "little", "skip": 0})

    def test_samples_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.txt"
            path.write_text("# Modbus RTU\n" + "\n".join(MODBUS_FRAMES) + "\n")
            result = run_cli("solve", "--frames", "--samples-file", str(path), "-q")
        self.assertEqual(result.code, 0)
        self.assertIn("crc-16-modbus", result.stdout)

    def test_errors_and_no_match(self) -> None:
        self.assertIn("No samples", run_cli("solve").stderr)
        self.assertIn("DATA:CHECKSUM", run_cli("solve", "0x0102").stderr)
        # The same data with two different checksums cannot come from any algorithm.
        no_match = run_cli("solve", "0x0102:0x10", "0x0102:0x11", "--width", "8")
        self.assertEqual(no_match.code, 1)

    def test_single_sample_warns(self) -> None:
        result = run_cli("solve", "123456789:0x4B37")
        self.assertIn("Only one sample", result.stdout)


class VerifyTest(unittest.TestCase):
    def test_valid_and_invalid(self) -> None:
        self.assertEqual(run_cli("verify", "crc-16-modbus", "123456789", "0x4B37", "-q").stdout.strip(), "valid")
        invalid = run_cli("verify", "crc-16-modbus", "123456789", "0x374B")
        self.assertEqual(invalid.code, 1)
        self.assertIn("INVALID", invalid.stdout)
        self.assertIn("bytes are swapped", invalid.stdout)

    def test_frame(self) -> None:
        result = run_cli("verify", "crc-16-modbus", "0x" + MODBUS_FRAMES[0], "--frame")
        self.assertEqual(result.code, 0)
        self.assertIn("little-endian", result.stdout)
        broken = run_cli("verify", "crc-16-modbus", "0x010300000001840B", "--frame", "--json")
        self.assertEqual(broken.code, 1)
        self.assertFalse(broken.json()["valid"])

    def test_custom_algorithm(self) -> None:
        result = run_cli("verify", "123456789", "0x29B1", "--width", "16", "--poly", "0x1021", "--init", "0xFFFF")
        self.assertEqual(result.code, 0)


class ForgeTest(unittest.TestCase):
    def test_append(self) -> None:
        result = run_cli("forge", "crc-32", "hello world", "0xDEADBEEF")
        self.assertEqual(result.code, 0)
        self.assertIn("46 C9 6E 0B", result.stdout)
        self.assertIn("0xDEADBEEF == target", result.stdout)
        self.assertEqual(
            run_cli("forge", "crc-32", "hello world", "0xDEADBEEF", "-q").stdout.strip(),
            (b"hello world".hex() + "46c96e0b").upper(),
        )

    def test_write_file_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.bin"
            data = run_cli(
                "forge", "crc-16-modbus", "0x0102", "0x0000", "--at", "start", "-o", str(path), "--json"
            ).json()
            self.assertEqual(data["offset"], 0)
            written = path.read_bytes()
        verify = run_cli("calc", "crc-16-modbus", "0x" + written.hex(), "-q")
        self.assertEqual(verify.stdout.strip(), "0x0000")

    def test_overwrite_and_negative_offset(self) -> None:
        data = run_cli(
            "forge", "crc-32", "0x00112233445566778899", "0x12345678", "--at", "-4", "--overwrite", "--json"
        ).json()
        self.assertEqual(data["mode"], "overwrite")
        self.assertEqual(data["offset"], 6)
        self.assertEqual(len(data["data"]), 20)

    def test_errors(self) -> None:
        self.assertEqual(run_cli("forge", "adler-32", "x", "0x1").code, 2)
        self.assertIn("one's complement", run_cli("forge", "internet-checksum", "0x4500", "0xFFFF").stderr)


class RecordsTest(unittest.TestCase):
    def test_valid_and_invalid_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good.hex"
            good.write_text(":0300300002337A1E\n:00000001FF\n")
            bad = Path(directory) / "bad.hex"
            bad.write_text(":0300300002337A1F\n:00000001FF\n")
            ok = run_cli("records", str(good))
            self.assertEqual(ok.code, 0)
            self.assertIn("VALID", ok.stdout)
            failed = run_cli("records", str(bad))
            self.assertEqual(failed.code, 1)
            self.assertIn("expected 0x1E, found 0x1F", failed.stdout)
            data = run_cli("records", str(bad), "--json").json()
            self.assertEqual(data["issues"][0]["expected"], "0x1E")
        self.assertEqual(run_cli("records", "-", "-q", stdin=b"S9030000FC\n").stdout.strip(), "valid")
        self.assertEqual(run_cli("records", "missing.hex").code, 2)


class CodegenTest(unittest.TestCase):
    def test_stdout_and_file(self) -> None:
        result = run_cli("codegen", "crc-16-modbus", "--lang", "python")
        self.assertIn("def crc_16_modbus(data: bytes) -> int:", result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crc.c"
            written = run_cli("codegen", "crc-32", "--lang", "c", "-o", str(path))
            self.assertIn("Wrote c code", written.stdout)
            self.assertIn("uint32_t crc_32(const void *data, size_t length)", path.read_text())

    def test_custom_and_errors(self) -> None:
        result = run_cli(
            "codegen", "--width", "16", "--poly", "0x1021", "--init", "0xFFFF", "--lang", "rust", "--name", "my_crc"
        )
        self.assertIn("pub fn my_crc(data: &[u8]) -> u16", result.stdout)
        self.assertEqual(run_cli("codegen", "sum-8").code, 2)
        self.assertEqual(run_cli("codegen", "crc-82-darc", "--lang", "c").code, 2)


class TransformTest(unittest.TestCase):
    def test_transform_text_output(self) -> None:
        result = run_cli("transform", "0b0100000101000010", "--output-format", "text")
        self.assertEqual(result.code, 0)
        self.assertIn("AB", result.stdout)

    def test_quiet_and_json(self) -> None:
        self.assertEqual(
            run_cli("transform", "0x11223344", "--byte-order", "swap16", "-q").stdout.strip(), "22 11 44 33"
        )
        self.assertEqual(run_cli("transform", "0x1122", "--byte-order", "reverse", "--json").json()["result"], "2211")
        self.assertEqual(run_cli("transform", "0x112233", "--byte-order", "swap16").code, 2)


class CatalogCommandsTest(unittest.TestCase):
    def test_list(self) -> None:
        result = run_cli("list", "--search", "modbus", "--aliases")
        self.assertIn("1 CRC, 1 checksum", result.stdout)
        self.assertIn("modbus-lrc", result.stdout)
        self.assertEqual(run_cli("list", "--width", "3", "-q").stdout.split(), ["crc-3-gsm", "crc-3-rohc"])
        self.assertEqual(run_cli("list", "--family", "checksum", "--json").json()["count"], 21)
        self.assertEqual(run_cli("list", "--width", "999").code, 1)

    def test_info(self) -> None:
        result = run_cli("info", "CRC-16/TMS37157")
        self.assertIn("Init             0x89EC", result.stdout)
        self.assertIn("attested", result.stdout)
        self.assertIn("reveng.sourceforge.io", result.stdout)
        checksum = run_cli("info", "intel-hex", "--json").json()
        self.assertEqual(checksum["family"], "checksum")
        unknown = run_cli("info", "crc-16-modbsu")
        self.assertEqual(unknown.code, 2)
        self.assertIn("crc-16-modbus", unknown.stderr)

    def test_self_test(self) -> None:
        human = run_cli("self-test")
        self.assertIn("PASSED", human.stdout)
        self.assertIn("134 (113 CRCs, 21 checksums)", human.stdout)
        self.assertEqual(run_cli("self-test", "-q").stdout.strip(), "passed")
        self.assertTrue(run_cli("self-test", "--json").json()["passed"])


if __name__ == "__main__":
    unittest.main()
