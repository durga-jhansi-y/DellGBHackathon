# Crystal Geo: Team Work Delegation

Three people, three tracks. Each track owns its folders so nobody edits the same
files. Details of *what* to build live in [BUILD_INSTRUCTIONS.md](../BUILD_INSTRUCTIONS.md);
this document says *who* builds it, *in what order*, and *how the pieces connect*.

| Track | Owner | Folders owned | One-line mission |
|---|---|---|---|
| **A. Data + AI** | Durga | `backend/`, `db/`, `ai/`, `schemas/findings.schema.json` | Tiger Data database, dataset loader, service API, and the Qwen AI features |
| **B. Frontend** | Greatness | `app/` | Flutter UI for Map, Datasets, Findings, Settings |
| **C. Compression + Continuous Recognition** | Shruthi | `crates/`, `recognition/`, `schemas/manifest.cddl`, `schemas/index.sql`, `testdata/`, `docs/format-specification.md`, `docs/threat-model.md` | Rust packaging, signing, verification, tile reads, benchmarks, and the continuous image-recognition pipeline |

Branches: each person works on their own branch .
Open PRs into `main`. Never push directly to `main`. Touch another track's
folder only through a PR that its owner reviews.

---

## 0. Shared contracts (agree on these first, before writing much code)

These are the seams between tracks. Whoever owns the file drafts it; the other
two review within the first working session. After that, changes need all three
to agree, because each change breaks someone else.

| Contract | File | Owner | Consumers |
|---|---|---|---|
| Repository interfaces: `DatasetRepository`, `TileRepository`, `RecognitionRepository`, `TrustRepository` | `app/lib/core/repositories/*.dart` | Greatness | D,S (they implement the real ones) |
| Rust API: `open_generation`, `query_tiles`, `read_verified_tile`, `decode_tile`, `close_generation` and typed errors (build doc 6.6, section 9) | `crates/crystal_core` public API | Shruthi | D (via the Dart bridge) |
| Service API (OpenAPI): datasets, generations, download manifests, findings, AI ask | `backend/openapi.yaml` | A | B, C |
| Findings JSON schema | `schemas/findings.schema.json` | A | B |
| Package manifest and index schema | `schemas/manifest.cddl`, `schemas/index.sql` | C | A (catalog mirrors it), B (display fields) |

Rules that apply to everyone:
- Every request and result carries `generation_id`.
- Use the typed error names from build doc section 9. Do not invent new ones silently.
- A verification error never falls back to showing unverified bytes.
- No secrets in git. Use `.env` (git-ignored); keep `.env.example` current.
- Pin dependency versions in lockfiles.

---

## Track A: Data + AI (Durga)

### A1. Tiger Data database  (start now; nothing blocks this)
Purpose: the **server/publisher-side catalog**. The phone still uses the read-only
SQLite index from the build doc; Tiger Data does not replace it.

1. Provision a Tiger Cloud service; enable `postgis`, `timescaledb`, and `pgvector`
   (if available) extensions. Put `DATABASE_URL` in `.env`.
2. Write `db/schema.sql` (migrations in `db/migrations/`):
   - `datasets`, `generations` (with `generation_sequence`, `manifest_sha256`, `signed_at`)
   - `images`: ~25 typed columns from `image_metadata.csv` (`image_id`, `pair_id`,
     `paired_image_id`, `acquisition_date`, `source_agency`, `sha256`, `image_bytes`,
     `image_path`, lat/lon, bounds, license, attribution) plus a `raw` JSONB column
     for the remaining ~250 columns
   - `images.footprint geometry(Polygon, 4326)` built from west/south/east/north,
     GiST index
   - `findings` (provenance columns per the build doc: generation, capture time,
     model id and version, settings, confidence, review status)
   - `weather_daily` as a hypertable, **only if** `observations.sqlite` is obtained
3. Write `db/load_hf.py`: download `image_metadata.csv` from
   `jennifer-fowler/UTSA-flood-images`, verify row count (1,204) and uniqueness of
   `image_id`, bulk `COPY` into Postgres, idempotent re-runs.
4. Write `db/verify.sql`: row counts, null checks on bounds, pair integrity
   (every `paired_image_id` resolves), duplicate `sha256` check.
5. Resolve the open question: ask the dataset owner (Jennifer) for
   `observations.sqlite`; the README says it exists but it is not in the repo.

**Done when:** a fresh checkout plus `.env` produces an identical populated
database in one command, and `verify.sql` passes.

### A2. Service API  (after A1)
A thin FastAPI (or similar) service so the app never talks to Postgres directly.
- `GET /datasets`, `GET /datasets/{id}/generations`
- `GET /generations/{id}/manifest` (returns the signed files C produces; the
  service does not sign anything)
- `GET /images?bbox=&date_from=&date_to=` (spatial query over footprints)
- `POST /findings`, `GET /findings?generation_id=`
- `POST /ai/ask`, `POST /ai/search` (see A3)
Publish `backend/openapi.yaml` early so B can build against mocks.

### A3. AI features (Qwen on GB10)
Qwen is served on the GB10 through an OpenAI-compatible endpoint; keys live in
`.env`. Build on the dataset's own notebooks (`01_Image_Question_Answering`,
`02_Text_to_Image_Retrieval`, `03_Before_After_Change_and_Weather`).

1. `ai/vlm_client.py`: one wrapper around the endpoint, with timeouts, retries,
   and no logging of keys or image bytes.
2. **Image Q&A:** user selects an area or image, asks a question, Qwen answers
   with the image attached.
3. **Before/after change detection:** use the 575 Harvey pairs
   (`paired_image_id`). Output structured JSON: changed regions, description,
   confidence.
4. **Text-to-image retrieval:** embeddings via the dataset's `image_embeddings.py`,
   stored in pgvector; query by text, return image ids ranked.
5. **Findings output:** every AI result is a row matching
   `findings.schema.json`: `generation_id`, image ids, capture times, `model_id`,
   `model_version`, prompt/settings hash, confidence or uncertainty, and
   `reviewed_by_human: false`. Wording must never present model output as
   confirmed fact.
6. **Evaluation:** `ai/eval/`: hold out labeled examples. The dataset's
   `flood_visible` labels are blank, so hand-label a small validation set
   (suggested labels: yes / no / uncertain). **Split by `pair_key` and
   `dependence_group`** so pairs and frames from one video never straddle
   train/test.

**Division with Shruthi (Track C):** A builds and serves the model features;
Shruthi's recognition pipeline (C5) decides *which* tiles to analyze, feeds them
verified pixels, and runs continuously. Both write the same findings schema.

**Honest scope note:** the build doc places recognition on the *phone* in Phase 5
and keeps it disabled until validated. For the hackathon, inference runs on the
GB10 server and the app calls it online. Label it experimental in the UI and
keep the provenance fields identical, so it can move on-device later.

**Done when:** the app can request Q&A or a before/after comparison for a
dataset image and get back a schema-valid finding with provenance, and
accuracy on the hand-labeled set is measured and written down.

---

## Track B: Frontend (Flutter)

Follows build doc section 5. B is **never blocked**, because everything starts
against fake repositories.

1. Create the Flutter app under `app/` with the feature folders from build doc
   section 4. Four destinations: Map, Datasets, Findings, Settings. Bottom nav on
   phones, rail on tablets.
2. Define the four repository interfaces (contract above) and a `fake_*`
   implementation of each driven by fixtures in `app/test/fixtures/`.
3. **First-run flow** (3 steps, build doc 5.2). No location permission until the
   user taps "Show my location."
4. **Map screen:** canvas with pan, pinch, double-tap zoom; dataset badge;
   scale bar; info button (source, license, acquisition time, resolution,
   freshness warning); layer control; persistent banner for failures. Missing
   tile: neutral checkerboard. Failed verification: distinct warning tile with
   icon and text, never color alone.
5. **Map state and tile planner:** immutable `MapViewState`, 16-32 ms debounce,
   one-tile margin, nearest-first priority, cancel stale requests, bounded
   decoded-tile cache keyed by `generation_id`.
6. **Datasets screen:** installed vs available, signature status, download and
   verify as *separate* progress phases, Update / Pause / Resume / Verify again /
   Make active / Delete with confirmation.
7. **Findings screen:** list plus map overlay; every finding shows
   confidence/uncertainty, imagery date, model version, review status. Add an
   "Ask about this area" box wired to `POST /ai/ask` (mock first, real once A2 lands).
8. Accessibility: dynamic text, screen reader labels, 48 dp targets, portrait and
   landscape.
9. Tests: widget tests for each screen state; integration tests for first run
   and offline map use; airplane-mode and low-storage cases once real storage exists.
10. Later (after C's API is stable): bind real repositories through
    `flutter_rust_bridge`; background downloads via WorkManager / iOS
    background sessions.

**Done when:** the full UI works end-to-end on fixtures, then swaps to real
repositories without screen changes.

---

## Track C: Compression + Security + Continuous Recognition (Shruthi)

Follows build doc sections 6-8 and phases 0-2, then 4, then 5 (recognition). C
owns the most long-lead work, so C gets the most review help from the others.

**Boundary with Track A (AI).** A owns the *model side*: the Qwen/GB10 client,
Q&A, retrieval, findings schema and storage, and evaluation. C owns the
*pipeline side*: deciding which tiles to look at, feeding them verified pixels,
running continuously, and handing results to A's findings store. C calls the
model only through A's `ai/vlm_client` (or its on-device successor), so the
model can be swapped without touching the pipeline.

### C1. Phase 0: format and threat model (first)
- `schemas/manifest.cddl`, `schemas/index.sql` (strict tables + R-tree),
  container header spec.
- `docs/format-specification.md`, `docs/threat-model.md`, `docs/key-management.md`,
  `docs/recovery-policy.md`.
- `testdata/tiny_valid_generation/` (signed) and `testdata/corrupted_generations/`
  (one per negative case in build doc 8.8).
- **Done when** two independent programs parse the valid fixture and every negative
  fixture fails for its expected typed error.

### C2. Phase 1: packaging CLI (`crystal_pack`)
- **Source adapter for this dataset.** The Hugging Face images are plain PNG/JPG
  with no embedded georeferencing; their bounds are in `image_metadata.csv`
  (WGS84 bounds, Web Mercator grid). Build an adapter that reads image + bounds
  from the CSV instead of requiring GeoTIFF. Keep a GeoTIFF reader as a later
  option.
- Tile into pyramids (256x256), independent compression, named profiles
  (`visual-webp-v1`, `analysis-webp-lossless-v1`, `raw-test-v1`), containers
  (512 MiB cap), SQLite index, canonical CBOR manifest, Ed25519 signing with a
  **dev key clearly marked and rejected by production builds**.
- Reuse each image's existing `sha256` as a provenance/input check.
- **Done when** packaging the same input twice (unencrypted) gives byte-identical
  output and every output passes the verifier.

### C3. Phase 2: trusted core (`crystal_core`)
- Staged import, verification in the exact order of build doc 8.4, atomic
  activation, generation leases, `query_tiles`, exact-range reads with checked
  arithmetic, hash check before decode, decode size limits, rollback protection.
- Fault-injection tests at every durable-write boundary.
- Expose the narrow API and typed errors that B binds to. Publish a stub crate
  with the final signatures **early** so B can code against it.

### C4. Phase 4: benchmark and harden (`crystal_bench`)
- Crystal vs MBTiles on identical pixels, bounds, zooms and codec. Report the
  metrics list in build doc 7.6; 30+ iterations after warm-up; save
  machine-readable results.
- Fuzz manifest parsing, index validation, offsets and decoders in CI.

**Done when** fault injection cannot make a partial generation active, and the
benchmark results are reproducible.

### C5. Phase 5: continuous image recognition (starts after C3 reads tiles)
Build `recognition/` (Rust, with a thin Python harness for experiments):
1. **Recognition planner:** separate from the map planner. Chooses resolution,
   overlap, and context margin per `RecognitionRequest` (build doc 5.6), and
   scans a region continuously: new tiles, newly activated generations, or a
   user-drawn area.
2. **Work queue and worker:** bounded queue with its own workers. Map requests
   always have higher priority and recognition must never block panning. Cancel
   or drop stale work when the generation changes.
3. **Verified input only:** tiles reach the model only through
   `read_verified_tile` and `decode_tile`. Use the `analysis-webp-lossless-v1`
   variant if lossy pixels hurt accuracy, and measure that difference.
4. **Before/after mode:** for Harvey pairs, request both images on the shared
   grid and pass them to A's change-detection prompt.
5. **Findings handoff:** write results in A's `findings.schema.json` format with
   generation id, tile keys, capture time, model id/version and processing
   settings. Deduplicate overlapping tile results.
6. **Model packaging:** if a model is ever shipped on-device, it is signed and
   verified like any other artifact. For the hackathon the model stays on the
   GB10 behind A's client.
7. **Measure:** accuracy by codec profile on A's hand-labeled set, throughput
   (tiles/sec), and the map frame-rate impact while recognition is running.

**Done when** recognition runs continuously over a region without dropping map
frame rate, every finding traces to exact verified inputs and a model version,
and accuracy is measured against the agreed validation set.

---

## Milestones and integration points

Sequence by dependency, not by calendar; rescale to your actual deadline.

| Milestone | A: Data + AI | B: Frontend | C: Compression | Integration check |
|---|---|---|---|---|
| **M0** contracts | Draft `openapi.yaml`, `findings.schema.json` | Draft repository interfaces | Draft `manifest.cddl`, `index.sql`, Rust API stub | All three review each other's contract |
| **M1** parallel build | Tiger DB + loader; API skeleton | App shell, fake repos, first-run, map on fixtures | Fixtures, `crystal_format`, pack CLI v0 | A's mock API and C's tiny fixture load in B's fakes |
| **M2** real data | Full API; Qwen Q&A and retrieval | Datasets + Findings screens, Ask box on real API | Verify + activate + tile reads | B renders a real packaged generation from C |
| **M3** demo path | Before/after findings; eval numbers; stable `vlm_client` for C | Rust bridge swapped in; offline mode | Benchmarks; fuzzing; recognition planner and worker v0 (C5) | **Demo:** install package -> airplane mode -> pan map -> ask Qwen about a pair |
| **M4** polish | Eval write-up | Accessibility pass, tests | Threat-model tests, docs | Release checklist (build doc section 12) |

**Cut line if time runs short:** keep M3's demo path. Drop encryption at rest,
background download integration, tablet layout, and GeoTIFF support first. Never
drop signature verification or the "never show unverified bytes" rule, since that
is the project's core claim.

## Working agreements
- Small PRs (under ~400 changed lines), one reviewer from another track.
- Each track keeps a `README` in its folder with run and test commands.
- CI (`.github/workflows/`): Rust fmt/clippy/test, Flutter analyze/test, Python
  lint; add the dependency/SBOM check before any release.
- Daily 10-minute sync: blocked on whom, and which contract changed.
- Claims in docs (security, speed) are claims until tests and benchmarks show
  them. Say "proposed" until then.

## Open questions to settle early
1. Where do image bytes live for the demo: Hugging Face directly, or object
   storage behind the service? (Affects A2 and B's downloader.)
2. Is `observations.sqlite` available? If not, drop the weather hypertable and
   the weather-context part of the before/after feature.
3. Does the GB10 stay reachable during the demo? If not, record a fallback set of
   precomputed findings.
4. Who holds the production signing key? (Not any laptop; dev key only until then.)
