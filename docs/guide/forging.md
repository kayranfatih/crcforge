# Forging

`forge` changes a few bytes of the data so it produces a checksum you choose. Typical uses: patching firmware without breaking its integrity check, building test vectors, or proving that a checksum offers no protection against deliberate changes.

```console
$ crcforge forge crc-32 "hello world" 0xDEADBEEF
Forge
Algorithm        crc-32: width=32, poly=0x04C11DB7, init=0xFFFFFFFF, refin=true, refout=true, xorout=0xFFFFFFFF
Original data    plain-text, 11 bytes
Target           0xDEADBEEF
Patch            46 C9 6E 0B (inserted at offset 11)
Forged data      68 65 6C 6C 6F 20 77 6F 72 6C 64 46 C9 6E 0B
Verified         0xDEADBEEF == target
```

## Where the patch goes

| Option | Effect |
| --- | --- |
| *(default)* | append the patch at the end |
| `--at start` | insert at the beginning |
| `--at 0x100` | insert at byte offset 0x100 |
| `--at -4` | offset counted from the end |
| `--overwrite` | replace bytes at `--at` instead of inserting (the length stays the same) |
| `--patch-size N` | use N patch bytes (default: the checksum width in bytes) |
| `-o FILE` | write the forged bytes to FILE |

To keep a firmware image's size and fix its trailing CRC-16:

```bash
crcforge forge crc-16-modbus firmware.bin 0x0000 --as-file --at -2 --overwrite -o patched.bin
```

`-q` prints only the forged data (compact hex by default), and `--json` includes the patch, offset and data.

## How it works

- **CRCs** are affine over GF(2), so each patch bit flips a fixed set of output bits regardless of the rest of the data. CRCForge sets up a `width × patch-bits` linear system and solves it. Any target is reachable with `ceil(width / 8)` patch bytes. A rare exception is when the chosen bytes cannot influence every output bit; CRCForge then asks for another position or a larger patch.
- **Sum and XOR checksums** are solved arithmetically. The patch must start on a word boundary; when appending to data whose length isn't a multiple of the word size, CRCForge pads with zero bytes first. Narrow words in a wide sum (for example bytes in `sum-32`) can only add a little each, so distant targets need long patches. CRCForge refuses above 1024 words and explains why.
- A one's complement sum (with end-around carry) is never zero for non-zero data, so a checksum that would require a zero sum cannot be forged.
- Fletcher, Adler-32 and the BSD/SysV sums are not supported for forging.

Every forged result is recomputed with the regular engine before it is printed.
