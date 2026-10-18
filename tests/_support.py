"""Shared test helpers. Importing this module makes ``src`` importable without installing."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Always test the code in src/, ahead of an installed copy or the checkout root.
while str(ROOT / "src") in sys.path:
    sys.path.remove(str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src"))

from crcforge.cli import main  # noqa: E402


class CliResult:
    def __init__(self, code: int, stdout: str, stderr: str) -> None:
        self.code = code
        self.stdout = stdout
        self.stderr = stderr

    def json(self) -> Any:
        return json.loads(self.stdout)

    def __repr__(self) -> str:
        return f"CliResult(code={self.code}, stdout={self.stdout!r}, stderr={self.stderr!r})"


def run_cli(*args: str, stdin: bytes | None = None) -> CliResult:
    """Run the CLI in-process and capture its output (colors off)."""
    out, err = io.StringIO(), io.StringIO()
    original_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(["--no-color", *args])
            except SystemExit as exit_:  # argparse usage errors and --help/--version
                code = int(exit_.code or 0)
    finally:
        sys.stdin = original_stdin
    return CliResult(code, out.getvalue(), err.getvalue())
