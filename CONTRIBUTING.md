# Contributing to CRCForge

Thanks for helping. Bug reports, new checksum families, catalog updates and documentation fixes are all welcome.

## Setup

```bash
git clone https://github.com/kayranfatih/crcforge
cd crcforge
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e . -r requirements-dev.txt
```

CRCForge has no runtime dependencies, and that should stay true: use only the standard library in `src/`.

## Checks

Run these before opening a pull request; CI runs the same commands.

```bash
python -m pytest                  # all tests
ruff check . && ruff format --check .
mypy                              # strict mode
```

The tests are plain `unittest` test cases, so `python -m unittest discover -s tests` works without pytest. The C code generation test needs `gcc`/`cc`, or the `ziglang` package (`pip install ziglang`). The Rust test needs `rustc`. Both are skipped when no compiler is found.

## Project layout

| Path | Contents |
| --- | --- |
| `src/crcforge/crc.py` | CRC model and the table-driven engine |
| `src/crcforge/checksums.py` | sum, complement, XOR, Fletcher, Adler, BSD and SysV checksums |
| `src/crcforge/catalog.py` | lookup, filtering and self-validation |
| `src/crcforge/catalog_crc.py` | the RevEng catalogue (generated) |
| `src/crcforge/gf2.py` | GF(2) polynomial arithmetic, factorization and linear algebra |
| `src/crcforge/solver.py` | parameter derivation and the custom search |
| `src/crcforge/frames.py`, `forge.py`, `records.py`, `codegen.py` | the matching features |
| `src/crcforge/cli.py`, `output.py`, `io.py` | command line, output modes and input parsing |

## Updating the CRC catalog

`src/crcforge/catalog_crc.py` is generated from the RevEng CRC catalogue; don't edit it by hand.

```bash
python scripts/sync_reveng_catalog.py         # downloads the live catalogue
python scripts/gen_algorithm_docs.py          # refreshes docs/algorithms.md
python -m pytest tests/test_catalog.py
```

The script checks every entry's check value and residue, and keeps the names CRCForge 1.0 used as primary names (`LEGACY_NAMES`).

## Adding a checksum family

1. Add a frozen dataclass in `checksums.py` with `compute()`, `describe()` and `params()`.
2. Add catalog entries with a `check` value for `123456789` from an independent reference, and cite it in a test.
3. Regenerate `docs/algorithms.md`.

## Documentation

The docs site is built with MkDocs Material:

```bash
python -m pip install -r docs/requirements.txt
mkdocs serve
```

## Releasing

1. Update `__version__` in `src/crcforge/__init__.py` and add a section to `CHANGELOG.md`.
2. Commit, then tag: `git tag v1.2.0 && git push origin main v1.2.0`.
3. The release workflow tests, builds and publishes to PyPI (trusted publishing), then creates the GitHub release from the changelog section.

One-time setup: on pypi.org, add this repository's `release.yml` workflow with the `pypi` environment as a trusted publisher. In the repository settings, set Pages to deploy from GitHub Actions.
