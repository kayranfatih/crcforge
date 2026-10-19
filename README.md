![CRCForge logo](https://raw.githubusercontent.com/kayranfatih/crcforge/main/docs/assets/crcforge_logo.png)

[![CI](https://github.com/kayranfatih/crcforge/actions/workflows/ci.yml/badge.svg)](https://github.com/kayranfatih/crcforge/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](https://github.com/kayranfatih/crcforge/blob/main/LICENSE)

**CRCForge** is a Swiss Army knife for CRCs and checksums, built for reverse engineers, protocol analysts and firmware researchers. Give it some data and its checksum and it checks 134 known algorithms. If none of them match, it can work out the CRC parameters from a few samples. It can also patch data to get the CRC you want, check Intel HEX and S-record files and generate CRC code in C, Python or Rust.

- 134 algorithms: all 113 CRCs from the [RevEng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) plus sum, one's and two's complement, XOR, Fletcher, Adler-32, BSD and SysV checksums
- Works out poly, init, xorout and reflection from samples, 32 and 64-bit CRCs included
- Finds the checksum inside captured frames: where it is, how wide it is and its byte order
- Also tries reversed or byte-swapped data and checksums
- Patches data so it produces a chosen CRC or checksum
- `--json` and `-q` on every command, a Python API and no dependencies

## Install

```bash
pipx install crcforge
```

`pip install crcforge` works too. CRCForge needs Python 3.10 or newer. For the latest code:

```bash
pipx install git+https://github.com/kayranfatih/crcforge
```

## Quick start

Calculate a CRC:

```console
$ crcforge calc crc-16-modbus 123456789 -q
0x4B37
```

Find which algorithm made a checksum:

```console
$ crcforge find 0x4B37 123456789
CRC Match Search
Checksum         0x4B37
Data             plain-text, 9 bytes
Byte-order scan  native
Matches          1

  Algorithm      Type  Parameters
  ─────────────  ────  ────────────────────────────────────────────────────────────
  crc-16-modbus  CRC   poly=0x8005 init=0xFFFF refin=true refout=true xorout=0x0000
```

Patch data so its CRC becomes `0xDEADBEEF`:

```console
$ crcforge forge crc-32 "hello world" 0xDEADBEEF
Forge
Algorithm        crc-32  poly=0x04C11DB7 init=0xFFFFFFFF refin=true refout=true xorout=0xFFFFFFFF (32-bit)
Data             plain-text, 11 bytes
Target           0xDEADBEEF
Patch            46 C9 6E 0B (inserted at offset 11)
Forged data      68 65 6C 6C 6F 20 77 6F 72 6C 64 46 C9 6E 0B
Verified         0xDEADBEEF == target
```

## Finding an unknown CRC with `solve`

Use `solve` when you have a few messages with their checksums but don't know the algorithm. Write each sample as `DATA:CHECKSUM`:

```console
$ crcforge solve PING:0x3197 PONG:0x16A1 PUSH:0x4524 STATUS:0x7CB4
CRC Solver
Samples          4 (lengths: 4, 6 bytes)
Width            16 bits (inferred from 4 hex digits; pass --width to override)

Derived CRC parameters
  Poly    Init    RefIn  RefOut  XorOut  Note
  ──────  ──────  ─────  ──────  ──────  ───────────────────────────
  0x3D65  0x1234  false  false   0x0F0F
  0x3D65  0xF917  false  false   0xE42C  equivalent to the row above
RevEng spec      width=16 poly=0x3d65 init=0x1234 refin=false refout=false xorout=0x0f0f

note: Polynomials divisible by (x+1) have equivalent init/xorout pairs; all listed pairs are correct.
```

None of the known algorithms fit these samples, so `solve` worked out the parameters itself. It goes like this:

1. It checks every known algorithm first.
2. Samples of the same length give the polynomial (PING, PONG and PUSH here).
3. A sample of a different length then fixes init and xorout (STATUS here).
4. Simple sums and XOR checksums are tested too.

Three or four samples are usually enough, as long as two of them have the same length. With fewer samples many algorithms fit by chance and CRCForge warns you. Rows marked "equivalent" give the same CRC values, so any of them works.

If the checksum is stored inside each message, pass the whole frames with `--frames`:

```console
$ crcforge solve --frames 010300000001840A 010300000002C40B 01030000000AC5CD 0106000100FF984A
CRC Solver (frames)
Frames           4 (length 8 bytes)
Layouts tested   5

Layout           16-bit checksum at the end, little-endian

Catalog matches
  Algorithm      Type  Parameters
  ─────────────  ────  ────────────────────────────────────────────────────────────
  crc-16-modbus  CRC   poly=0x8005 init=0xFFFF refin=true refout=true xorout=0x0000
```

It tries 8, 16 and 32-bit checksums at the end of the frame in both byte orders. `--crc-at auto` also tries the start and `--skip auto` skips header bytes.

Only have one message? Use `find`. If you know part of the algorithm, `brute` can search the rest:

```bash
crcforge brute 0x4B37 123456789 --width 16 --poly 0x8005
```

## Commands

| Command | What it does |
| --- | --- |
| `calc` | Calculate a CRC or checksum, or all of them with `--all` |
| `find` | Find which known algorithm matches a checksum |
| `solve` | Work out the algorithm from several samples or frames |
| `brute` | Try every byte order, or search CRC parameters for one sample |
| `verify` | Check a checksum (`--frame` reads it from the data) |
| `forge` | Patch data to get a chosen CRC or checksum |
| `records` | Check an Intel HEX or S-record file |
| `codegen` | Generate CRC code in C, Python or Rust |
| `info` | Show the details of one algorithm |
| `list` | List the algorithms (`--width`, `--family`, `--search`) |
| `transform` | Reverse or swap the byte order of data |
| `self-test` | Check every algorithm against its known check value |

`crcforge <command> -h` shows all options with examples.

## Input

| Data | Read as |
| --- | --- |
| `0xDEADBEEF`, `0xDE AD BE EF` | hex bytes |
| `0b01001000` | binary bytes |
| `-` | bytes from stdin |
| `firmware.bin --as-file` | bytes from a file |
| anything else | UTF-8 text (`--as-text` forces text) |

Checksums are decimal unless they start with `0x` or `0b`, so `19255` and `0x4B37` are the same value. `4B37` without a prefix is rejected.

`--offset`, `--length` and `--exclude-tail` cut out part of the data, for example a header or a stored checksum.

A wrong result often comes from byte order rather than the algorithm. `--byte-order` changes the data first: `reverse` flips the whole stream and `swap16`, `swap32` and `swap64` swap the bytes inside each word. `find` and `solve` also check if the checksum itself is stored byte-swapped (`0x374B` instead of `0x4B37`).

## Python API

```python
import crcforge
```

| Function | Returns |
| --- | --- |
| `calculate(algorithm, data)` | the CRC or checksum as an `int` |
| `find(data, checksum)` | the known algorithms that produce `checksum` |
| `solve(samples, width)` | a `SolveResult` (see below) |
| `forge(data, algorithm, target)` | a `ForgeResult`, the patched bytes are in `.data` |
| `list_algorithms(width, family, search)` | the algorithms in the catalog |
| `check_records(text)` | a `RecordReport` for Intel HEX or S-record text |

`algorithm` is a name such as `"crc-32"` or an algorithm object:

```python
crcforge.calculate("crc-16-modbus", b"123456789")   # 0x4B37

[a.name for a in crcforge.find(b"123456789", 0x4B37)]   # ['crc-16-modbus']

patched = crcforge.forge(b"hello world", "crc-32", 0xDEADBEEF).data
crcforge.calculate("crc-32", patched)   # 0xDEADBEEF

my_crc = crcforge.CrcAlgorithm("my-crc", 16, poly=0x1021, init=0xFFFF, xor_out=0, refin=False, refout=False)
crcforge.calculate(my_crc, b"123456789")   # 0x29B1
```

`solve` takes a list of `Sample(data, checksum)` and the checksum width in bits:

```python
samples = [
    crcforge.Sample(b"PING", 0x3197),
    crcforge.Sample(b"PONG", 0x16A1),
    crcforge.Sample(b"PUSH", 0x4524),
    crcforge.Sample(b"STATUS", 0x7CB4),
]
result = crcforge.solve(samples, width=16)

result.found              # True if anything fits
result.catalog_matches    # known algorithms that fit
result.crc_matches        # CRC parameters worked out from the samples
result.checksum_matches   # sums and XOR checksums that fit
result.notes              # warnings, for example when there are too few samples

crc = result.crc_matches[0].algorithm   # a CrcAlgorithm
crc.poly, crc.init, crc.xor_out         # 0x3D65, 0x1234, 0x0F0F
crc.reveng_spec()                       # 'width=16 poly=0x3d65 init=0x1234 refin=false refout=false xorout=0x0f0f'
crcforge.calculate(crc, b"PING")        # 0x3197
```

Each match also has `input_mode` and `crc_mode`, which tell you if the data or the checksum had to be byte-swapped. Parameters use RevEng notation, so values from the RevEng catalogue work as they are. More in the [Python API docs](https://kayranfatih.github.io/crcforge/python-api/).

## Scripting

`--json` prints JSON and `-q` prints only the result. Both work before or after the command name. Errors go to stderr.

| Exit code | Meaning |
| --- | --- |
| 0 | match found or checksum valid |
| 1 | no match, invalid checksum or a broken record file |
| 2 | wrong usage or bad input |
| 130 | stopped with Ctrl+C (long searches print what they found so far) |

## Docs

Full docs are on the [documentation site](https://kayranfatih.github.io/crcforge/) and in [`docs/`](https://github.com/kayranfatih/crcforge/tree/main/docs). Changes per release are in [CHANGELOG.md](https://github.com/kayranfatih/crcforge/blob/main/CHANGELOG.md).

## Development

```bash
python -m pip install -e . -r requirements-dev.txt
python -m pytest
ruff check . && ruff format --check . && mypy
```

See [CONTRIBUTING.md](https://github.com/kayranfatih/crcforge/blob/main/CONTRIBUTING.md) for the catalog sync script, the docs and releases.

## Credits

Fatih Kayran · X/Twitter: [@kayranfatih](https://x.com/kayranfatih)

The CRC catalog comes from Greg Cook's [RevEng CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm).
