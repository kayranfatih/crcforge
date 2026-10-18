# CRCForge

**CRCForge** is a Swiss Army knife for CRCs and checksums, built for reverse engineers, protocol analysts and firmware researchers. It answers the question "I have bytes and a checksum: how was it made?", even when the algorithm is not in any catalog.

<div class="grid" markdown>

- **134 built-in algorithms**: the complete RevEng CRC catalogue plus sum, complement, XOR, Fletcher, Adler and BSD/SysV checksums, all verified.
- **Parameter solver**: derives poly, init, xorout, refin and refout from a few samples, for any width.
- **Frame analysis**: locates the checksum inside captured frames.
- **Forge**: makes data produce the CRC or checksum you choose.
- **Records and code**: validates Intel HEX/S-record files, and generates C, Python and Rust implementations.
- **Scriptable**: `--json`, `--quiet`, exit codes, a Python API, no dependencies.

</div>

## Install

```bash
pipx install crcforge
```

or `python -m pip install crcforge`. Python 3.10+ is required; there are no third-party dependencies. To run the latest code: `pipx install git+https://github.com/kayranfatih/crcforge`.

## A first session

```console
$ crcforge calc crc-16-modbus 123456789 -q
0x4B37

$ crcforge find 0x4B37 123456789 -q
crc-16-modbus

$ crcforge solve --frames 010300000001840A 010300000002C40B 01030000000AC5CD -q
# 16-bit checksum at the end, little-endian
crc-16-modbus

$ crcforge solve PING:0x3197 PONG:0x16A1 PUSH:0x4524 STATUS:0x7CB4 -q
width=16 poly=0x3d65 init=0x1234 refin=false refout=false xorout=0x0f0f
```

The last command recovers a CRC that is in no catalog from four short samples.

## Where to go next

- [Concepts](guide/concepts.md): how CRCForge reads data and checksums, byte order, and parameter notation.
- [Reverse-engineering a checksum](guide/reverse-engineering.md): the step-by-step workflow and how the solver works.
- [Forging](guide/forging.md): making data hit a target checksum.
- [Command reference](commands.md): every command and option.
- [Python API](python-api.md): use CRCForge from your own code.
- [Algorithms](algorithms.md): the full catalog.
