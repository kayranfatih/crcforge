"""Run CRCForge from a source checkout without installing it: ``python crcforge.py ...``."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap() -> None:
    # src/ must come first: this file is itself named crcforge.py, and the checkout directory
    # (sys.path[0]) would otherwise shadow the real package, e.g. after `pip install -e .`.
    src_path = str(Path(__file__).resolve().parent / "src")
    while src_path in sys.path:
        sys.path.remove(src_path)
    sys.path.insert(0, src_path)
    module = sys.modules.get("crcforge")
    if module is not None and not hasattr(module, "__path__"):
        del sys.modules["crcforge"]


def main() -> int:
    _bootstrap()
    from crcforge.cli import main as package_main

    return package_main()


if __name__ == "__main__":
    raise SystemExit(main())
