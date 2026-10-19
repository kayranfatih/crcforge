# CRCForge

**CRCForge** is a Swiss Army knife for CRCs and checksums, built for reverse engineers, protocol analysts and firmware researchers. Give it some data and its checksum and it checks 134 known algorithms. If none of them match, it can work out the CRC parameters from a few samples.

<div class="grid" markdown>

- **134 algorithms**: all RevEng CRCs plus sum, complement, XOR, Fletcher, Adler and BSD/SysV checksums
- **Solver**: works out poly, init, xorout and reflection from a few samples
- **Frames**: finds the checksum inside captured frames
- **Forge**: patches data to get the CRC or checksum you want
- **Records and code**: checks Intel HEX and S-record files and generates C, Python and Rust code
- **Scripting**: `--json`, `-q`, exit codes, a Python API and no dependencies

</div>

## Install

```bash
pipx install crcforge
```

`pip install crcforge` works too. CRCForge needs Python 3.10 or newer. For the latest code: `pipx install git+https://github.com/kayranfatih/crcforge`.

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

The last command finds a CRC that isn't in any catalog from four short samples.

## Where to go next

- [Concepts](guide/concepts.md): how data and checksums are read, byte order and parameter notation.
- [Reverse-engineering a checksum](guide/reverse-engineering.md): the workflow and how the solver works.
- [Forging](guide/forging.md): patching data to get a target checksum.
- [Command reference](commands.md): every command and option.
- [Python API](python-api.md): use CRCForge from your own code.
- [Algorithms](algorithms.md): the full catalog.
