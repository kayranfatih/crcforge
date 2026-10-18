# Reverse-engineering a checksum

You have data and the checksum that goes with it, and you want to know how the checksum was computed. Work through these steps in order; each one is more general than the one before.

## 1. Try the catalog: `find`

```console
$ crcforge find 0x4B37 123456789
CRC Match Search
Checksum         0x4B37
Original data    plain-text
Byte-order scan  native
Matches          1
Match            [crc:as-given] crc-16-modbus: width=16, poly=0x8005, init=0xFFFF, refin=true, refout=true, xorout=0x0000
```

`find` checks all 134 algorithms, and also checks the checksum with its bytes swapped. Add `--scan-byte-order` if the payload might be stored in a different byte order, and `--width`/`--family` to narrow the search.

!!! warning "One sample can mislead"
    A 16-bit checksum has 65,536 possible values, so with about 40 catalog algorithms of that width a single sample occasionally matches the wrong one, and wide custom searches match many. Confirm with a second sample, which is what `solve` is for.

## 2. Use several samples: `solve`

Give `solve` a few `DATA:CHECKSUM` pairs. It checks the catalog first and then derives CRC parameters, so it also finds algorithms that are in no catalog:

```console
$ crcforge solve PING:0x3197 PONG:0x16A1 PUSH:0x4524 STATUS:0x7CB4
CRC Solver
Samples          4 (lengths: 4, 6 bytes)
Width            16 bits (inferred from 4 hex digits; pass --width to override)
Derived CRC      width=16 poly=0x3d65 init=0x1234 refin=false refout=false xorout=0x0f0f
                 or init=0xf917 xorout=0xe42c (equivalent)
note: Some polynomials are divisible by (x+1); for those each init/xorout pair has equivalent twins that give identical CRCs for every message. All twins are listed and all are correct.
```

### Choosing samples

- **Include at least two samples of the same length.** They cancel init and xorout, which reveals the polynomial directly, for any width. Three or more equal-length samples make coincidences unlikely.
- **Include samples of a different length** to pin down init and xorout. With a single length, many init/xorout pairs give identical results for every sample, and CRCForge shows the conventional choices (init 0 or all-ones).
- **Longer and more varied samples are better.** Two-byte messages leave room for many coincidental fits.
- Without any equal-length pair, polynomials of up to 16 bits are found by scanning all of them. That takes several seconds, spread over every CPU core, and is skipped when a catalog algorithm already fits (`--all-solutions` runs it anyway). Wider CRCs need an equal-length pair or `--poly`.

Samples can come from files (`@frame.bin:0x1234`) or from a samples file with one sample per line (`--samples-file samples.txt`; `#` starts a comment).

### Reading the result

| Line | Meaning |
| --- | --- |
| `Catalog CRC` | a built-in algorithm fits every sample |
| `Derived CRC` | CRC parameters derived from the samples, in RevEng notation, ready to paste into `calc` or RevEng |
| `= crc-16-x (built-in)` | the derived parameters are a catalog algorithm |
| `or init=... xorout=... (equivalent)` | another init/xorout pair that gives identical results |
| `Checksum` | a sum, complement, XOR or other checksum fits |
| `Other fits` | further parameter sets fit too; usually coincidences when a catalog algorithm already matched |

`--all-solutions` lists everything that fits, and `--json` returns it all in machine-readable form.

### Why some parameters come in pairs

When the polynomial is divisible by (x+1), which is every polynomial with an even number of terms, two different init/xorout pairs produce identical CRCs for **every** message of every length. No sample can tell them apart, so CRCForge lists both. Either one is a correct description of the checksum; published specs simply picked one.

## 3. The checksum is inside the data: `solve --frames`

Captured traffic usually contains the checksum inside each frame. Pass the frames as hex (or `@file` for binary):

```console
$ crcforge solve --frames 010300000001840A 010300000002C40B 01030000000AC5CD 0106000100FF984A
CRC Solver (frames)
Frames           4 (lengths: 8 bytes)
Layouts tested   5
Layout           16-bit checksum at the end, little-endian
Catalog CRC      crc-16-modbus: width=16, poly=0x8005, init=0xFFFF, refin=true, refout=true, xorout=0x0000
```

By default CRCForge tries 8-, 16- and 32-bit checksums at the end of the frame, in both byte orders. To widen the search:

| Option | Effect |
| --- | --- |
| `--width N` | only this checksum width |
| `--crc-at start` / `--crc-at auto` | checksum at the start / try both ends |
| `--crc-endian big` / `little` | fix the byte order of the stored checksum |
| `--skip N` / `--skip auto` | exclude N header bytes (sync words, addresses) from the checksummed data / try 0 to 8 |

Once you know the layout, `verify --frame` checks new frames:

```console
$ crcforge verify crc-16-modbus 0x010300000001840A --frame -q
valid
```

## 4. One sample, partial knowledge: `brute`

Without custom flags, `brute` runs the catalog against every byte-order transform. With `--poly` or `--full-custom` it searches CRC parameters for a single sample:

```console
$ crcforge brute 0x4B37 123456789 --width 16 --poly 0x8000:0x80FF --init 0xFFFF
```

- **xorout is never scanned.** It is solved directly from the sample (or init is, when you fix xorout), so `--init 0:0xFFFF` costs the same as a single init value.
- Value specs accept single values, lists (`0x1021,0x8005`) and ranges (`0x1000:0x10FF[:STEP]`).
- `--full-custom` searches every polynomial of the width (up to 16 bits). With only one sample, a free init and a free xorout fit every polynomial, so CRCForge limits both to the conventional values 0 and all-ones and says so.
- CRCForge prints how many coincidental matches to expect. When that number is large, the results can't identify the CRC; collect more samples and use `solve`.
- Large searches run on every CPU core (`--jobs` to control). Progress with an ETA appears on stderr, and Ctrl+C prints what was found so far.

## How the solver works

A CRC is *affine* over GF(2). For fixed width, poly, refin and refout:

```text
crc(data) = raw(data) XOR Z_len(init) XOR xorout
```

Here `raw` is the CRC with init and xorout set to zero, and `Z_len` is a linear map that depends only on the message length.

1. **Polynomial.** For two messages of the same length, init and xorout cancel: `crc1 XOR crc2 = raw(m1 XOR m2)`. In polynomial terms, the generator divides `(M1 + M2)·x^width + (C1 + C2)`. CRCForge takes the GCD of these polynomials over all equal-length pairs. When the GCD has a higher degree than the width, it is factored (Cantor–Zassenhaus) and every divisor of the right degree is a candidate.
2. **init and xorout.** With the polynomial known, each sample gives `width` linear equations in the `2·width` unknown bits of init and xorout. Gaussian elimination gives the solution, or the space of equivalent solutions.
3. **Verification.** Every reported parameter set is recomputed with the regular CRC engine against all samples.

This is the same idea behind Greg Cook's RevEng. CRCForge adds the frame-layout search, byte-order handling and the catalog of checksum families.
