"""Validate Intel HEX and Motorola S-record files record by record."""

from __future__ import annotations

from dataclasses import dataclass, field

INTEL_HEX = "intel-hex"
S_RECORD = "s-record"

_INTEL_TYPES = {
    0x00: "data",
    0x01: "end-of-file",
    0x02: "extended-segment-address",
    0x03: "start-segment-address",
    0x04: "extended-linear-address",
    0x05: "start-linear-address",
}
_SREC_ADDRESS_BYTES = {"0": 2, "1": 2, "2": 3, "3": 4, "5": 2, "6": 3, "7": 4, "8": 3, "9": 2}
_SREC_TYPES = {
    "0": "header",
    "1": "data-16",
    "2": "data-24",
    "3": "data-32",
    "5": "count-16",
    "6": "count-24",
    "7": "start-32",
    "8": "start-24",
    "9": "start-16",
}


@dataclass(frozen=True)
class RecordIssue:
    line: int
    message: str
    expected: int | None = None
    actual: int | None = None


@dataclass
class RecordReport:
    format: str
    records: int = 0
    data_bytes: int = 0
    types: dict[str, int] = field(default_factory=dict)
    issues: list[RecordIssue] = field(default_factory=list)
    lowest_address: int | None = None
    highest_address: int | None = None

    @property
    def ok(self) -> bool:
        return not self.issues

    def _count(self, record_type: str) -> None:
        self.types[record_type] = self.types.get(record_type, 0) + 1

    def _span(self, address: int, length: int) -> None:
        if length <= 0:
            return
        end = address + length - 1
        self.lowest_address = address if self.lowest_address is None else min(self.lowest_address, address)
        self.highest_address = end if self.highest_address is None else max(self.highest_address, end)


def intel_hex_checksum(record: bytes) -> int:
    """Two's complement of the byte sum (everything between ':' and the checksum)."""
    return -sum(record) & 0xFF


def s_record_checksum(record: bytes) -> int:
    """One's complement of the low byte of the sum of count, address and data bytes."""
    return ~sum(record) & 0xFF


def detect_format(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(":"):
            return INTEL_HEX
        if stripped[:1] in ("S", "s"):
            return S_RECORD
        break
    raise ValueError("Not an Intel HEX or Motorola S-record file (records must start with ':' or 'S').")


def _decode(line_number: int, digits: str, report: RecordReport) -> bytes | None:
    if len(digits) % 2:
        report.issues.append(RecordIssue(line_number, "odd number of hex digits"))
        return None
    try:
        return bytes.fromhex(digits)
    except ValueError:
        report.issues.append(RecordIssue(line_number, "invalid hex digits"))
        return None


def check_records(text: str) -> RecordReport:
    record_format = detect_format(text)
    report = RecordReport(record_format)
    if record_format == INTEL_HEX:
        _check_intel_hex(text, report)
    else:
        _check_s_records(text, report)
    return report


def _check_intel_hex(text: str, report: RecordReport) -> None:
    base_address = 0
    seen_eof = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(":"):
            report.issues.append(RecordIssue(line_number, "record does not start with ':'"))
            continue
        raw = _decode(line_number, stripped[1:], report)
        if raw is None:
            continue
        report.records += 1
        if len(raw) < 5:
            report.issues.append(RecordIssue(line_number, "record is too short"))
            continue
        length, record_type = raw[0], raw[3]
        if len(raw) != length + 5:
            report.issues.append(
                RecordIssue(line_number, f"length byte says {length} data bytes but the record has {len(raw) - 5}")
            )
            continue
        body, checksum = raw[:-1], raw[-1]
        expected = intel_hex_checksum(body)
        if expected != checksum:
            report.issues.append(RecordIssue(line_number, "checksum mismatch", expected, checksum))
        type_name = _INTEL_TYPES.get(record_type)
        if type_name is None:
            report.issues.append(RecordIssue(line_number, f"unknown record type 0x{record_type:02X}"))
            continue
        report._count(type_name)
        if seen_eof:
            report.issues.append(RecordIssue(line_number, "record after end-of-file record"))
        address = int.from_bytes(raw[1:3], "big")
        data = raw[4:-1]
        if record_type == 0x00:
            report.data_bytes += len(data)
            report._span(base_address + address, len(data))
        elif record_type == 0x01:
            seen_eof = True
        elif record_type == 0x02 and len(data) == 2:
            base_address = int.from_bytes(data, "big") << 4
        elif record_type == 0x04 and len(data) == 2:
            base_address = int.from_bytes(data, "big") << 16
    if report.records and not seen_eof:
        report.issues.append(RecordIssue(0, "missing end-of-file record (:00000001FF)"))


def _check_s_records(text: str, report: RecordReport) -> None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) < 4 or stripped[0] not in ("S", "s"):
            report.issues.append(RecordIssue(line_number, "record does not start with 'S'"))
            continue
        record_type = stripped[1]
        address_bytes = _SREC_ADDRESS_BYTES.get(record_type)
        if address_bytes is None:
            report.issues.append(RecordIssue(line_number, f"unknown record type S{record_type}"))
            continue
        raw = _decode(line_number, stripped[2:], report)
        if raw is None:
            continue
        report.records += 1
        count = raw[0]
        if count != len(raw) - 1:
            report.issues.append(
                RecordIssue(line_number, f"count byte says {count} bytes but the record has {len(raw) - 1}")
            )
            continue
        if count < address_bytes + 1:
            report.issues.append(RecordIssue(line_number, "record is too short for its address field"))
            continue
        body, checksum = raw[:-1], raw[-1]
        expected = s_record_checksum(body)
        if expected != checksum:
            report.issues.append(RecordIssue(line_number, "checksum mismatch", expected, checksum))
        report._count(_SREC_TYPES[record_type])
        if record_type in ("1", "2", "3"):
            address = int.from_bytes(raw[1 : 1 + address_bytes], "big")
            data = raw[1 + address_bytes : -1]
            report.data_bytes += len(data)
            report._span(address, len(data))
