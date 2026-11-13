# Experimental recognition integration

The recognition queue connects the existing verified Rust reader to a model
backend. This session adds an adapter for the GB10's local OpenAI-compatible
Qwen server; it does not train or replace your recognition model. Results are
experimental, unreviewed text observations with **uncalibrated uncertainty**.
This does not establish recognition accuracy. The imagery and findings remain
for development and noncommercial demonstration; the dataset card is
CC-BY-NC-4.0. Unsigned packages must never be accepted in production.

## Run one local job

Build `crystal-core` as described in [Rust setup](rust-core.md), install the
fixture into a [generation store](generation-store.md), and start the local VLM.
The commands below use the first 256×256 tile from each acquisition date:

```sh
python3 -m pip install -e '.[test]'
export CRYSTAL_RUST_BINARY="$PWD/target/debug/crystal-core"
python3 -m crystal_geo recognize .cache/goal2/store .cache/recognition \
  --region -95.625 29.844217466091493 -95.62362670898438 29.84540862642843 \
  --variant both --model-id Qwen/Qwen3-VL-8B-Instruct \
  --model-version local-unversioned --max-tokens 256 \
  --question 'Compare the before and after tiles. Describe visible changes and uncertainty.' \
  --allow-unsigned-development
python3 -m crystal_geo recognize-status .cache/recognition REQUEST_ID
```

The local adapter defaults to `http://127.0.0.1:8000/v1`. It accepts only loopback
endpoints, disables proxies and redirects, limits response size, and uses normal
TLS validation for HTTPS. If authentication is needed, set `CRYSTAL_LLM_API_KEY`;
credentials are never put in findings. It does not load credentials or endpoint
settings from the teaching repository. No model server is needed for automated
tests, which use deterministic backends.

`--model-version` is an explicit deployment/checkpoint label supplied by you.
`local-unversioned` honestly records that this demonstration's checkpoint has
not been cryptographically identified. The API-reported model ID must match the
requested ID. Model weights and server-side preprocessing are not verified by
this integration; both limitations are recorded.

## Connect your continuous recognition loop

```python
from crystal_geo.recognition import LocalVLMBackend, RecognitionQueue
from crystal_geo.recognition_types import RecognitionRequest
from crystal_geo.store import GenerationStore

store = GenerationStore(".cache/goal2/store", allow_unsigned_development=True)
region = (-95.625, 29.844217466091493, -95.62362670898438, 29.84540862642843)
with RecognitionQueue(store, ".cache/my-findings", LocalVLMBackend(),
                      capacity=2, workers=1) as queue:
    request_id = queue.submit(RecognitionRequest(
        region, "both", "Qwen/Qwen3-VL-8B-Instruct", "my-deployment-version",
    ))
    record = queue.wait(request_id, timeout=60)
```

Your loop can submit successive regions and inspect `get`, `wait`, or `cancel`.
`QueueFull` is explicit backpressure; retry or choose what to cancel in your
caller. There is no automatic polling, frame capture, or resubmission. A custom
backend implements `infer(request, images, cancellation) -> ModelResult` and
returns text, the actual model ID, and a dictionary of reproducibility settings.
Use bounded I/O/deadlines, honor cancellation, and exclude credentials from that
dictionary. Settings are recorded as reported by that backend; an empty dictionary
means it did not report them. Calibration or structured detection outputs need a
separately validated contract.

Admission snapshots the active pointer and READY identity, then starts one Rust
`serve` process that opens and verifies that exact generation. Its handshake
must match the generation ID and manifest SHA-256. Pending as well as running
jobs retain their own readers, so activation of B cannot switch a job pinned to A.
Each selected payload is read through Rust and checked again before any input
is passed to the model. One inference call receives the complete verified batch;
a failed tile prevents that job's inference entirely.

Requests use WGS84 west/south/east/north bounds, before/after/both, and native zoom
18. The entire region must be installed. At most four intersecting tiles are
accepted, matching this adapter's small input budget. Full tiles are provided
without cropping, resizing, or re-encoding; the requested region and each tile's
date/bounds are included as text labels. These are qualitative tile observations,
not geolocated object detections. Choose smaller regions rather than silently
dropping tiles. Other codecs, resolutions and explicit context margins are future work.

## Scheduling, cancellation and recovery

The default is one worker and two pending/reserved admission slots; configuration
is capped at two workers and sixteen pending slots. In-memory finished-job
history is bounded (default 64, maximum 256); older records remain readable from
disk. Map retrieval uses independent readers and takes no recognition queue lock.
This preserves storage access, not a measured GPU/frame-rate guarantee.

Queued cancellation closes the pinned reader without calling the model. Running
cancellation finishes an in-progress integrity read, skips later work, and
discards a model response if the HTTP request was already sent. It does not
abort server GPU inference. The local adapter has a configurable socket timeout
(default 30 seconds, maximum 60). Custom backends must bound their own work.
Shutdown requests cancellation and waits; `ShutdownTimeout` means a backend has
not returned and the caller should retry closing after its deadline.

One scheduler owns each findings directory through a POSIX file lock. JSON
records are private, bounded to 64 KiB, written through a synced temporary file
and atomic replacement, and described by `schemas/findings.schema.json`. On a
restart, queued/running records become `interrupted`; inference is never
automatically replayed. Completed records remain available. Storage failures
mark a job failed in memory and stop new submissions if its final state cannot
be saved. Fix storage and restart before retrying. Records and imagery generations
are retained indefinitely; disk retention/quota policy remains future work.

Findings include generation/manifest hash, every input payload hash, tile
coordinates/bounds, filenames and capture dates, requested region/resolution,
model ID/version label, request parameters, backend-reported settings, timestamps,
review status and uncertainty. No numerical confidence is invented. These JSON
records are unsigned; they assume a private local directory and are not an audit
signature or a defense against an adversarial filesystem writer.

## Validation

```sh
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace
CRYSTAL_RUST_BINARY="$PWD/target/debug/crystal-core" python3 -m unittest discover -s tests -v
```

CI installs the schema-test dependency and enables Rust integration tests. Tests
cover A-to-B activation during recognition, independent map reads, bounded work
and history, queued/running cancellation, input corruption before and after
pinning (including the second tile of a batch), provenance/schema conformance,
restart without replay, failed persistence, malformed/oversized model responses,
loopback-only delivery and bounded reader protocol input/timeouts. A live local
TX003 job was also exercised; its output is a demonstration, not an accuracy
validation dataset. Flutter, model training, encryption and production signing
are unchanged.
