"""Print the CHANGELOG.md section for one version (used by the release workflow).

Usage: python scripts/release_notes.py 1.1.0
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def section(version: str, text: str) -> str:
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise SystemExit(f"Version {version} not found in CHANGELOG.md")
    return match.group(1).strip() + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.stdout.write(section(sys.argv[1], CHANGELOG.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
