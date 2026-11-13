# Rust verification and tile retrieval

`crates/crystal_core` is the read-only Rust port of the existing unsigned TX003
development format. It shares the authoritative SQL schema and provenance file
with Python, reads the existing fixture unchanged, and requires explicit
development opt-in. **No unsigned package may be accepted in production.**
The dataset card is CC-BY-NC-4.0; this remains a development and noncommercial
demonstration. See [source attribution and reuse terms](fixture-usage.md).

## Build and check

Rust 1.88.0, rustfmt and Clippy are pinned in `rust-toolchain.toml`. Dependency
versions and registry checksums are pinned in `Cargo.lock`. Install Python test
dependencies with `python3 -m pip install -e '.[test]'`. Build from the
workspace root with a C compiler for the bundled SQLite dependency:

```sh
cargo build --locked --workspace
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace
CRYSTAL_RUST_BINARY="$PWD/target/debug/crystal-core" python3 -m unittest discover -s tests -v
```

On this GB10, the toolchain was installed under the ignored repository cache
without changing shell profiles. To use that installation in another shell:

```sh
export CARGO_HOME="$PWD/.cache/cargo"
export RUSTUP_HOME="$PWD/.cache/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
```

Dependencies need network access on first installation; verification, retrieval
and all tests use local data. Python-only test runs skip the four parity tests
unless `CRYSTAL_RUST_BINARY` is set. CI explicitly builds and enables them.

## CLI

```sh
target/debug/crystal-core verify testdata/tiny_valid_generation --allow-unsigned-development
target/debug/crystal-core inspect testdata/tiny_valid_generation --allow-unsigned-development
target/debug/crystal-core read-tile testdata/tiny_valid_generation before 18 61440 108284 /tmp/tile.webp --allow-unsigned-development
target/debug/crystal-core decode-tile testdata/tiny_valid_generation after 18 61440 108284 /tmp/tile.rgba --allow-unsigned-development
```

`read-tile` exports the exact stored WebP payload. `decode-tile` exports 256×256×4
RGBA bytes. Both exclusively create new output files and never overwrite an
existing file. An interrupted output write can leave a partial export; package
and active-generation files are not modified. `inspect` prints all tile keys,
bounds, source/date provenance, and compressed/decoded SHA-256 values as JSON.
Errors print `ERROR: ...` and exit with status 1. Omitting the development flag
rejects the package; no signed/production package parser is implemented.

## Rust API

```rust
use crystal_core::{Generation, TileKey, TrustPolicy, Variant};

fn read_example() -> crystal_core::Result<()> {
    let generation = Generation::open(
        "testdata/tiny_valid_generation", TrustPolicy::DevelopmentUnsigned,
    )?;
    let key = TileKey { variant: Variant::Before, zoom: 18, x: 61440, y: 108284 };
    let verified = generation.read_verified_tile(generation.generation_id(), key)?;
    let exact_compressed_bytes = verified.bytes();
    let pixels = verified.decode()?;
    assert!(!exact_compressed_bytes.is_empty());
    assert_eq!(pixels.rgba.len(), 256 * 256 * 4);
    Ok(())
}
```

Opening a generation bounds and validates canonical JSON, identifiers,
provenance, paths, exact sizes, index/container hashes, header identity/checksum,
the strict SQLite schema, integrity and foreign keys, metadata, tile coordinates,
WGS84 bounds and R-tree entries, codec parameters, contiguous nonoverlapping
payload coverage, and every tile's stored hash and bounded decode. Canonical
round-trip comparison also rejects duplicate JSON keys. The default Serde
nesting limit remains enabled.

The reader deserializes the exact hashed index slice into a read-only in-memory
SQLite database through the binding's safe API. It sets SQLite length/attachment
limits and an instruction budget, then captures the 32 tile descriptors and
drops the database. It retains the opened container descriptor. Readers do not
switch generation if a directory or active pointer changes; Rust RAII closes
the descriptor when the handle is dropped.

Each request must name the pinned generation. Checked u64 arithmetic validates
the payload range before converting its length to an allocation size. Exact
positional reads check container size/header and payload SHA-256 before bytes
enter `VerifiedCompressedTile`. Its fields and constructor are private; callers
cannot construct it from arbitrary bytes. `decode` accepts this verified type,
requires lossless WebP and exactly 256×256 pixels, and configures the decoder's
allocation budget to 8 MiB. Dimension limits are strict; the allocation limit is
a decoder budget, not a measured process-memory ceiling. The application crate
forbids unsafe Rust; dependencies include native SQLite and their own internals.

## CI and tests

`.github/workflows/ci.yml` runs on pushes and pull requests with read-only
repository permissions. It checks formatting and Clippy, runs Rust tests, runs
the full Python suite with Rust parity enabled, and verifies the fixture in both
languages. Local validation runs on the ARM64 GB10; the configured GitHub runner
is Ubuntu 24.04 x86-64. The workflow itself will run after these changes are pushed.

Parity tests compare all 32 stored payload hashes, decoded source-pixel hashes,
tile keys, bounds, filenames and dates. They exercise the existing Python
corruption cases against both verifiers, including refreshed outer hashes that
force deeper structural checks. Rust tests additionally cover checked-arithmetic
overflow, decoder dimensions, default unsigned rejection, post-open corruption,
header changes, truncation, and index/container pinning.

This port does not yet implement Rust installation/activation, active-pointer
reading, garbage collection, Flutter/FFI bindings, production signatures,
encryption or a general-purpose codec interface. The separate
[experimental recognition queue](recognition.md) now uses a bounded JSON-lines
`serve` process to retain a Rust reader per job. Python remains
the installer; Rust can open its installed `generations/<id>/package` directory.
The accepted profile and size limits remain deliberately fixed to TX003. Both
readers assume a trusted local directory; they are not hardened against an
adversary who can concurrently rewrite the unsigned package and its parent
directories. Full open-time verification is not optimized for large datasets.
