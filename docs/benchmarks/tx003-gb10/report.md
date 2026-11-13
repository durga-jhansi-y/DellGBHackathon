# TX003 compression evaluation

Development and noncommercial demonstration. Dataset card: CC-BY-NC-4.0.
UNSIGNED DEVELOPMENT ONLY; MUST NEVER BE ACCEPTED IN PRODUCTION.

Scope: exploratory; 30 timed iterations after 5 warm-up iterations per candidate/workload/cache policy.

| Candidate | Payload bytes | SQLite bytes | Manifest bytes | Total bytes | Exact source pixels |
| --- | ---: | ---: | ---: | ---: | --- |
| crystal-webp | 1756656 | 65536 | 3286 | 1825734 | 32/32 |
| crystal-raw | 8388608 | 61440 | 3250 | 8453554 | 32/32 |
| mbtiles-webp | 1756656 | 1826816 | 6777 | 1833593 | 32/32 |
| mbtiles-raw | 8388608 | 8445952 | 6741 | 8452693 | 32/32 |

WebP payload bytes / raw RGBA bytes on TX003: 0.209410.

| Candidate | Workload | Connection policy | P50 total ms | P95 ms | P99 ms | Peak worker RSS MiB |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| crystal-raw | single_tile | reopen_each_request | 0.2385 | 0.2488 | 0.2540 | 26.41 |
| crystal-raw | single_tile | reuse_after_warmup | 0.1224 | 0.1264 | 0.1281 | 26.39 |
| crystal-raw | viewport_2x2 | reopen_each_request | 0.6512 | 0.7132 | 0.7296 | 27.79 |
| crystal-raw | viewport_2x2 | reuse_after_warmup | 0.5227 | 0.5817 | 0.5888 | 27.73 |
| crystal-webp | single_tile | reopen_each_request | 1.3539 | 1.3948 | 1.4034 | 31.21 |
| crystal-webp | single_tile | reuse_after_warmup | 1.1944 | 1.2289 | 1.2455 | 31.25 |
| crystal-webp | viewport_2x2 | reopen_each_request | 4.9098 | 5.0808 | 5.1261 | 33.08 |
| crystal-webp | viewport_2x2 | reuse_after_warmup | 4.7598 | 4.8936 | 4.9427 | 33.07 |
| mbtiles-raw | single_tile | reopen_each_request | 0.3073 | 0.3235 | 0.3281 | 26.72 |
| mbtiles-raw | single_tile | reuse_after_warmup | 0.1654 | 0.1729 | 0.1780 | 26.72 |
| mbtiles-raw | viewport_2x2 | reopen_each_request | 0.7634 | 0.8163 | 0.8275 | 28.43 |
| mbtiles-raw | viewport_2x2 | reuse_after_warmup | 0.5873 | 0.6499 | 0.6636 | 28.25 |
| mbtiles-webp | single_tile | reopen_each_request | 1.3715 | 1.4243 | 1.4550 | 31.17 |
| mbtiles-webp | single_tile | reuse_after_warmup | 1.1957 | 1.2206 | 1.2587 | 31.36 |
| mbtiles-webp | viewport_2x2 | reopen_each_request | 4.9875 | 5.1946 | 5.2175 | 33.00 |
| mbtiles-webp | viewport_2x2 | reuse_after_warmup | 4.8280 | 5.0329 | 5.3904 | 33.29 |

## Interpretation

- TX003 only: 32 tiles at one zoom; measurements do not establish general compression gains.
- MBTiles stores exactly the same payloads as its Crystal counterpart; each date has a separate file and TMS rows.
- Totals include unsigned manifest companions. MBTiles SQLite bytes include payloads; Crystal index bytes do not.
- Raw RGBA is an evaluation-only control, not an installable Crystal codec or portable MBTiles image format.
- OS caches were not evicted. Reopening resets connections, not the operating-system page cache.
- Lookup and payload reads are combined; hash verification is applied equally from a trusted evaluation catalogue.
- End-to-end includes connection open/close for reopen policy. Reused-connection initial open is reported separately.
- Linux VmHWM is the current exec memory-map peak, including Python/native libraries and warmup; it is a coarse counter, not decoder-exclusive allocation. Raw ru_maxrss is retained as context.
- Physical read-byte counters cover the worker workload and may be zero for cached I/O; payload bytes exclude SQLite page overhead.
- Thirty samples yield descriptive P50/P95/P99 values, not statistical proof of performance superiority.
- Recognition accuracy, battery, GPU, Flutter frame rate and true cold-start disk behavior are not measured.

Raw samples and stage timings are in `samples.csv` and `report.json`. Inputs, ordering and configuration are in `protocol.json`. Artifact byte reproducibility: True.
