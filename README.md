![CRCForge logo](https://raw.githubusercontent.com/kayranfatih/crcforge/main/docs/assets/crcforge_logo.png)

[![CI](https://github.com/kayranfatih/crcforge/actions/workflows/ci.yml/badge.svg)](https://github.com/kayranfatih/crcforge/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](https://github.com/kayranfatih/crcforge/blob/main/LICENSE)

**CRCForge** is a Swiss Army knife for CRCs and checksums, built for reverse engineers, protocol analysts and firmware researchers. Give it data and a checksum and it tells you which algorithm produced it. When no known algorithm fits, it **derives the CRC parameters from your samples**. It also forges data to hit a chosen CRC, validates Intel HEX and S-record files, and generates C, Python or Rust implementations.

- **134 built-in algorithms**: the complete [RevEng CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) (113 CRCs, each verified against its check value and residue) plus sum, one's/two's complement, XOR, Fletcher, Adler-32, BSD and SysV checksums.
- **Parameter solver**: derives poly, init, xorout, refin and refout from a handful of samples, for any width including 32- and 64-bit CRCs, typically in well under a second.
- **Frame analysis**: finds where the checksum sits in captured frames, how wide it is and which byte order it uses.
- **Byte-order aware**: also tries reversed and word-swapped payloads and byte-swapped checksums.
- **Forge**: insert or overwrite bytes so data produces any CRC or sum/XOR checksum you choose.
- **Scriptable**: `--json` and `--quiet` on every command, meaningful exit codes, a Python API, and no dependencies.

## Installation

```bash
pipx install crcforge
```

`python -m pip install crcforge` works too. To install the latest code from GitHub:

```bash
pipx install git+https://github.com/kayranfatih/crcforge
```

CRCForge needs Python 3.10 or newer and has no third-party dependencies. In a source checkout, `python crcforge.py` runs it without installing.

## Quick start

Calculate a known CRC:

```console
$ crcforge calc crc-16-modbus 123456789 -q
0x4B37
```

Find out which algorithm produced a checksum:

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

Captured some frames but don't know where the checksum is or how it is computed? Hand over the frames:

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

The checksum isn't from any catalog? `solve` derives the parameters. These four samples come from a made-up CRC:

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

Make data produce the CRC you want:

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

## Commands

| Command | What it does |
| --- | --- |
| `calc` | Calculate a built-in algorithm, custom CRC parameters, a custom sum checksum, or every catalog entry at once (`--all`). |
| `find` | Match one data/checksum pair against the catalog, including byte-swapped checksums. |
| `solve` | Identify the algorithm behind several samples: catalog matches, derived CRC parameters, sum/XOR layouts. `--frames` locates the checksum inside frames. |
| `brute` | Scan the catalog across every byte order, or search custom CRC parameters for a single sample. |
| `verify` | Check that data and a checksum belong together; `--frame` reads the checksum from the data. |
| `forge` | Insert or overwrite bytes so the data produces a target CRC or sum/XOR checksum. |
| `records` | Validate every record of an Intel HEX or Motorola S-record file. |
| `codegen` | Generate a table-driven implementation in C, Python or Rust. |
| `info` | Show an algorithm's parameters, aliases, check value, residue and RevEng reference. |
| `list` | List the catalog, filtered by `--width`, `--family` or `--search`. |
| `transform` | Apply a byte-order transform and print the bytes. |
| `self-test` | Verify every catalog entry against its published check value and residue. |

`crcforge <command> -h` (or `crcforge help <command>`) shows every option with examples. Mistyped commands and options get a clear error with a suggestion instead of a usage dump.

## Reverse-engineering a checksum

Start with the cheapest tool and move on when it comes up empty:

1. **`find`**: one sample against the catalog. Add `--scan-byte-order` if the payload may be stored in another byte order.
2. **`solve`**: several samples. Catalog CRCs are checked first, then the CRC parameters are derived:
   - **Two or more samples of the same length** cancel init and xorout. The polynomial then follows directly from a GCD over GF(2), for any width. This is the approach RevEng uses.
   - **Samples of different lengths** pin down init and xorout through a small linear system.
   - Sum, one's/two's complement and XOR layouts are tested as well.
3. **`solve --frames`**: when the checksum is stored inside each frame. CRCForge tries 8-, 16- and 32-bit checksums at the end (or `--crc-at auto` for the start too), in both byte orders, and with `--skip auto` it also skips header bytes.
4. **`brute`**: one sample, but you know part of the answer (for example the polynomial). xorout (or init) is solved directly instead of scanned, so `--poly 0x8005 --init 0:0xFFFF` takes milliseconds. Large searches use every CPU core.

Things worth knowing:

- **One short sample proves little.** A 16-bit checksum has only 65,536 values, so a single sample matches many algorithms by chance. CRCForge warns when that is likely; a third sample usually settles it.
- **Equivalent parameters exist.** When the polynomial is divisible by (x+1), every init/xorout pair has a "twin" that produces identical CRCs for every message. Both are listed and both are correct. With samples of only one length, init and xorout cannot be separated at all; CRCForge then shows the conventional choices.
- **`--all-solutions`** lists every parameter set that fits instead of only the likely answer, and **`--json`** gives everything in machine-readable form.

## Input rules

CRCForge treats the payload (`data`) and the checksum as different kinds of input.

| Payload | Meaning |
| --- | --- |
| `0xDEADBEEF`, `0xDE AD BE EF`, `0xDE_AD_BE_EF` | hex bytes (spaces and underscores are ignored) |
| `0b01001000` | binary bytes |
| `-` | raw bytes from stdin |
| `firmware.bin --as-file` | raw bytes from a file |
| anything else | UTF-8 text (`--as-text` forces this, even for values starting with `0x`) |

Checksums are **decimal unless prefixed**: `19255`, `0x4B37` and `0b0100101100110111` are the same value, while `4B37` is rejected with a hint. The number of hex digits sets the default width (`0x0B37` is 16 bits).

`--offset N`, `--length N` and `--exclude-tail N` select part of the data, for example to skip a header or leave out a stored checksum. In `solve`, samples are written `DATA:CHECKSUM`, and `@path` reads DATA from a file.

## Byte order

CRC mismatches often come from byte layout rather than the algorithm. CRCForge can transform the payload before calculating: `native`, `reverse` (whole stream), `swap16`, `swap32` or `swap64` (bytes inside each word). Separately, it checks whether the **checksum itself** is byte-swapped (`0x4B37` vs `0x374B`) and reports both cases separately as `[input:...]` and `[crc:...]`. Modes that cannot apply (a 9-byte input cannot be swapped in 16-bit words) or that produce the same bytes as another mode are skipped and reported once.

## Python API

```python
import crcforge

crcforge.calculate("crc-16-modbus", b"123456789")  # 0x4B37
[a.name for a in crcforge.find(b"123456789", 0x4B37)]  # ['crc-16-modbus']

samples = [
    crcforge.Sample(b"PING", 0x3197),
    crcforge.Sample(b"PONG", 0x16A1),
    crcforge.Sample(b"PUSH", 0x4524),
    crcforge.Sample(b"STATUS", 0x7CB4),
]
result = crcforge.solve(samples, width=16)
print(result.crc_matches[0].algorithm.reveng_spec())
# width=16 poly=0x3d65 init=0x1234 refin=false refout=false xorout=0x0f0f

patched = crcforge.forge(b"hello world", "crc-32", 0xDEADBEEF).data
custom = crcforge.CrcAlgorithm("mine", 16, poly=0x1021, init=0xFFFF, xor_out=0, refin=False, refout=False)
crcforge.calculate(custom, b"123456789")  # 0x29B1
```

CRC parameters follow RevEng notation, including `init` for reflected CRCs, so values copied from the RevEng catalogue work as-is.

## Scripting

Every command accepts `--json` (structured output) and `-q/--quiet` (just the result). The flags can go before or after the command. Errors go to stderr, with a hint when there is an obvious fix.

| Exit code | Meaning |
| --- | --- |
| 0 | success: a match was found, or the checksum is valid |
| 1 | no match, invalid checksum, or a broken record file |
| 2 | usage or input error |
| 130 | interrupted (Ctrl+C); long searches print what they found so far |

## Documentation

The full guide lives in [`docs/`](https://github.com/kayranfatih/crcforge/tree/main/docs) and on the [documentation site](https://kayranfatih.github.io/crcforge/): concepts, a reverse-engineering walkthrough, forging, every command, the Python API and the [algorithm list](https://github.com/kayranfatih/crcforge/blob/main/docs/algorithms.md). See [CHANGELOG.md](https://github.com/kayranfatih/crcforge/blob/main/CHANGELOG.md) for what changed in each release.

## Development

```bash
python -m pip install -e . -r requirements-dev.txt
python -m pytest                 # tests (python -m unittest discover -s tests also works, without pytest)
ruff check . && ruff format --check . && mypy
```

[CONTRIBUTING.md](https://github.com/kayranfatih/crcforge/blob/main/CONTRIBUTING.md) covers the catalog sync script, the docs and the release process.

## Credits

Fatih Kayran · X/Twitter: [@kayranfatih](https://x.com/kayranfatih)

The CRC catalog comes from Greg Cook's [RevEng CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm).
