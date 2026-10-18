# Command reference

Every command accepts the output options below, before or after the command name, and `--help` for the full option list with examples.

| Option | Effect |
| --- | --- |
| `--json` | machine-readable JSON on stdout (errors become `{"error": ..., "hint": ...}`) |
| `-q`, `--quiet` | only the result value |
| `--no-color` | plain output (the `NO_COLOR` environment variable works too) |
| `--show-banner` | print the banner first |

Commands that read data share these input options:

| Option | Effect |
| --- | --- |
| `--as-file` | read the data from a file (`-` for stdin) |
| `--as-text` | treat the data as text even if it starts with `0x`/`0b` |
| `--offset N` | skip N leading bytes |
| `--length N` | use only N bytes |
| `--exclude-tail N` | ignore the last N bytes |
| `--byte-order MODE` | `native`, `reverse`, `swap16`, `swap32` or `swap64` (most commands) |

Exit codes: **0** success, **1** no match / invalid, **2** usage or input error, **130** interrupted.

## calc

Calculate a checksum.

```bash
crcforge calc crc-16-modbus 123456789                  # built-in algorithm
crcforge calc 123456789 --algorithm crc-32             # same, data first
crcforge calc 0x313233 --width 16 --poly 0x1021 --init 0xFFFF          # custom CRC (RevEng notation)
crcforge calc 123456789 --sum --width 16 --word-size 16 --complement ones   # custom sum checksum
crcforge calc firmware.bin --as-file --all --family crc                # every catalog CRC at once
```

| Option | Effect |
| --- | --- |
| `--algorithm NAME` | built-in algorithm (instead of the first positional value) |
| `--width`, `--poly`, `--init`, `--xor-out`, `--refin`, `--refout` | custom CRC parameters |
| `--sum`, `--word-size BITS`, `--endian`, `--carry`, `--complement` | custom sum checksum (`--width` defaults to 8) |
| `--all` | every catalog algorithm; filter with `--width` and `--family` |
| `--output-format` | `hex`, `dec`, `bin` or `both` (default) |

## find

Match one data/checksum pair against the catalog. Byte-swapped checksums are reported too.

```bash
crcforge find 0x4B37 123456789
crcforge find 19255 123456789 --width 16
crcforge find 0x1A47 0xA1B2C3D4 --scan-byte-order
```

| Option | Effect |
| --- | --- |
| `--width BITS` | only this width |
| `--family crc\|checksum` | only one family |
| `--algorithm NAME` | only these algorithms (repeatable) |
| `--scan-byte-order` | also try every byte-order transform of the data |

## solve

Identify the algorithm behind several samples. See [Reverse-engineering a checksum](guide/reverse-engineering.md) for the full walkthrough.

```bash
crcforge solve 0x0102:0xE181 0x0304:0x8300 0x050607:0xA323
crcforge solve --samples-file samples.txt --width 32
crcforge solve --frames 010300000001840A 010300000002C40B 01030000000AC5CD
crcforge solve --frames @frame1.bin @frame2.bin --crc-at auto --skip auto
```

| Option | Effect |
| --- | --- |
| `sample ...` | `DATA:CHECKSUM` pairs (DATA may be `@file`), or whole frames with `--frames` |
| `--samples-file FILE` | one sample or frame per line, `#` comments allowed (`-` for stdin) |
| `--width BITS` | checksum width; defaults to the widest checksum literal (frames: 8, 16 and 32) |
| `--frames` | the checksum is inside each frame |
| `--crc-at end\|start\|auto` | where the checksum sits in a frame (default `end`) |
| `--crc-endian big\|little\|auto` | byte order of the stored checksum (default `auto`) |
| `--skip N\|auto` | header bytes excluded from the data (`auto` tries 0–8) |
| `--poly`, `--init`, `--xor-out` | restrict the search to these values (specs) |
| `--refin`, `--refout` | `true`, `false` or `auto` (default) |
| `--scan-byte-order` | also try byte-order transforms of the data |
| `--all-solutions` | list every fitting parameter set, and run the polynomial scan even when a catalog algorithm fits |
| `--limit N` | stop after N CRC solutions (default 64) |

## brute

Without custom flags, scan every catalog algorithm against every byte-order transform. With `--poly` or `--full-custom`, search custom CRC parameters for one sample. xorout (or init) is solved, never scanned.

```bash
crcforge brute 0x4B37 123456789
crcforge brute 0x4B37 123456789 --width 16 --poly 0x8005 --init 0:0xFFFF
crcforge brute 0x31C3 123456789 --full-custom --refin false --refout false
```

Value specs: `0x1021`, lists `0x1021,0x8005`, inclusive ranges `0x1000:0x10FF` or `0x1000:0x10FF:0x10`.

| Option | Effect |
| --- | --- |
| `--width BITS` | CRC width (inferred from the checksum with `--full-custom`) |
| `--poly`, `--init`, `--xor-out` | value specs; init and xorout default to 0 |
| `--refin`, `--refout` | `true`, `false` or `auto` (default) |
| `--full-custom` | every polynomial, init and xorout free (widths up to 16; with one sample init/xorout are limited to 0 and all-ones) |
| `--algorithm`, `--family` | catalog mode filters |
| `--jobs N` | worker processes; 0 = automatic |
| `--max-combinations N` | safety limit on evaluations (default 50,000,000) |
| `--limit N` | matches to show (default 100) |
| `--progress-interval SECONDS` | progress cadence when stderr is not a terminal; 0 disables |

## verify

Check that data and a checksum belong together.

```bash
crcforge verify crc-16-modbus 123456789 0x4B37
crcforge verify crc-16-modbus 0x010300000001840A --frame
crcforge verify crc-32 firmware.bin 0xCBF43926 --as-file
```

With `--frame`, the checksum is read from the data (`--crc-at end|start`, `--crc-endian big|little|auto`, `--skip N`). When the checksum only matches after swapping its bytes, `verify` says so. Custom CRC and sum parameters work as in `calc`. Quiet output is `valid` or `invalid`.

## forge

Insert or overwrite bytes so the data produces a target checksum. See [Forging](guide/forging.md).

```bash
crcforge forge crc-32 "hello world" 0xDEADBEEF
crcforge forge crc-16-modbus firmware.bin 0x0000 --as-file --at -2 --overwrite -o patched.bin
crcforge forge sum-8 0x0102030405 0x00
```

| Option | Effect |
| --- | --- |
| `--at POS` | byte offset, negative from the end, `start` or `end` (default) |
| `--overwrite` | replace bytes instead of inserting |
| `--patch-size BYTES` | patch length (default: the checksum width) |
| `-o FILE` | write the forged bytes |
| `--output-format` | `hex`, `compact-hex` or `text` |

## records

Validate an Intel HEX or Motorola S-record file: every record checksum, length byte, record type and the end-of-file record. Reports the data size and address range.

```bash
crcforge records firmware.hex
crcforge records firmware.s19 --json
```

## codegen

Generate a table-driven implementation in C (C99, `<stdint.h>`), Python or Rust. CRCs up to 64 bits work in every language; wider ones only in Python.

```bash
crcforge codegen crc-16-modbus --lang c -o crc16_modbus.c
crcforge codegen --width 16 --poly 0x1021 --init 0xFFFF --lang python --name ccitt
```

The generated header records the parameters and the expected check value.

## info, list, self-test

```bash
crcforge info crc-32              # parameters, aliases, check, residue, RevEng reference
crcforge list --width 16          # filter by width
crcforge list --family checksum   # or by family
crcforge list --search modbus --aliases
crcforge self-test                # verify every catalog entry
```

## transform

Apply a byte-order transform and print the bytes, without any checksum work.

```bash
crcforge transform 0xA9BB7BFD --byte-order swap32
crcforge transform 0x48656C6C6F --output-format text
```
