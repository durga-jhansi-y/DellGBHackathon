-- Tiger Data / Tiger Cloud (Postgres + PostGIS). Same logical model as schema_sqlite.sql.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS images (
  image_id          TEXT PRIMARY KEY,
  image_path        TEXT NOT NULL,
  pair_id           TEXT,
  paired_image_id   TEXT,
  pair_key          TEXT,
  image_role        TEXT,
  collection        TEXT,
  event_name        TEXT,
  acquisition_date  DATE,
  locality          TEXT,
  state             TEXT,
  latitude          DOUBLE PRECISION,
  longitude         DOUBLE PRECISION,
  west              DOUBLE PRECISION,
  south             DOUBLE PRECISION,
  east              DOUBLE PRECISION,
  north             DOUBLE PRECISION,
  footprint         geometry(Polygon, 4326),
  source_agency     TEXT,
  platform          TEXT,
  width_px          INTEGER,
  height_px         INTEGER,
  native_resolution_min_m DOUBLE PRECISION,
  license_url       TEXT,
  attribution       TEXT,
  sha256            CHAR(64) NOT NULL,
  image_bytes       BIGINT,
  dependence_group  TEXT,
  candidate_condition TEXT,
  flood_visible     TEXT,
  raw               JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS images_pair      ON images (paired_image_id);
CREATE INDEX IF NOT EXISTS images_date      ON images (acquisition_date);
CREATE INDEX IF NOT EXISTS images_footprint ON images USING GIST (footprint);

CREATE TABLE IF NOT EXISTS findings (
  finding_id        BIGSERIAL PRIMARY KEY,
  generation_id     TEXT NOT NULL DEFAULT 'dev',
  kind              TEXT NOT NULL,
  image_id          TEXT NOT NULL REFERENCES images(image_id),
  paired_image_id   TEXT REFERENCES images(image_id),
  question          TEXT,
  answer            TEXT NOT NULL,
  confidence        DOUBLE PRECISION,
  imagery_time      DATE,
  model_id          TEXT NOT NULL,
  model_version     TEXT,
  settings_json     JSONB,
  reviewed_by_human BOOLEAN NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS findings_image ON findings (image_id);
