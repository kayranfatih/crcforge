from __future__ import annotations

import unittest

import _support  # noqa: F401
from crcforge.records import (
    INTEL_HEX,
    S_RECORD,
    check_records,
    detect_format,
    intel_hex_checksum,
    s_record_checksum,
)

VALID_HEX = """\
:020000040800F2
:10010000214601360121470136007EFE09D2190140
:100110002146017E17C20001FF5F16002148011928
:00000001FF
"""

VALID_SREC = """\
S00F000068656C6C6F202020202000003C
S11F00007C0802A6900100049421FFF07C6C1B787C8C23783C6000003863000026
S11F001C4BFFFFE5398000007D83637880010014382100107C0803A64E800020E9
S111003848656C6C6F20776F726C642E0A0042
S5030003F9
S9030000FC
"""


class IntelHexTest(unittest.TestCase):
    def test_valid_file(self) -> None:
        report = check_records(VALID_HEX)
        self.assertEqual(report.format, INTEL_HEX)
        self.assertTrue(report.ok, report.issues)
        self.assertEqual(report.records, 4)
        self.assertEqual(report.data_bytes, 32)
        self.assertEqual(report.lowest_address, 0x08000100)
        self.assertEqual(report.highest_address, 0x0800011F)
        self.assertEqual(report.types, {"extended-linear-address": 1, "data": 2, "end-of-file": 1})

    def test_bad_checksum_is_reported_with_expected_value(self) -> None:
        broken = VALID_HEX.replace("D2190140", "D2190141")
        report = check_records(broken)
        self.assertFalse(report.ok)
        self.assertEqual(len(report.issues), 1)
        issue = report.issues[0]
        self.assertEqual((issue.line, issue.expected, issue.actual), (2, 0x40, 0x41))

    def test_structure_problems(self) -> None:
        report = check_records(":0300300002337A\n:0300300002337\n:0200300002337A1E\nZZ\n")
        messages = [issue.message for issue in report.issues]
        self.assertTrue(any("odd number" in m for m in messages))
        self.assertTrue(any("length byte" in m for m in messages))
        self.assertTrue(any("does not start" in m for m in messages))
        self.assertTrue(any("end-of-file" in m for m in messages))

    def test_unknown_type_and_records_after_eof(self) -> None:
        unknown = ":00000009F7\n:00000001FF\n"
        self.assertTrue(any("unknown record type" in i.message for i in check_records(unknown).issues))
        after = ":00000001FF\n:0300300002337A1E\n"
        self.assertTrue(any("after end-of-file" in i.message for i in check_records(after).issues))

    def test_segment_addressing(self) -> None:
        text = ":020000021000EC\n:0300300002337A1E\n:00000001FF\n"
        report = check_records(text)
        self.assertTrue(report.ok, report.issues)
        self.assertEqual(report.lowest_address, 0x10000 + 0x30)


class SRecordTest(unittest.TestCase):
    def test_valid_file(self) -> None:
        report = check_records(VALID_SREC)
        self.assertEqual(report.format, S_RECORD)
        self.assertTrue(report.ok, report.issues)
        self.assertEqual(report.records, 6)
        self.assertEqual(report.types["data-16"], 3)
        self.assertEqual(report.lowest_address, 0)

    def test_bad_checksum(self) -> None:
        report = check_records(VALID_SREC.replace("0A0042", "0A0043"))
        self.assertEqual([(i.line, i.expected, i.actual) for i in report.issues], [(4, 0x42, 0x43)])

    def test_count_mismatch_and_unknown_type(self) -> None:
        report = check_records("S1130000FF\nS4030000FC\n")
        messages = [issue.message for issue in report.issues]
        self.assertTrue(any("count byte" in m for m in messages))
        self.assertTrue(any("unknown record type S4" in m for m in messages))


class HelpersTest(unittest.TestCase):
    def test_checksum_helpers(self) -> None:
        self.assertEqual(intel_hex_checksum(bytes.fromhex("0300300002337A")), 0x1E)
        self.assertEqual(s_record_checksum(bytes.fromhex("030000")), 0xFC)

    def test_detect_format(self) -> None:
        self.assertEqual(detect_format("\n\n:00000001FF"), INTEL_HEX)
        self.assertEqual(detect_format("S9030000FC"), S_RECORD)
        with self.assertRaises(ValueError):
            detect_format("hello")
        with self.assertRaises(ValueError):
            detect_format("")


if __name__ == "__main__":
    unittest.main()
