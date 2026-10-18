"""Regenerate src/crcforge/catalog_crc.py from the RevEng CRC catalogue.

Usage:
    python scripts/sync_reveng_catalog.py            # download the live catalogue
    python scripts/sync_reveng_catalog.py --source all.htm

Names that CRCForge 1.0 used stay the primary names so existing commands keep working;
RevEng's canonical name and aliases are added as aliases.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crcforge.crc import REVENG_URL, CrcAlgorithm, calculate_crc, calculate_residue  # noqa: E402

OUTPUT = ROOT / "src" / "crcforge" / "catalog_crc.py"

LEGACY_NAMES: dict[str, tuple[str, tuple[str, ...]]] = {
    "CRC-5/EPC-C1G2": ("crc-5-epc-c1g2", ("epc-c1g2", "crc-5-epc")),
    "CRC-5/USB": ("crc-5-usb", ("usb-5",)),
    "CRC-6/CDMA2000-A": ("crc-6-cdma2000-a", ("cdma2000-a",)),
    "CRC-6/DARC": ("crc-6-darc", ("darc-6",)),
    "CRC-8/SMBUS": ("crc-8", ("crc8", "crc-8-smbus", "smbus")),
    "CRC-8/AUTOSAR": ("crc-8-autosar", ("autosar-8", "autosar")),
    "CRC-8/BLUETOOTH": ("crc-8-bluetooth", ("bluetooth",)),
    "CRC-8/HITAG": ("crc-8-hitag", ("hitag",)),
    "CRC-8/MAXIM-DOW": ("crc-8-maxim-dow", ("crc-8-maxim", "dallas-1-wire")),
    "CRC-8/MIFARE-MAD": ("crc-8-mifare-mad", ("mifare-mad",)),
    "CRC-8/SAE-J1850": ("crc-8-sae-j1850", ("j1850", "sae-j1850")),
    "CRC-10/ATM": ("crc-10-atm", ("atm-10",)),
    "CRC-13/BBC": ("crc-13-bbc", ("bbc",)),
    "CRC-14/DARC": ("crc-14-darc", ("darc-14",)),
    "CRC-15/MPT1327": ("crc-15-mpt1327", ("mpt1327",)),
    "CRC-16/ARC": ("crc-16-arc", ("crc-16", "crc-16-ibm", "arc", "ibm")),
    "CRC-16/IBM-3740": (
        "crc-16-ccitt-false",
        ("ccitt-false", "crc-16-ibm-3740", "ibm-3740", "crc-16-autosar", "autosar-16"),
    ),
    "CRC-16/DECT-R": ("crc-16-dect-r", ("dect-r",)),
    "CRC-16/DECT-X": ("crc-16-dect-x", ("dect-x",)),
    "CRC-16/DNP": ("crc-16-dnp", ("dnp",)),
    "CRC-16/EN-13757": ("crc-16-en-13757", ("en-13757",)),
    "CRC-16/GENIBUS": ("crc-16-genibus", ("genibus",)),
    "CRC-16/KERMIT": ("crc-16-kermit", ("kermit",)),
    "CRC-16/M17": ("crc-16-m17", ("m17",)),
    "CRC-16/MAXIM-DOW": ("crc-16-maxim-dow", ("crc-16-maxim", "maxim-16")),
    "CRC-16/MODBUS": ("crc-16-modbus", ("modbus",)),
    "CRC-16/NRSC-5": ("crc-16-nrsc-5", ("nrsc-5",)),
    "CRC-16/OPENSAFETY-A": ("crc-16-opensafety-a", ("opensafety-a",)),
    "CRC-16/OPENSAFETY-B": ("crc-16-opensafety-b", ("opensafety-b",)),
    "CRC-16/PROFIBUS": ("crc-16-profibus", ("profibus",)),
    "CRC-16/SPI-FUJITSU": ("crc-16-spi-fujitsu", ("spi-fujitsu", "aug-ccitt")),
    "CRC-16/T10-DIF": ("crc-16-t10-dif", ("t10-dif",)),
    "CRC-16/TMS37157": ("crc-16-tms37157", ("tms37157",)),
    "CRC-16/UMTS": ("crc-16-umts", ("umts", "crc-16-buypass", "buypass")),
    "CRC-16/USB": ("crc-16-usb", ("usb",)),
    "CRC-16/IBM-SDLC": ("crc-16-x25", ("x-25", "x25", "crc-16-ibm-sdlc", "ibm-sdlc", "iso-hdlc-16")),
    "CRC-16/XMODEM": ("crc-16-xmodem", ("xmodem",)),
    "CRC-24/OPENPGP": ("crc-24-openpgp", ("openpgp",)),
    "CRC-24/OS-9": ("crc-24-os-9", ("os-9",)),
    "CRC-32/ISO-HDLC": ("crc-32", ("iso-hdlc", "adccp", "v-42", "ethernet")),
    "CRC-32/AUTOSAR": ("crc-32-autosar", ("autosar-32",)),
    "CRC-32/BASE91-D": ("crc-32-base91-d", ("base91-d",)),
    "CRC-32/CKSUM": ("crc-32-cksum", ("cksum", "posix")),
    "CRC-32/BZIP2": ("crc-32-bzip2", ("bzip2",)),
    "CRC-32/ISCSI": ("crc-32c", ("castagnoli", "crc-32-iscsi", "iscsi")),
    "CRC-32/JAMCRC": ("crc-32-jamcrc", ("jamcrc",)),
    "CRC-32/MPEG-2": ("crc-32-mpeg-2", ("mpeg-2",)),
    "CRC-32/AIXM": ("crc-32q", ("32q", "crc-32-aixm", "aixm")),
    "CRC-32/XFER": ("crc-32-xfer", ("xfer",)),
    "CRC-64/ECMA-182": ("crc-64-ecma-182", ("ecma-182",)),
    "CRC-64/WE": ("crc-64-we", ("we",)),
    "CRC-64/GO-ISO": ("crc-64-go-iso", ("go-iso",)),
    "CRC-64/NVME": ("crc-64-nvme", ("nvme",)),
    "CRC-64/XZ": ("crc-64-xz", ("xz",)),
    "CRC-82/DARC": ("crc-82-darc", ("darc-82",)),
}

ENTRY_RE = re.compile(r'<P CLASS="([a-z-]+)"><CODE>(width=[^<]+)</CODE>')
ALIAS_RE = re.compile(r'<LI>Alias: <A NAME="[^"]*"><STRONG>([^<]+)</STRONG>')
PARAM_RE = re.compile(r'(\w+)=("[^"]*"|\S+)')


def normalize(name: str) -> str:
    return name.strip().lower().replace("/", "-").replace("_", "-").replace(" ", "")


def parse(text: str) -> list[dict[str, object]]:
    matches = list(ENTRY_RE.finditer(text))
    entries = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        params = dict(PARAM_RE.findall(html.unescape(match.group(2))))
        entries.append(
            {
                "status": match.group(1),
                "name": params["name"].strip('"'),
                "width": int(params["width"]),
                "poly": int(params["poly"], 16),
                "init": int(params["init"], 16),
                "refin": params["refin"] == "true",
                "refout": params["refout"] == "true",
                "xorout": int(params["xorout"], 16),
                "check": int(params["check"], 16),
                "residue": int(params["residue"], 16),
                "aliases": [html.unescape(alias) for alias in ALIAS_RE.findall(text, match.end(), end)],
            }
        )
    return entries


def build(entries: list[dict[str, object]]) -> list[CrcAlgorithm]:
    algorithms = []
    seen: dict[str, str] = {}
    for entry in entries:
        reveng_name = str(entry["name"])
        primary, legacy_aliases = LEGACY_NAMES.get(reveng_name, (normalize(reveng_name), ()))
        aliases: list[str] = []
        for candidate in (*legacy_aliases, normalize(reveng_name), *map(normalize, entry["aliases"])):  # type: ignore[arg-type]
            if candidate != primary and candidate not in aliases:
                aliases.append(candidate)
        for name in (primary, *aliases):
            if name in seen:
                raise SystemExit(f"Name collision: {name} ({seen[name]} and {reveng_name})")
            seen[name] = reveng_name
        width = int(entry["width"])  # type: ignore[call-overload]
        algorithm = CrcAlgorithm(
            primary,
            width,
            int(entry["poly"]),  # type: ignore[call-overload]
            int(entry["init"]),  # type: ignore[call-overload]
            int(entry["xorout"]),  # type: ignore[call-overload]
            bool(entry["refin"]),
            bool(entry["refout"]),
            tuple(aliases),
            check=int(entry["check"]),  # type: ignore[call-overload]
            residue=int(entry["residue"]),  # type: ignore[call-overload]
            status=str(entry["status"]),
            reveng_name=reveng_name,
        )
        calculated = calculate_crc(b"123456789", algorithm)
        if calculated != algorithm.check:
            raise SystemExit(f"{reveng_name}: check mismatch {calculated:#x} != {algorithm.check:#x}")
        if calculate_residue(algorithm) != algorithm.residue:
            raise SystemExit(f"{reveng_name}: residue mismatch")
        algorithms.append(algorithm)
    missing = set(LEGACY_NAMES) - {str(entry["name"]) for entry in entries}
    if missing:
        raise SystemExit(f"Legacy entries missing from RevEng: {sorted(missing)}")
    return algorithms


def render(algorithms: list[CrcAlgorithm]) -> str:
    lines = [
        '"""CRC catalog generated from the RevEng CRC catalogue.',
        "",
        f"Source: {REVENG_URL}",
        "Regenerate with: python scripts/sync_reveng_catalog.py",
        "Do not edit by hand.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from .crc import CrcAlgorithm",
        "",
        "CRC_CATALOG: tuple[CrcAlgorithm, ...] = (",
    ]
    for algorithm in algorithms:
        digits = max(1, (algorithm.width + 3) // 4)

        def hexv(value: int, digits: int = digits) -> str:
            return f"0x{value:0{digits}X}"

        quoted = ", ".join(f'"{alias}"' for alias in algorithm.aliases)
        aliases = f"({quoted},)" if len(algorithm.aliases) == 1 else f"({quoted})"
        lines.extend(
            [
                "    CrcAlgorithm(",
                f'        "{algorithm.name}",',
                f"        width={algorithm.width},",
                f"        poly={hexv(algorithm.poly)},",
                f"        init={hexv(algorithm.init)},",
                f"        xor_out={hexv(algorithm.xor_out)},",
                f"        refin={algorithm.refin},",
                f"        refout={algorithm.refout},",
                f"        aliases={aliases},",
                f"        check={hexv(algorithm.check or 0)},",
                f"        residue={hexv(algorithm.residue or 0)},",
                f'        status="{algorithm.status}",',
                f'        reveng_name="{algorithm.reveng_name}",',
                "    ),",
            ]
        )
    lines.append(")")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, help="Read a saved copy of all.htm instead of downloading.")
    args = parser.parse_args()
    if args.source:
        text = args.source.read_text(encoding="utf-8", errors="replace")
    else:
        with urllib.request.urlopen(REVENG_URL, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
    algorithms = build(parse(text))
    OUTPUT.write_text(render(algorithms), encoding="utf-8", newline="\n")
    print(f"Wrote {len(algorithms)} CRC definitions to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
