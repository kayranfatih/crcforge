from __future__ import annotations

import importlib.util
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401
from crcforge.catalog import CHECK_INPUT, get_algorithm, get_algorithms
from crcforge.codegen import CodegenError, generate, identifier
from crcforge.crc import CrcAlgorithm, calculate_crc

NATIVE = [a for a in get_algorithms(family="crc") if isinstance(a, CrcAlgorithm) and a.width <= 64]


def c_compiler() -> list[str] | None:
    """gcc/cc when installed, otherwise the clang-based compiler bundled with the ziglang package."""
    for name in ("gcc", "cc"):
        path = shutil.which(name)
        if path:
            return [path]
    if importlib.util.find_spec("ziglang") is not None:
        return [sys.executable, "-m", "ziglang", "cc"]
    return None


class PythonCodegenTest(unittest.TestCase):
    def test_generated_python_matches_the_engine_for_every_catalog_crc(self) -> None:
        rng = random.Random(1)
        for algorithm in get_algorithms(family="crc"):
            assert isinstance(algorithm, CrcAlgorithm)
            source = generate(algorithm, "python")
            namespace: dict[str, object] = {}
            exec(compile(source, f"<{algorithm.name}>", "exec"), namespace)
            function = namespace[identifier(algorithm.name)]
            with self.subTest(algorithm.name):
                self.assertEqual(function(CHECK_INPUT), algorithm.check)  # type: ignore[operator]
                data = rng.randbytes(rng.randrange(0, 100))
                self.assertEqual(function(data), calculate_crc(data, algorithm))  # type: ignore[operator]

    def test_custom_name_and_header(self) -> None:
        source = generate(get_algorithm("crc-16-modbus"), "python", "modbus_crc")  # type: ignore[arg-type]
        self.assertIn("def modbus_crc(data: bytes) -> int:", source)
        self.assertIn('check: modbus_crc("123456789") == 0x4B37', source)


class NativeCodegenTest(unittest.TestCase):
    def test_c_and_rust_sources_have_the_expected_shape(self) -> None:
        for algorithm in NATIVE:
            with self.subTest(algorithm.name):
                c_source = generate(algorithm, "c")
                self.assertIn("#include <stdint.h>", c_source)
                self.assertIn(f"{identifier(algorithm.name)}(const void *data, size_t length)", c_source)
                rust = generate(algorithm, "rust")
                self.assertIn(f"pub fn {identifier(algorithm.name)}(data: &[u8])", rust)

    def test_wide_crcs_are_python_only(self) -> None:
        wide = get_algorithm("crc-82-darc")
        assert isinstance(wide, CrcAlgorithm)
        for language in ("c", "rust"):
            with self.assertRaises(CodegenError):
                generate(wide, language)

    def test_unknown_language_and_non_crc(self) -> None:
        with self.assertRaises(CodegenError):
            generate(get_algorithm("crc-32"), "cobol")  # type: ignore[arg-type]
        with self.assertRaises(CodegenError):
            generate(get_algorithm("sum-8"), "c")  # type: ignore[arg-type]

    @unittest.skipUnless(c_compiler(), "no C compiler")
    def test_generated_c_compiles_and_matches_check_values(self) -> None:
        compiler = c_compiler()
        assert compiler
        with tempfile.TemporaryDirectory() as directory:
            parts = ["#include <stdio.h>"]
            calls = []
            for algorithm in NATIVE:
                parts.append(generate(algorithm, "c"))
                calls.append(
                    f'    printf("%llx\\n", (unsigned long long){identifier(algorithm.name)}("123456789", 9));'
                )
            source = "\n".join(parts).replace("#include <stddef.h>\n#include <stdint.h>\n", "")
            source = "#include <stddef.h>\n#include <stdint.h>\n" + source
            source += "\nint main(void)\n{\n" + "\n".join(calls) + "\n    return 0;\n}\n"
            path = Path(directory) / "crcs.c"
            path.write_text(source)
            binary = Path(directory) / "crcs"
            flags = ["-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", "-O1"]
            subprocess.run([*compiler, *flags, str(path), "-o", str(binary)], check=True)
            output = subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout.split()
        self.assertEqual([int(value, 16) for value in output], [a.check for a in NATIVE])

    @unittest.skipUnless(shutil.which("rustc"), "no Rust compiler")
    def test_generated_rust_compiles_and_matches_check_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parts = []
            calls = []
            for algorithm in NATIVE:
                parts.append(generate(algorithm, "rust"))
                calls.append(f'    println!("{{:x}}", {identifier(algorithm.name)}(b"123456789"));')
            source = "#![allow(dead_code)]\n" + "\n".join(parts) + "\nfn main() {\n" + "\n".join(calls) + "\n}\n"
            path = Path(directory) / "crcs.rs"
            path.write_text(source)
            binary = Path(directory) / "crcs"
            subprocess.run(["rustc", "-O", "--edition", "2021", str(path), "-o", str(binary)], check=True)
            output = subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout.split()
        self.assertEqual([int(value, 16) for value in output], [a.check for a in NATIVE])


class IdentifierTest(unittest.TestCase):
    def test_identifier(self) -> None:
        self.assertEqual(identifier("crc-16/modbus"), "crc_16_modbus")
        self.assertEqual(identifier("32q"), "crc_32q")
        self.assertEqual(identifier("!!!"), "crc")


if __name__ == "__main__":
    unittest.main()
