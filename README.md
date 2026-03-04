# CRCForge

![CRCForge logo](docs/assets/crcforge_logo.png)

**CRCForge** is a Swiss Army knife for CRC tasks, built for reverse engineers, protocol analysts, firmware researchers, and anyone who needs to identify, verify, or brute-force CRC behavior from raw data. It combines CRC calculation, algorithm discovery, byte-order testing, checksum byte-swap detection, and custom parameter brute-force in one CLI, so you can move from "I have bytes and a checksum" to "I know exactly how this CRC was produced" without jumping between multiple scripts.

## Installation

CRCForge is packaged as a standard Python CLI. It currently uses only Python's standard library at runtime, so there are no third-party runtime dependencies to install.

Basic setup:

```powershell
git clone <this-repo-url>
cd crcforge
python -m pip install -e .
crcforge
```

For a local checkout without installation, `python crcforge.py` still works as a compatibility wrapper.

Primary entrypoints:

```powershell
crcforge
python -m crcforge
```

Running either entrypoint without a subcommand prints the built-in help screen.

## What CRCForge Is For

CRCForge is designed for workflows like these:

- You have a payload and want to calculate a CRC with a known algorithm.
- You captured traffic, a firmware blob, or a frame dump and need to figure out which known CRC matches it.
- You suspect the payload bytes are in the wrong endianness.
- You suspect the checksum bytes themselves are swapped.
- You have a custom CRC and need to brute-force `poly`, `init`, `xor-out`, `refin`, and `refout`.
- You want one tool that can calculate, search, transform, and brute-force CRCs without switching utilities.

## Built-in Catalog

The built-in catalog is based on the RevEng CRC catalogue:

- https://reveng.sourceforge.io/crc-catalogue/

RevEng attested and confirmed CRC definitions are included here. When multiple RevEng names map to the same exact parameter set, CRCForge stores one primary implementation and exposes the rest as aliases.

## Core Model

CRCForge intentionally treats the payload (`original-data`) and the target checksum (`checksum`) as different kinds of input.

### `original-data`

`original-data` is the raw payload whose CRC you want to calculate, test, or brute-force against.

Interpretation rules:

- Starts with `0x` -> parsed as raw hex bytes
- Starts with `0b` -> parsed as raw binary bytes
- Anything else -> parsed as plain UTF-8 text
- `--as-file` -> read raw bytes from disk

Valid examples:

```text
0xDEADBEEF
0xDE AD BE EF
0xDE_AD_BE_EF
0xAA0xBB0xCC
0b0100100001101001
0b010010000b01101001
HELLO
123456789
frame_header_v2
```

Example:

```powershell
crcforge calc crc-16-modbus 0x313233343536373839
```

This uses the raw hex byte stream:

```text
31 32 33 34 35 36 37 38 39
```

Those bytes are hexadecimal values, and in ASCII they decode to:

```text
123456789
```

While this:

```powershell
crcforge calc crc-16-modbus 123456789
```

uses the UTF-8 text bytes of `123456789`.

### `checksum`

`checksum` is the CRC value you expect, verify, or search for.

Interpretation rules:

- Starts with `0x` -> parsed as hexadecimal
- Starts with `0b` -> parsed as binary
- Plain digits without a prefix -> parsed as decimal
- Values containing hex letters (`A-F`) without `0x` are rejected on purpose

Valid examples:

```text
0x4B37
19255
0b0100101100110111
```

This distinction is deliberate. A payload like `123456789` is usually meaningful as text. A checksum like `19255` is usually meaningful as a number.

## Quick Start

Calculate a known CRC:

```powershell
crcforge calc crc-16-modbus 123456789
```

Find which built-in CRC matches a payload and checksum:

```powershell
crcforge find 0x4B37 123456789 --width 16
```

Brute-force across the full built-in catalog and all byte-order modes:

```powershell
crcforge brute 0x1A47 firmware_block_01 --width 16
```

Transform payload bytes without doing any CRC work:

```powershell
crcforge transform 0xA9BB7BFD --byte-order swap32
```

## Command Reference

### `calc`

Use `calc` when you already know the CRC algorithm, or when you want to test one exact custom parameter set.

Built-in algorithm mode:

```powershell
crcforge calc crc-16-modbus 0x313233343536373839
crcforge calc crc-16-modbus 123456789
crcforge calc crc-16-modbus 0b001100010011001000110011001101000011010100110110001101110011100000111001
crcforge calc 123456789 --algorithm crc-16-modbus
```

Custom parameter mode:

```powershell
crcforge calc 123456789 --width 16 --poly 0x1021 --init 0xFFFF --refin false --refout false
crcforge calc 0x313233343536373839 --width 82 --poly 0x0308C0111011401440411 --init 0x0 --xor-out 0x0 --refin true --refout true
```

Use `calc` when:

- you know the algorithm name
- you want a direct CRC result
- you want to verify one exact custom parameter set

### `find`

Use `find` when you want to match a payload and checksum against the built-in catalog.

Examples:

```powershell
crcforge find 0x4B37 0x313233343536373839 --width 16
crcforge find 19255 123456789 --width 16
crcforge find 0b0100101100110111 123456789 --width 16
crcforge find 0x1A47 packet_payload_v3 --width 16 --scan-byte-order
```

`find` can detect:

- matching built-in CRC algorithms
- input byte-order issues
- checksum byte-swapping issues

Use `find` when:

- you think the CRC is probably a known algorithm
- you want a filtered search
- you want to scan one mode or all byte-order modes

### `brute`

Use `brute` when you want the widest search behavior.

Without custom flags, `brute` scans:

- the full built-in catalog
- all supported input byte-order transforms
- both checksum interpretations: `as-given` and `byte-swapped`

Examples:

```powershell
crcforge brute 0x4B37 0x313233343536373839
crcforge brute 0x4B37 123456789 --width 16
crcforge brute 0x1A47 packet_payload_v3 --width 16
```

#### Custom brute-force

If you provide `--poly` or `--full-custom`, `brute` switches into custom parameter brute-force.

Custom brute can scan:

- `poly`
- `init`
- `xor-out`
- `refin`
- `refout`
- all supported input byte-order transforms
- both checksum interpretations: `as-given` and `byte-swapped`

Targeted custom brute examples:

```powershell
crcforge brute 0x29B1 123456789 --width 16 --poly 0x1021 --init 0xFFFF --refin false --refout false
crcforge brute 0x4B37 0x313233343536373839 --width 16 --poly 0x8005 --init 0x0000:0xFFFF --xor-out 0x0:0xFFFF
crcforge brute 0x906E 123456789 --width 16 --poly 0x1021,0x8005 --refin auto --refout auto
```

Full-range custom brute example:

```powershell
crcforge brute 0x4B37 123456789 --full-custom --max-combinations 100000
```

`--full-custom` behavior:

- expands omitted `poly`, `init`, and `xor-out` to the full range for the selected width
- keeps `refin` and `refout` controlled by `auto`, `true`, or `false`
- infers `--width` from the checksum when `--width` is not supplied
- prints the inferred width in the output so the search is transparent
- prints live progress updates during long scans
- lets you control progress output cadence with `--progress-interval`

Custom parameter spec format:

- single value: `0x1021`
- comma list: `0x1021,0x8005`
- inclusive range: `0x1000:0x10FF`
- inclusive range with step: `0x1000:0x10FF:0x10`

Use `brute` when:

- the CRC family is unknown
- the payload may be byte-swapped
- the checksum may be byte-swapped
- the CRC may be custom and not part of the built-in catalog

### `transform`

Use `transform` when you only want to normalize or inspect byte order.

`transform` accepts the same input forms as the CRC commands, so it is useful as a quick "what bytes am I actually testing?" sanity check before running `calc`, `find`, or `brute`.

Input handling:

- values starting with `0x` are parsed as raw hex bytes
- values starting with `0b` are parsed as raw binary bytes
- all other values are treated as UTF-8 text
- `--as-file` reads raw bytes directly from a file path
- `--as-text` forces plain text mode even if the value starts with `0x` or `0b`

Data rules:

- hex input must contain a full number of bytes, so it needs an even number of hex digits
- binary input must contain full bytes, so its bit count must be divisible by 8
- spaces and underscores inside hex and binary literals are normalized before parsing
- plain text is encoded as UTF-8 before the byte-order transform is applied

Output handling:

- default output is spaced hex, such as `41 42 43 44`
- `--output-format compact-hex` prints compact uppercase hex, such as `41424344`
- `--output-format text` decodes the transformed bytes as UTF-8 and replaces invalid bytes if needed

Examples:

```powershell
crcforge transform 0xA9BB7BFD --byte-order swap32
crcforge transform 0x12345678 --byte-order reverse
crcforge transform 0x48656C6C6F --output-format text
crcforge transform 0b0100000101000010 --output-format text
crcforge transform demo.bin --as-file --byte-order swap16
crcforge transform 0x48656C6C6F --as-text
```

What these mean:

- `0xA9BB7BFD` is interpreted as four bytes: `A9 BB 7B FD`
- `0b0100000101000010` is interpreted as two bytes: `41 42`, which renders as `AB` in text output
- `demo.bin --as-file` loads the file contents exactly as stored on disk
- `0x48656C6C6F --as-text` treats the literal characters `0x48656C6C6F` as text, not as hex bytes

This is useful for:

- fixing endian issues before CRC analysis
- checking how a payload looks after swapping
- testing whether a protocol field was stored in the wrong byte order

### `list`

Use `list` to inspect the built-in CRC catalog.

Examples:

```powershell
crcforge list
crcforge list --width 16
crcforge list --width 82
crcforge list --aliases
crcforge list --width 16 --aliases
```

`list` now shows:

- `Algorithms`: primary parameter-set count
- `Aliases`: additional alias-name count
- `Names total`: all accessible names (`Algorithms + Aliases`)

### `self-test`

Use `self-test` to validate the built-in catalog against the standard `123456789` check vectors.

```powershell
crcforge self-test
```

### `banner`

Use `banner` to print the ASCII banner and credits.

```powershell
crcforge banner
```

## Byte Order / Endianness

CRC mismatches often come from byte layout, not from the polynomial itself.

CRCForge supports these input transforms:

- `native`: use bytes exactly as provided
- `reverse`: reverse the entire byte stream
- `swap16`: reverse bytes inside each 2-byte word
- `swap32`: reverse bytes inside each 4-byte word
- `swap64`: reverse bytes inside each 8-byte word

Example using `0x11223344`:

- `native` -> `11 22 33 44`
- `reverse` -> `44 33 22 11`
- `swap16` -> `22 11 44 33`
- `swap32` -> `44 33 22 11`

On a single 4-byte value, `reverse` and `swap32` can look identical. The difference becomes clearer on longer inputs.

Example using `0x1122334455667788`:

- `native` -> `11 22 33 44 55 66 77 88`
- `reverse` -> `88 77 66 55 44 33 22 11`
- `swap16` -> `22 11 44 33 66 55 88 77`
- `swap32` -> `44 33 22 11 88 77 66 55`
- `swap64` -> `88 77 66 55 44 33 22 11`

Practical endianness examples:

- a 16-bit field documented as `0x1234` may be transmitted as bytes `34 12`; `swap16` corrects that
- a 32-bit value `0xA1B2C3D4` stored little-endian may appear as `D4 C3 B2 A1`; `swap32` corrects that
- if an entire payload was captured in reverse order, `reverse` tests that case directly

Alignment rules:

- `swap16` requires input length divisible by 2
- `swap32` requires input length divisible by 4
- `swap64` requires input length divisible by 8

If a transform does not fit the payload length, CRCForge skips that mode and reports the reason instead of aborting the entire scan.

CRCForge also distinguishes two different swap problems:

- `input` swap: the payload bytes are arranged in the wrong byte order
- `crc` swap: the checksum bytes themselves are byte-swapped, such as `0x471A` vs `0x1A47`

That distinction matters:

- if the payload is wrong, you need to transform the input bytes before calculating or matching the CRC
- if only the checksum is wrong, the payload may already be correct and only the reported CRC bytes need swapping

## Practical Notes

- `find` and built-in `brute` work against the built-in catalog.
- custom `brute` mode is the path for unknown, non-catalog CRCs.
- `--max-combinations` protects custom brute-force from exploding into impractical search spaces.
- `--progress-interval` controls how often long custom brute scans report progress.
- aliases let you search common CRC names even when several names resolve to the same underlying parameter set.
- if you are unsure whether the problem is the payload layout or the checksum layout, use `find --scan-byte-order` or `brute` first.

## Credits

Fatih Kayran

X/Twitter: https://x.com/kayranfatih
