# Crystal Geo Architecture

Proposed architecture for secure offline imagery retrieval, map rendering,
and local recognition. Capabilities require implementation and validation.

The first Python development slice now builds and verifies the TX003 offline
before/after fixture. See [fixture usage and limitations](docs/fixture-usage.md).
It uses an **unsigned development manifest that must never be accepted in
production**, and is for development and noncommercial demonstration only.
The remaining architecture below is still proposed.

```mermaid
flowchart TD
    Sources["Imagery sources"]

    subgraph Data["1. Data Layer"]
        Download["Incremental download<br/>into staging area"]
        Validate["Validate metadata, files,<br/>and geographic alignment"]
        BuildIndex["Build geographic tile index"]
        Publish["Atomically publish<br/>completed generation"]
        PlanMap["Map tile-request planner"]
        PlanAI["Recognition tile-request planner<br/>Resolution, overlap, and context"]
        Recognize["Local recognition worker"]
        Findings["Results with source, capture time,<br/>model version, and processing settings"]
    end

    subgraph Compression["2. Crystal Compression Layer"]
        Tile["Generate tiles and<br/>resolution levels"]
        Encode["Independently compress tiles"]
        Select["Locate selected payloads<br/>using authenticated index"]
        Decode["Decode verified tiles"]
    end

    subgraph Security["3. Crystal Sentinel Layer"]
        Seal["Create manifest binding<br/>index and payload hashes<br/>Sign using trusted publisher key"]
        CheckUpdate["Verify staged generation<br/>Completeness, authenticity, integrity"]
        CheckIndex["Authenticate active index<br/>Check generation policy"]
        CheckTile["Verify selected payloads"]
        Reject["Reject failed verification"]
    end

    Staging[("Staged generation")]
    Active[("Active offline generation")]
    Trust[("Separate trusted state<br/>Trust anchors and accepted generation")]

    subgraph UI["4. UI Layer"]
        Map["Offline map<br/>Pan and zoom"]
        Task["Recognition requests"]
        Display["Imagery and findings<br/>Sources, age, and uncertainty"]
        Controls["Sync and storage controls"]
    end

    Sources --> Download --> Validate --> Tile --> Encode
    Encode --> BuildIndex --> Seal --> Staging
    Staging --> CheckUpdate
    Trust --> CheckUpdate
    CheckUpdate -->|"Pass"| Publish --> Active
    CheckUpdate -->|"Fail"| Reject

    Active --> CheckIndex
    Trust --> CheckIndex
    CheckIndex -->|"Pass: authenticated index"| Select
    CheckIndex -->|"Fail"| Reject

    Map --> PlanMap --> Select
    Task --> PlanAI --> Select
    Select -->|"Read selected bytes from active generation"| CheckTile
    CheckTile -->|"Pass"| Decode
    CheckTile -->|"Fail"| Reject

    Decode -->|"Display tiles"| Display
    Decode -->|"Model inputs"| Recognize --> Findings --> Display
    Controls --> Download
```

## Geographic tile index

Each tile entry records:

- Coordinate reference system, geographic bounds, and pixel-to-map transform.
- Tile coordinates, dimensions, resolution level, and boundary padding.
- Acquisition time and download time.
- Source, dataset version, and generation identifier.
- Container identifier, compressed payload offset, and length.
- Codec, decoding parameters, and expected payload hash.

The authenticated manifest binds the index and payload hashes to the
dataset generation.

## Offline update guarantees

1. Download into a separate staging area.
2. Validate source data and build the candidate generation.
3. Verify its authenticated manifest, index, and required payloads.
4. Publish the generation atomically.
5. Keep the previous generation available until publication succeeds.

Interrupted updates must not change the active dataset. Readers remain
pinned to one generation so they cannot mix old indexes with new payloads.

Signing authenticates the publisher; it does not prove the imagery is true
or current. Rollback protection requires separately protected freshness
state and a defined recovery policy.

## Independent workloads

Map rendering and recognition use separate request planners and work queues.
Recognition can request higher resolution and neighboring tiles without
blocking navigation. Both share the authenticated storage and retrieval path.

## Crystal Geo v0.1

First implement tiled storage, an authenticated geographic index, transactional
publication, and a minimal offline viewer.

Benchmark against an established tiled format using identical imagery:
storage size, bytes read, retrieval latency, peak memory, and verification
overhead.

Add local recognition after validating the storage and retrieval path.

## Implementation guide

See [Crystal Geo Mobile App: Detailed Build Instructions](BUILD_INSTRUCTIONS.md)
for the proposed mobile UI, data, compression, security, testing, and delivery
plan.
