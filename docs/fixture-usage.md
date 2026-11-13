# TX003 development fixture

This fixture is for **development and noncommercial demonstration**. The
[UTSA-flood-images dataset card](https://huggingface.co/datasets/jennifer-fowler/UTSA-flood-images)
is **CC-BY-NC-4.0**. The manifest is **unsigned and must never be accepted in
production**. A successful verification checks development-format consistency
and integrity against this mutable manifest; it does not authenticate a publisher.

## Run

From the repository root, use Python 3.11+ with SQLite R-tree/STRICT support and
Pillow with WebP support. Install the pinned Python dependency if needed:

```sh
python3 -m pip install -e .
python3 -m crystal_geo build-fixture testdata/tiny_valid_generation --force
python3 -m crystal_geo verify testdata/tiny_valid_generation
python3 -m unittest discover -s tests -v
git diff --check
```

`build-fixture` downloads missing PNGs into `testdata/source_images/` from the
pinned Hugging Face revision below, with normal TLS certificate/hostname
verification, HTTPS-only redirects, a timeout, and an 8 MiB per-source limit.
Only hash-verified downloads enter the cache. Existing cached PNGs are checked
again; a bad hash fails instead of silently replacing data. Both hashes pass
before decoding begins. The included source cache makes rebuilding and tests
work offline; it contains the original, unchanged PNGs downloaded during this
implementation.

To use the existing local dataset clone, without any network request:

```sh
python3 -m crystal_geo build-fixture /tmp/tx003-demo --source-dir /home/dell/UTSA-flood-images/images
```

`--force` replaces only an existing **valid fixture**, preserving unrelated or
corrupted directories. Move a corrupted generation aside before rebuilding.
Builds finish and verify in a sibling temporary directory before publication.
Errors print `ERROR: ...` to stderr and exit with status 1. Output files are
read-only (0444), written once, and never edited in place by the builder.

## Sources and geography

Pinned dataset revision: `ad98e681e7cf6af9ced145833b522c4f6c0acad1`.
Selected provenance is in `crystal_geo/tx003.json`, matched against both the
local CSV and the remote CSV at that revision. The local GB10 teaching repository
was reviewed at `ef6954a23785346569ada9415d506b8dda8a9ebc`; its recognition/model
code is outside this slice.

| Variant | Image | Acquisition date | Expected source SHA-256 |
| --- | --- | --- | --- |
| before | TX003_2016_NAIP.png | 2016-09-28 | b0868cdde0a6cb87ce4e45e05fc4ce1b9200ea1f1a30610ffd3cd96f61539597 |
| after | TX003_2017_NOAA.png | 2017-08-30 | 015326ed0fe6529afb99dc48c4fd1e99c98b4c67a649573db94a081b9705596d |

The dates have day precision, not exact UTC capture times. USDA NAIP attribution:
USDA-FSA-APFO Aerial Photography Field Office; hosted by Microsoft Planetary
Computer. Its recorded source terms say no use constraints, with attribution
requested. NOAA attribution: NOAA National Geodetic Survey. Its recorded source
terms are CC0-1.0 with NOAA metadata use notes preserved. These source-specific
terms and source URLs remain in the manifest alongside the dataset card's
CC-BY-NC-4.0 label; government imagery does not imply endorsement.

Both 1024×1024 PNGs lack embedded georeferencing. The dataset CSV supplies WGS84
coverage `[-95.625, 29.84064389983442, -95.6195068359375, 29.84540862642843]`
in west/south/east/north order and identifies the pixel grid as EPSG:3857.
Each variant is split into a 4×4 grid of 256×256 tiles without resampling.
The tiles use global XYZ zoom 18, x=61440..61443, y=108284..108287.
Latitude edges use inverse Web Mercator, rather than linear latitude division.
The index stores EPSG:4326 bounds and a validated R-tree; floating-point R-tree
bounds have SQLite's float32 precision. Fine registration between dates is not
assumed, and upsampling the original NAIP data added no source detail.

## Development format

`testdata/tiny_valid_generation/` contains exactly:

```text
manifest.json
index.sqlite
containers/container-000001.bin
```

The strict SQLite schema is in `schemas/index.sql`; a matching packaged copy
is in `crystal_geo/index.sql`, with equality checked by a test. Metadata and
source tables are linked to tiles through foreign keys. Tile rows include
generation, before/after variant, global coordinates, bounds, dimensions,
container ranges, codec parameters, and stored-byte SHA-256. The source table
holds image filename, SHA-256, acquisition date, source agency, credits and
reuse terms.

The container begins with a 256-byte header: `CGEOdev1` magic (8 bytes),
big-endian u32 version and header size, 64-byte NUL-padded generation ID,
64-byte NUL-padded container ID, 80 reserved zero bytes, and SHA-256 of the
preceding 224 bytes. Independent lossless WebP payloads follow in deterministic
before/after, row, column order. Ranges must be contiguous, nonoverlapping,
within the declared container size, and cover all bytes after the header.
The file is capped at 16 MiB; each compressed payload at 1 MiB. Each decode is
limited to a verified 256×256 WebP lossless image. Alpha channels are retained
as RGBA. The fixture has 32 tiles, a 65,536-byte index, and a 1,756,912-byte
container with the recorded toolchain.

The manifest is sorted compact JSON with one trailing newline, not the future
production CBOR format. It records file sizes/hashes, source hashes, coverage,
provenance, codec settings, toolchain, and explicit unsigned status. The reader
enforces a 64 KiB manifest limit and a 1 MiB index limit, rejects duplicate JSON
keys, unsafe paths, symlinks, extra package files, unsupported schemas/profiles,
generation mismatches, invalid ranges and hashes, then decodes each tile only
after its stored-byte hash succeeds. Python integer arithmetic cannot wrap;
offset checks also enforce `length <= container_size - offset`.

Rebuilds were byte-identical with Python 3.12.3, Pillow 10.2.0, libwebp 1.3.2,
and SQLite 3.45.1. The Python dependency/build backend are pinned; native WebP
and SQLite versions are recorded, not bundled. Different native toolchains can
produce different bytes. The reproduction test compares against the included
generation and therefore requires this toolchain for byte-exact equality.

## Checks and limits

Tests cover all requested rejection cases, deeper structural checks after
refreshing outer hashes, source hash enforcement before decode, pixel-exact
round trips, reproducibility, HTTPS downgrade rejection, certificate validation,
download cleanup, strict SQL constraints, geographic bounds and R-tree entries,
unsafe paths/symlinks, and CLI failures.

This is a TX003 Python development format, not a general geospatial packager
or the Rust trusted mobile core. It has no publisher authentication, rollback
protection, protected storage, downloads with resume, Flutter UI, recognition,
encryption, or production signing. The separate [generation store](generation-store.md)
adds staged installation, durable-write ordering, atomic activation and pinned
readers. `build-fixture --generation-id tx003-dev-v2` creates another generation
with the same imagery and a distinct safe identifier; identifiers are never
overwritten in the installed store. The packaging command's `--force` still
uses two directory renames with restoration on ordinary error;
a crash between them can temporarily leave the output path absent. Use a single
writer in a trusted local directory: read-only modes and symlink checks do not
protect against a concurrent adversary replacing files, parent directories, or
the unsigned manifest. Someone who changes all relevant hashes can author a
different internally consistent unsigned fixture. Integrity does not establish
real-world accuracy, imagery freshness, or flood conditions.
