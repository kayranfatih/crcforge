from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap() -> None:
    src_path = Path(__file__).resolve().parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def main() -> int:
    _bootstrap()
    from crcforge.cli import main as package_main

    return package_main()


if __name__ == "__main__":
    raise SystemExit(main())
