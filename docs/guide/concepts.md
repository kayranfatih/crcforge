# Concepts

## Data and checksums are different kinds of input

CRCForge deliberately reads the payload (`original-data`) and the checksum differently. A payload like `123456789` is usually text; a checksum like `19255` is usually a number.

### Payload rules

| You write | CRCForge uses |
| --- | --- |
| `0xDEADBEEF` | the bytes `DE AD BE EF` |
| `0xDE AD BE EF`, `0xDE_AD_BE_EF`, `0xDE0xAD0xBE0xEF` | the same bytes; spaces, underscores and repeated `0x` are ignored |
| `0b0100100001101001` | the bytes `48 69` (8 bits per byte) |
| `-` | raw bytes read from stdin |
| `path --as-file` | raw bytes of the file |
| anything else, or `--as-text` | the UTF-8 encoding of the text |

Hex payloads need two digits per byte, and binary payloads a multiple of 8 bits. CRCForge says so, and suggests `--as-text` when the value was meant as text (for example `0xygen`). When a text payload happens to name an existing file, CRCForge adds a note suggesting `--as-file`.

To process part of the data, use `--offset N` (skip leading bytes), `--length N` (take N bytes) and `--exclude-tail N` (drop trailing bytes, for example a stored checksum).

### Checksum rules

| You write | Value | Default width |
| --- | --- | --- |
| `0x4B37` | 19255 | 16 bits: 4 hex digits × 4 |
| `0x0B37` | 2871 | 16 bits: leading zeros count |
| `0b0100101100110111` | 19255 | 16 bits: one per binary digit |
| `19255` | 19255 | 16 bits: rounded up to whole bytes |
| `4B37` | rejected | CRCForge suggests `0x4B37` |

The default width only matters for commands that need a width and don't have `--width`, such as `solve` and `brute --full-custom`. CRCForge prints how it inferred the width.

## Parameter notation

CRC parameters follow the Rocksoft model in **RevEng notation**:

| Parameter | Meaning |
| --- | --- |
| `width` | number of bits in the CRC |
| `poly` | generator polynomial without the top bit, MSB-first (for example `0x1021`) |
| `init` | initial register value, MSB-first, **even for reflected CRCs** |
| `refin` | reflect each input byte |
| `refout` | reflect the register before the final XOR |
| `xorout` | value XORed into the result |
| `check` | the CRC of the ASCII string `123456789` |
| `residue` | register contents after a valid codeword, before the final XOR |

Values copied from the RevEng catalogue therefore work unchanged:

```console
$ crcforge calc 123456789 --width 16 --poly 0x1021 --init 0x89EC --refin true --refout true -q
0x26B1
```

!!! note "Changed in 1.1"
    CRCForge 1.0 treated `--init` as already reflected when `--refin true`. That only made a difference when init was not a bit palindrome, but it made some RevEng parameters give wrong results. See the [changelog](../changelog.md).

## Byte order

Mismatches often come from byte layout rather than the algorithm. CRCForge can transform the payload before calculating:

| Mode | `0x1122334455667788` becomes |
| --- | --- |
| `native` | `11 22 33 44 55 66 77 88` |
| `reverse` | `88 77 66 55 44 33 22 11` |
| `swap16` | `22 11 44 33 66 55 88 77` |
| `swap32` | `44 33 22 11 88 77 66 55` |
| `swap64` | `88 77 66 55 44 33 22 11` |

The **checksum itself** can also be stored byte-swapped (`0x4B37` stored as `37 4B`). `find`, `brute` and `solve` try that too and label each result with what had to change: `[input:swap16]` for the payload, `[crc:byte-swapped]` for the checksum.

Modes that cannot apply (a 9-byte input has no whole 16-bit words) or that produce exactly the same bytes as an earlier mode (`reverse` and `swap32` on 4 bytes) are skipped and listed once in a note.

## Families

- **CRCs**: the 113 definitions of the RevEng catalogue. Each entry records RevEng's status: *attested*, *confirmed*, *academic* or *third-party*. See [Algorithms](../algorithms.md).
- **Checksums**: word sums with optional one's/two's complement (including the Internet checksum, Intel HEX and S-record checksums), XOR, Fletcher-16/32/64, Adler-32, and the BSD and SysV `sum` algorithms.

`crcforge info NAME` shows any entry in detail, and `crcforge self-test` re-verifies all of them.
