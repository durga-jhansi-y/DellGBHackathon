-- Local development database (SQLite). Same logical model as schema_postgres.sql.
-- Footprint is stored as west/south/east/north (WGS84) instead of a PostGIS polygon.

CREATE TABLE IF NOT EXISTS images (
  image_id          TEXT PRIMARY KEY,
  image_path        TEXT NOT NULL,
  pair_id           TEXT,
  paired_image_id   TEXT,
  pair_key          TEXT,
  image_role        TEXT,
  collection        TEXT,
  event_name        TEXT,
  acquisition_date  TEXT,
  locality          TEXT,
  state             TEXT,
  latitude          REAL,
  longitude         REAL,
  west              REAL,
  south             REAL,
  east              REAL,
  north             REAL,
  source_agency     TEXT,
  platform          TEXT,
  width_px          INTEGER,
  height_px         INTEGER,
  native_resolution_min_m REAL,
  license_url       TEXT,
  attribution       TEXT,
  sha256            TEXT NOT NULL CHECK (length(sha256) = 64),
  image_bytes       INTEGER,
  dependence_group  TEXT,
  candidate_condition TEXT,
  flood_visible     TEXT,                -- blank in source; for our own annotations
  raw_json          TEXT NOT NULL         -- all remaining source columns
);

CREATE INDEX IF NOT EXISTS images_pair   ON images (paired_image_id);
CREATE INDEX IF NOT EXISTS images_date   ON images (acquisition_date);
CREATE INDEX IF NOT EXISTS images_bbox   ON images (west, east, south, north);
CREATE INDEX IF NOT EXISTS images_source ON images (source_agency);

-- AI results. Every finding keeps its provenance (build doc section 5.6).
CREATE TABLE IF NOT EXISTS findings (
  finding_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  generation_id     TEXT NOT NULL DEFAULT 'dev',
  kind              TEXT NOT NULL,        -- qa | change | retrieval
  image_id          TEXT NOT NULL REFERENCES images(image_id),
  paired_image_id   TEXT REFERENCES images(image_id),
  question          TEXT,
  answer            TEXT NOT NULL,
  confidence        REAL,                 -- model-reported; null if unknown
  imagery_time      TEXT,                 -- capture date of the imagery used
  model_id          TEXT NOT NULL,
  model_version     TEXT,
  settings_json     TEXT,
  reviewed_by_human INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS findings_image ON findings (image_id);
