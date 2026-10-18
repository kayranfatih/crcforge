from __future__ import annotations

import importlib.util
import unittest

import _support
from crcforge.catalog import (
    ALGORITHMS_BY_NAME,
    FULL_CATALOG,
    UnknownAlgorithmError,
    find_matching_algorithms,
    get_algorithm,
    get_algorithms,
    normalize_name,
    suggest_names,
    validate_catalog,
)
from crcforge.catalog_crc import CRC_CATALOG


def load_sync_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "sync_reveng_catalog", _support.ROOT / "scripts" / "sync_reveng_catalog.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CatalogContentTest(unittest.TestCase):
    def test_self_validation_passes(self) -> None:
        self.assertEqual(validate_catalog(), [])

    def test_full_reveng_catalogue_is_included(self) -> None:
        self.assertEqual(len(CRC_CATALOG), 113)
        self.assertEqual(len({algorithm.reveng_name for algorithm in CRC_CATALOG}), 113)
        statuses = {algorithm.status for algorithm in CRC_CATALOG}
        self.assertEqual(statuses, {"attested", "confirmed", "academic", "third-party"})

    def test_names_from_version_1_0_still_resolve_to_the_same_parameters(self) -> None:
        legacy = load_sync_script().LEGACY_NAMES
        for reveng_name, (primary, aliases) in legacy.items():
            for name in (primary, *aliases):
                with self.subTest(name):
                    algorithm = get_algorithm(name)
                    self.assertEqual(algorithm.name, primary)
                    self.assertEqual(getattr(algorithm, "reveng_name", None), reveng_name)

    def test_reveng_canonical_names_resolve(self) -> None:
        for algorithm in CRC_CATALOG:
            with self.subTest(algorithm.reveng_name):
                self.assertIs(get_algorithm(str(algorithm.reveng_name)), algorithm)

    def test_every_name_is_unique(self) -> None:
        names = [normalize_name(n) for algorithm in FULL_CATALOG for n in (algorithm.name, *algorithm.aliases)]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(ALGORITHMS_BY_NAME), len(names))


class LookupTest(unittest.TestCase):
    def test_lookup_ignores_case_and_separators(self) -> None:
        for name in ("CRC-16/MODBUS", "crc_16_modbus", " Crc-16-Modbus ", "modbus"):
            self.assertEqual(get_algorithm(name).name, "crc-16-modbus")

    def test_unknown_name_suggests_close_matches(self) -> None:
        with self.assertRaises(UnknownAlgorithmError) as context:
            get_algorithm("crc16-modbs")
        self.assertIn("crc-16-modbus", context.exception.suggestions)
        self.assertIn("Did you mean", str(context.exception))
        self.assertIsInstance(context.exception, KeyError)

    def test_suggestions_fall_back_to_substring_matches(self) -> None:
        self.assertIn("crc-16-modbus", suggest_names("modb"))

    def test_filters(self) -> None:
        self.assertTrue(all(a.width == 16 for a in get_algorithms(width=16)))
        self.assertTrue(all(a.family == "checksum" for a in get_algorithms(family="checksum")))
        self.assertEqual([a.name for a in get_algorithms(search="xmodem")], ["crc-16-xmodem"])
        self.assertEqual(get_algorithms(width=999), [])

    def test_named_selection_deduplicates_aliases(self) -> None:
        selected = get_algorithms(names=["modbus", "crc-16-modbus", "CRC-16/MODBUS"])
        self.assertEqual([a.name for a in selected], ["crc-16-modbus"])

    def test_results_are_sorted_by_width_then_name(self) -> None:
        algorithms = get_algorithms()
        self.assertEqual(algorithms, sorted(algorithms, key=lambda a: (a.width, a.name)))

    def test_find_matching_algorithms(self) -> None:
        names = [a.name for a in find_matching_algorithms(b"123456789", 0x4B37)]
        self.assertIn("crc-16-modbus", names)
        self.assertEqual(find_matching_algorithms(b"123456789", 0x4B37, family="checksum"), [])


class SyncScriptTest(unittest.TestCase):
    def test_parser_reads_reveng_markup_including_the_bluetooth_typo(self) -> None:
        module = load_sync_script()
        html = (
            '<H3><A NAME="crc.cat.crc-8-bluetooth">CRC-8/BLUETOOTH</A></H3>\n'
            '<P CLASS="attested"><CODE>width=8  poly=0xa7  init=0x00  refin=true  refout=true  xorout=0x00  '
            'check=0x26  residue=0x00  name="CRC-8/BLUETOOTH"</CODE><P>\n'
            '<LI>Alias: <A NAME="crc.cat.x"><STRONG>CRC-8/BT</STRONG></A></LI>\n'
        )
        entries = module.parse(html)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "CRC-8/BLUETOOTH")
        self.assertEqual(entries[0]["aliases"], ["CRC-8/BT"])
        self.assertEqual(entries[0]["poly"], 0xA7)


if __name__ == "__main__":
    unittest.main()
