PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE generation_metadata (
    dataset_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL UNIQUE,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    crs TEXT NOT NULL CHECK (crs = 'EPSG:4326'),
    grid_crs TEXT NOT NULL CHECK (grid_crs = 'EPSG:3857'),
    zoom INTEGER NOT NULL CHECK (zoom = 18)
) STRICT;

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    variant TEXT NOT NULL UNIQUE CHECK (variant IN ('before', 'after')),
    acquisition_date TEXT NOT NULL,
    source_image TEXT NOT NULL UNIQUE,
    source_sha256 BLOB NOT NULL CHECK (length(source_sha256) = 32),
    source_agency TEXT NOT NULL,
    attribution TEXT NOT NULL,
    reuse_terms TEXT NOT NULL
) STRICT;

CREATE TABLE tiles (
    tile_id INTEGER PRIMARY KEY,
    generation_id TEXT NOT NULL REFERENCES generation_metadata(generation_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    variant TEXT NOT NULL CHECK (variant IN ('before', 'after')),
    zoom INTEGER NOT NULL CHECK (zoom = 18),
    tile_x INTEGER NOT NULL CHECK (tile_x >= 0 AND tile_x < 262144),
    tile_y INTEGER NOT NULL CHECK (tile_y >= 0 AND tile_y < 262144),
    west REAL NOT NULL CHECK (west >= -180 AND west < 180),
    south REAL NOT NULL CHECK (south > -86 AND south < 86),
    east REAL NOT NULL CHECK (east > west AND east <= 180),
    north REAL NOT NULL CHECK (north > south AND north < 86),
    width_px INTEGER NOT NULL CHECK (width_px = 256),
    height_px INTEGER NOT NULL CHECK (height_px = 256),
    container_id TEXT NOT NULL CHECK (container_id = 'container-000001'),
    payload_offset INTEGER NOT NULL CHECK (payload_offset >= 256),
    payload_length INTEGER NOT NULL CHECK (payload_length > 0 AND payload_length <= 1048576),
    codec TEXT NOT NULL CHECK (codec = 'webp'),
    codec_parameters TEXT NOT NULL,
    stored_sha256 BLOB NOT NULL CHECK (length(stored_sha256) = 32),
    UNIQUE (zoom, tile_x, tile_y, variant)
) STRICT;

CREATE VIRTUAL TABLE tile_bounds USING rtree(tile_id, west, east, south, north);
