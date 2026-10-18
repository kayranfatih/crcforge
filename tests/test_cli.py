from __future__ import annotations

import os
import re
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _support import run_cli
from crcforge.cli import build_parser
from crcforge.solver import ScanResult

MODBUS_FRAMES = ["010300000001840A", "010300000002C40B", "01030000000AC5CD", "0106000100FF984A"]


class GeneralTest(unittest.TestCase):
    def test_no_arguments_prints_help(self) -> None:
        result = run_cli()
        self.assertEqual(result.code, 0)
        self.assertIn("usage:", result.stdout.lower())
        self.assertIn("Common tasks", result.stdout)

    def test_version(self) -> None:
        result = run_cli("--version")
        self.assertEqual(result.code, 0)
        self.assertIn("1.1.0", result.stdout)

    def test_every_command_has_help_with_examples(self) -> None:
        for command in ("calc", "find", "brute", "solve", "verify", "forge", "records", "codegen", "transform", "list"):
            with self.subTest(command):
                result = run_cli(command, "--help")
                self.assertEqual(result.code, 0)
                self.assertIn("Examples:", result.stdout)

    def test_every_help_example_runs(self) -> None:
        """Examples shown in --help must keep working (files they mention are skipped)."""
        parser = build_parser()
        texts = [parser.epilog or ""]
        for action in parser._subparsers._group_actions:  # type: ignore[union-attr]
            texts.extend(sub.epilog or "" for sub in action.choices.values())  # type: ignore[union-attr]
        commands = []
        for text in texts:
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped.startswith("crcforge "):
                    continue
                command = re.split(r"\s{2,}", stripped)[0]
                if re.search(r"@|\.(bin|hex|s19|txt|c)\b", command):
                    continue
                commands.append(command)
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
            ("calc", "crc-32"): "Usage",
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
        self.assertIn("built-in-catalog", result.stdout)
        self.assertIn("[input:native][crc:as-given] crc-16-modbus", result.stdout)

    def test_little_endian_word_checksum(self) -> None:
        result = run_cli("brute", "0xB861", "0x004573000000004011400000A8C00100A8C0C700", "--width", "16")
        self.assertEqual(result.code, 0)
        self.assertIn("[input:swap16][crc:as-given] ones-complement-16", result.stdout)

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
        self.assertIn("init=solved directly", result.stdout)
        self.assertIn("(= crc-16-modbus)", result.stdout)
        narrow = run_cli("brute", "0x4B37", "123456789", "--width", "16", "--poly", "0x8000:0x80FF", "--init", "0xFFFF")
        self.assertIn("xorout=solved directly", narrow.stdout)
        self.assertIn("(= crc-16-modbus)", narrow.stdout)

    def test_full_custom_explains_conventional_limits_and_coincidences(self) -> None:
        result = run_cli(
            "brute", "19255", "123456789", "--full-custom", "--refin", "true", "--refout", "true", "--jobs", "1"
        )
        self.assertEqual(result.code, 0)
        self.assertIn("inferred from the decimal value", result.stdout)
        self.assertIn("limited to 0 and all-ones", result.stdout)
        self.assertIn("coincidental", result.stdout)
        self.assertIn("poly=0x8005", result.stdout)
        first_match = next(line for line in result.stdout.splitlines() if line.startswith("Match "))
        self.assertIn("crc-16-modbus", first_match)

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
        self.assertIn("Catalog CRC", result.stdout)
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
        self.assertIn("134 algorithms", human.stdout)
        self.assertEqual(run_cli("self-test", "-q").stdout.strip(), "passed")
        self.assertTrue(run_cli("self-test", "--json").json()["passed"])


if __name__ == "__main__":
    unittest.main()
