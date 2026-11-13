# Automated TX003 compression evaluation

One command builds candidates, verifies source hashes and exact decoded pixels,
rebuilds every artifact to check byte reproducibility, runs isolated measurements,
and exports JSON, CSV and Markdown reports. This is an evaluation of the current
tile/storage pipeline, not a new compression algorithm or a claim of general
performance superiority.

## Run

Use Linux, Python 3.11+ and the existing Pillow/SQLite dependencies. No model,
GPU inference, Rust compilation or extra benchmark package is required.

```sh
python3 -m crystal_geo evaluate-compression .cache/compression-run \
  --source-dir testdata/source_images --iterations 30 --warmup 5 --seed 0
```

The default builds a fresh WebP generation with the existing publisher prototype.
It then independently rebuilds it, the raw control and both MBTiles controls;
all twelve candidate files must have identical hashes on the second pass.
Allow about a minute for the two WebP encodes on this GB10, plus measurement time.

To evaluate an existing verified WebP generation without re-encoding it:

```sh
python3 -m crystal_geo evaluate-compression .cache/compression-smoke \
  --generation testdata/tiny_valid_generation --source-dir testdata/source_images \
  --iterations 3 --warmup 1 --seed 0
```

This mode records that WebP was reused; it does not claim an encoding benchmark
or new WebP encoder-determinism test. Fewer than 30 timed iterations prominently
labels the report as a smoke run. Supported limits are 1..1000 iterations,
0..100 warmups, and a 32-bit unsigned seed. Repeated runs need a new output path
or `--force`. Force replaces only a recognized evaluation with unchanged
candidate artifacts and no extra files; unrelated directories are preserved.
Use one writer per output directory. Reports are published from a sibling
temporary directory; this is not the production activation mechanism.

The source PNGs are checked against the pinned TX003 SHA-256 values before either
is decoded. With no `--source-dir`, missing cached PNGs use the existing verified
HTTPS downloader. Before/after dates, credits, reuse terms and source hashes are
retained. The dataset card is **CC-BY-NC-4.0**; fixtures, controls and results are
for **development and noncommercial demonstration**. All manifests remain
**unsigned and must never be accepted in production**.

## What is compared

| Candidate | Codec | Storage |
| --- | --- | --- |
| crystal-webp | Existing independent lossless WebP profile | Original Crystal development generation |
| crystal-raw | Uncompressed 256×256 RGBA | Evaluation-only container/index control |
| mbtiles-webp | Exact bytes copied from crystal-webp | Separate before/after MBTiles files |
| mbtiles-raw | Exact bytes copied from crystal-raw | Separate before/after MBTiles schema controls |

Every candidate covers the same 32 tiles, pixels, WGS84 bounds, dates and zoom 18.
WebP uses lossless RGBA, quality 100, method 6, exact alpha, with no resampling.
Raw tiles are exactly 262,144 bytes. The raw control has a separately marked
manifest and strict evaluation schema; normal Python/Rust readers continue to
reject it. No production codec policy was broadened.

MBTiles uses the standard `metadata` and four-column `tiles` interfaces, a unique
coordinate index, and TMS Y coordinates. Each acquisition needs its own file.
The [MBTiles 1.3 specification](https://github.com/mapbox/mbtiles-spec/blob/master/1.3/spec.md)
permits WebP. The raw control uses `application/octet-stream` with an explicit
RGBA metadata field; ordinary map viewers cannot decode that custom payload.
Unsigned manifest companions bind file hashes, source metadata and tile hashes.
They are evaluation additions, not part of the MBTiles specification.

Report sizes include all candidate files and manifest companions. SQLite bytes
include payloads for MBTiles, while Crystal's separate SQLite index does not.
Payload and metadata/container overhead totals are reported independently.
Those structural and metadata differences remain visible rather than attributing
them to a codec improvement.

## Measurement protocol

The seed selects a repeatable sequence of single-tile requests across both dates
and 2×2 viewports (four tiles from one date). The same sequences are used for
every candidate. Candidate/scenario execution order is also seeded.

For each of the four candidates, both workloads run under two connection policies:

- `reopen_each_request`: open databases/container, retrieve, verify, decode and
  close on every request. All these stages are included in end-to-end timing.
- `reuse_after_warmup`: reuse open connections after warmup. Initial open time is
  reported separately; per-request open/close stages are zero.

Each scenario runs in a fresh subprocess. Timings use `perf_counter_ns` and
include lookup/read, equal per-payload SHA-256 verification, decode, and applicable
connection costs. Lookup and BLOB reads cannot be cleanly separated for ordinary
SQLite queries, so both readers report a combined lookup/read phase. The trusted
evaluation catalogue supplies expected hashes equally to both formats; this is
not a timing of either production verifier or the Rust API. Exact source-pixel
hashes are checked after each timed request, outside its latency interval.

SQLite mmap is disabled. The requested cache budget is 2 MiB total per reader,
split across the two MBTiles connections. This is SQLite's requested cache budget,
not a hard process-memory cap. Package construction and validation warm OS caches.
**Reopened connections do not mean cold disk reads.** No privileged cache eviction
is performed. Reported payload bytes exclude SQLite page overhead; optional Linux
physical-read counters cover the worker including setup and warmup and may be zero.

Memory uses the current exec's Linux `/proc/self/status` `VmHWM` high-water mark,
including Python/native libraries and warmup. Baseline, peak and increase are
reported; raw `ru_maxrss` is retained as context because fork/exec history can
inflate it. These are coarse process counters, not isolated decoder allocations
or a process-memory ceiling. See the [Linux proc documentation](https://docs.kernel.org/filesystems/proc.html).

P50/P95/P99 use linear interpolation at `(n-1)*q`; raw samples are retained.
Thirty iterations provide descriptive values with limited tail coverage, not
statistical evidence that one format is generally faster. RSS and timings vary
with scheduling, caches and hardware. Only artifact bytes and request sequences
are expected to reproduce on the same source/toolchain/configuration.

## Outputs and validation

```text
OUTPUT/
  artifacts/                 Four candidates, twelve immutable files
  protocol.json              Sources' pixel/payload hashes and seeded requests
  report.json                Versions, sizes, correctness, all samples and metrics
  report.md                  Concise tables and interpretation limits
  samples.csv                Individual measured request phases
```

The runner verifies source/file/tile hashes, exact dimensions and decoded bytes,
raw index metadata/ranges, and MBTiles counts/coordinates/provenance before
measuring. A changed payload or decoded pixel fails the run. Repeated output
builds are compared byte-for-byte using complete artifact inventories.

Tests cover matched payloads, pixel equality, TMS rows, provenance corruption,
checked ranges, source hashes before decode, raw rejection by the normal verifier,
reproducibility, percentile math, seeded requests, stage accounting, CLI errors,
and safe output replacement. CI runs the complete suite and an explicitly labeled
three-iteration smoke evaluation, then uploads its four reports for 14 days.
There are no timing thresholds tied to a shared CI runner.

A full fresh-encode run from this ARM64 GB10 is preserved in
[`benchmarks/tx003-gb10/`](benchmarks/tx003-gb10/README.md). Binary candidate controls
remain in the ignored working cache and can be regenerated with the command above.
Recognition accuracy, lossy quality thresholds, energy/battery, mobile frame rate,
true cold disk behavior, multi-zoom/georeprojection and large-dataset scaling
remain unmeasured. No LLM inference is invoked by this evaluation.
