# TX003 GB10 measurement snapshot

This is the measured output of a fresh-encode evaluation on the local ARM64 GB10:

```sh
python3 -m crystal_geo evaluate-compression .cache/tx003-compression-evaluation \
  --source-dir testdata/source_images --iterations 30 --warmup 5 --seed 0
```

Two builds produced identical candidate bytes. There are 480 timed requests:
four candidates × two workloads × two connection policies × thirty iterations,
plus five warm-up requests per scenario. All 32 tiles per candidate decode to
the exact source RGBA pixels.

- `report.md`: readable sizes, latency, memory and interpretation limits.
- `report.json`: complete measurements, versions, file hashes and metadata.
- `protocol.json`: repeatable request order and expected tile/pixel hashes.
- `samples.csv`: individual measured phases.

These are report snapshots; candidate binaries are not checked in here.
Regenerate them in a new output directory with the command above. Artifact
bytes reproduce only with the same source/toolchain/configuration; timings and
RSS naturally vary. This is a small CPU/Python experiment, not evidence of
general compression or performance superiority. See [methodology](../../compression-evaluation.md).

Development and noncommercial demonstration only. Dataset card: CC-BY-NC-4.0.
All manifests are unsigned and must never be accepted in production.
