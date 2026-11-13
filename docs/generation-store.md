# Development generation store

Goal 2 adds local staged import, active-generation publication, restart recovery,
and verified tile retrieval to the TX003 Python prototype. The [fixture's source
credits and CC-BY-NC-4.0 dataset-card terms](fixture-usage.md) still apply. This is
**unsigned development and noncommercial demonstration data**. Every store
command requires `--allow-unsigned-development`; it must never be used in production.
The store has no production verification mode or signature implementation.

## Offline walkthrough

Run from the repository root with the Goal 1 dependencies. `.cache/` is ignored
by Git. The existing store parent directory must exist; store subdirectories are
created with private permissions.

```sh
mkdir -p .cache/goal2
python3 -m crystal_geo install .cache/goal2/store testdata/tiny_valid_generation --allow-unsigned-development
python3 -m crystal_geo status .cache/goal2/store --allow-unsigned-development
python3 -m crystal_geo read-tile .cache/goal2/store --variant before --zoom 18 --x 61440 --y 108284 --output .cache/goal2/before.png --allow-unsigned-development

python3 -m crystal_geo build-fixture .cache/goal2/generation-b --generation-id tx003-dev-v2 --source-dir testdata/source_images
python3 -m crystal_geo install .cache/goal2/store .cache/goal2/generation-b --allow-unsigned-development
python3 -m crystal_geo read-tile .cache/goal2/store --variant after --zoom 18 --x 61440 --y 108284 --output .cache/goal2/after.png --allow-unsigned-development
python3 -m crystal_geo recover .cache/goal2/store --allow-unsigned-development
python3 -m unittest discover -s tests -v
```

All commands above work without networking. PNG export uses exclusive creation:
choose a new output path on repeated runs. `read-tile --generation-id ID` can
require the caller's expected active generation. Output metadata includes
generation ID, imagery variant, source filename, acquisition date and bounds.
All successful store commands also print the unsigned development warning.

## Installation contract

```text
store/
  .writer.lock
  datasets/<dataset UUID>/
    active
    generations/<generation ID>/
      READY
      package/
        manifest.json
        index.sqlite
        containers/container-000001.bin
    staging/<random ID>.partial/
```

Installation copies only the three bounded, known package files into a new
staging directory; manifest paths never direct copying. Files become read-only,
are flushed and synced, and their containing directories are synced. The shared
fixture verifier then checks the complete staged package, including all tile
hashes and bounded decodes. A `READY` record binds its generation ID to its
manifest SHA-256. The marker and staging directory are synced before renaming
the generation into `generations/`; both rename parents are then synced.

Only then does the installer write and sync a temporary checksummed `active`
record, atomically replace `active`, and sync its parent directory. Readers see
one complete pointer record. Failed validation never changes the active pointer.
The pointer and READY checksums detect accidental corruption; they are not
publisher authentication. `status` validates the pointer; opening a reader also
validates READY, the manifest binding, and the complete installed package.

Writers serialize through a POSIX file lock, released by the operating system
on process exit. Installing an identical generation is idempotent. Reusing an
installed generation ID for different manifest content is rejected. Every
published generation is retained, including older generations and complete
generations left behind before pointer replacement. No installed generation is
overwritten or deleted by this API.

`recover` serializes with writers and removes only recognized partial staging
directories and temporary pointer files. It rejects symlinks and unexpected
staging entries. It never selects an orphan generation or repairs a damaged
active pointer by guessing. Retrying an import can reuse an identical verified
published generation. If no pointer was committed on the first install, the
store stays inactive until an explicit successful import.

An `ActivationUncertain` error means the pointer was replaced but its directory
sync failed. Inspect status and open the active generation before retrying;
the new generation may already be active. The previous generation is retained.

## Reader contract

```python
from crystal_geo.store import GenerationStore

store = GenerationStore(".cache/goal2/store", allow_unsigned_development=True)
with store.open_active() as reader:
    tile = reader.read_tile(
        generation_id=reader.generation_id,
        variant="before", zoom=18, x=61440, y=108284,
    )
    assert len(tile.rgba) == 256 * 256 * 4
```

A reader validates the active package, snapshots the exact hashed index bytes
into an in-memory SQLite database, captures tile descriptors, and opens and
authenticates its generation's container descriptor. Each request must name
that same generation. Activation of B does not change a reader already pinned
to A. The in-memory descriptors cannot switch to a newer on-disk index.

Reads use `pread` with checked ranges, exact byte counts, a size/header check,
and stored-payload SHA-256 verification before the shared bounded WebP decoder.
Returned pixels and metadata always include the pinned generation ID. There is
no cache shared across generations. Closing a reader waits for any current
read and releases its descriptor; context managers provide deterministic cleanup.
All generations remain retained, so readers require no garbage-collection leases
or cross-process reader counters in this version.

Errors include `UnsignedGenerationRejected`, `GenerationUnavailable`,
`GenerationInvalid`, `GenerationConflict`, `GenerationChanged`, `IndexInvalid`,
`TileNotFound`, `PayloadOutOfBounds`, `PayloadHashMismatch`, `LeaseClosed`,
`StateInvalid`, `UnsafePath`, `StorageFull`, `StorageError`, and
`ActivationUncertain`. CLI errors exit with status 1 and return no tile pixels.

## Validation and remaining limits

Offline tests cover exact source pixels, before/after provenance, restart,
reader pinning across in-process and separate-process updates, concurrent
writers, idempotence, generation collisions, damaged updates, pointer/READY
corruption, unsafe paths, mixed generations, out-of-bounds payloads, disk-full
errors, post-open corruption and truncation, and default rejection of unsigned
packages. Subprocesses abruptly exit after every file-sync, directory-sync,
and rename checkpoint for both first installation and A-to-B updates. Restart
must expose A or complete B (or no generation before first activation); cleanup
must never promote incomplete data. These are process-crash tests, not physical
power-loss tests or a guarantee about storage hardware.

This prototype targets a trusted local directory on Linux/POSIX with `flock`,
`pread`, directory `fsync`, atomic same-filesystem rename, and SQLite
serialization support. It does not defend against a concurrent malicious writer
who can replace parent directories or rewrite the unsigned records. Keep store
directories private and use the API for updates. A read/open performs full
package verification; this has not been benchmarked for large datasets. Retaining
every generation consumes disk indefinitely. Production signatures, protected
rollback state, garbage collection, resumed network downloads, mobile platform
durability testing, and the Rust installer port remain future work. The separate
[experimental recognition queue](recognition.md) now uses the read-only Rust core.
Flutter and encryption remain out of scope.
