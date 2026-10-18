# Python API

Everything the command line does is available from Python. CRCForge has no dependencies.

```python
import crcforge
```

## Calculating

```python
crcforge.calculate("crc-16-modbus", b"123456789")  # 19255 == 0x4B37
crcforge.calculate("CRC-16/MODBUS", b"123456789")  # RevEng names work too

algorithm = crcforge.get_algorithm("crc-32")
algorithm.compute(b"123456789")  # 0xCBF43926
algorithm.describe()  # 'crc-32: width=32, poly=0x04C11DB7, ...'
algorithm.reveng_spec()  # 'width=32 poly=0x04c11db7 init=0xffffffff ...'
```

Custom algorithms are plain dataclasses:

```python
ccitt = crcforge.CrcAlgorithm("ccitt", 16, poly=0x1021, init=0xFFFF, xor_out=0, refin=False, refout=False)
crcforge.calculate(ccitt, b"123456789")  # 0x29B1

internet = crcforge.SumChecksum("inet", 16, word_size=2, carry="end-around", complement="ones")
crcforge.calculate(internet, bytes.fromhex("450000730000400040110000c0a80001c0a800c7"))  # 0xB861
```

`init` uses RevEng notation for reflected CRCs as well, so catalogue values work unchanged.

## Searching the catalog

```python
[a.name for a in crcforge.find(b"123456789", 0x4B37)]  # ['crc-16-modbus']
crcforge.find(b"123456789", 0xDD, family="checksum")  # [SumChecksum(name='sum-8', ...)]
crcforge.list_algorithms(width=8, family="crc")  # every 8-bit CRC
```

`get_algorithm` raises `crcforge.UnknownAlgorithmError` (a `KeyError`) with close-match suggestions in `.suggestions`.

## Solving

```python
samples = [
    crcforge.Sample(b"PING", 0x3197),
    crcforge.Sample(b"PONG", 0x16A1),
    crcforge.Sample(b"PUSH", 0x4524),
    crcforge.Sample(b"STATUS", 0x7CB4),
]
result = crcforge.solve(samples, width=16)

result.catalog_matches  # catalog CRCs that fit every sample
result.crc_matches  # derived parameters (CrcMatch objects)
result.checksum_matches  # sum/XOR/other checksums that fit
result.notes  # explanations (ambiguity, skipped scans, ...)

best = result.crc_matches[0]
best.algorithm.reveng_spec()  # 'width=16 poly=0x3d65 init=0x1234 refin=false refout=false xorout=0x0f0f'
best.free_bits  # > 0 when init/xorout are not unique for these samples
```

`solve` accepts keyword options: `poly_values`, `init_values`, `xorout_values` (iterables restricting the search), `reflections` (pairs of `(refin, refout)`), `byte_order_modes`, `swap_checksums`, `limit` and `exhaustive`.

Frames with an embedded checksum are split with `FrameLayout`:

```python
layout = crcforge.FrameLayout(width=16, position="end", endian="little")
frames = [bytes.fromhex(f) for f in ("010300000001840A", "010300000002C40B", "01030000000AC5CD")]
crcforge.solve(layout.split_all(frames), 16, swap_checksums=False).catalog_matches[0].algorithm.name
# 'crc-16-modbus'
```

## Forging

```python
result = crcforge.forge(b"hello world", "crc-32", 0xDEADBEEF)
result.patch  # b'F\xc9n\x0b'
result.data  # b'hello worldF\xc9n\x0b'

crcforge.forge(image, "crc-16-modbus", 0x0000, offset=-2 % len(image), overwrite=True, patch_size=2)
```

`forge` raises `crcforge.ForgeError` when the target cannot be reached.

## Record files

```python
report = crcforge.check_records(open("firmware.hex").read())
report.ok, report.records, report.data_bytes
for issue in report.issues:
    print(issue.line, issue.message, issue.expected, issue.actual)
```

## Validation

```python
crcforge.validate_catalog()  # [] when every entry matches its check value and residue
```
