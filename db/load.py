"""Load the UTSA flood image metadata into a database.

Usage (repo root):
  python db/load.py                    # local SQLite -> data/crystal.sqlite (default)
  python db/load.py --target postgres  # Tiger Cloud, using DATABASE_URL from .env

Idempotent: re-running replaces rows. Downloads image_metadata.csv from the
public Hugging Face dataset if data/image_metadata.csv is absent.
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "image_metadata.csv"
SQLITE_DB = ROOT / "data" / "crystal.sqlite"
HF = "https://huggingface.co/datasets/jennifer-fowler/UTSA-flood-images/resolve/main/image_metadata.csv"

CORE = [
    "image_id", "image_path", "pair_id", "paired_image_id", "pair_key", "image_role",
    "collection", "event_name", "acquisition_date", "locality", "state", "latitude",
    "longitude", "west", "south", "east", "north", "source_agency", "platform",
    "width_px", "height_px", "native_resolution_min_m", "license_url", "attribution",
    "sha256", "image_bytes", "dependence_group", "candidate_condition", "flood_visible",
]


def clean(v):
    """NaN/blank -> None; numpy scalars -> Python."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v.item() if hasattr(v, "item") else v


def read_rows():
    if not CSV.exists():
        CSV.parent.mkdir(exist_ok=True)
        print("downloading metadata CSV...")
        urllib.request.urlretrieve(HF, CSV)
    df = pd.read_csv(CSV, low_memory=False, dtype={"flood_visible": "object"})
    if len(df) != 1204 or not df["image_id"].is_unique:
        sys.exit(f"unexpected CSV: {len(df)} rows, unique ids={df['image_id'].is_unique}")
    rows = []
    for rec in df.to_dict("records"):
        rec = {k: clean(v) for k, v in rec.items()}
        core = {c: rec[c] for c in CORE}
        raw = {k: v for k, v in rec.items() if k not in CORE and v is not None}
        rows.append((core, raw))
    return rows


def load_sqlite(rows):
    SQLITE_DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(SQLITE_DB)
    con.executescript((ROOT / "db" / "schema_sqlite.sql").read_text(encoding="utf-8"))
    cols = CORE + ["raw_json"]
    sql = f"INSERT OR REPLACE INTO images ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    with con:
        con.executemany(sql, [[c[k] for k in CORE] + [json.dumps(r)] for c, r in rows])
    return con


def load_postgres(rows):
    import psycopg
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"].strip(), connect_timeout=30)
    with con:
        con.execute((ROOT / "db" / "schema_postgres.sql").read_text(encoding="utf-8"))
        cols = CORE + ["footprint", "raw"]
        ph = ",".join(["%s"] * len(CORE)) + (
            ",CASE WHEN %s IS NULL THEN NULL ELSE ST_MakeEnvelope(%s,%s,%s,%s,4326) END,%s::jsonb"
        )
        sql = (f"INSERT INTO images ({','.join(cols)}) VALUES ({ph}) "
               f"ON CONFLICT (image_id) DO UPDATE SET raw = EXCLUDED.raw")
        with con.cursor() as cur:
            for c, r in rows:
                cur.execute(sql, [c[k] for k in CORE] +
                            [c["west"], c["west"], c["south"], c["east"], c["north"], json.dumps(r)])
    return con


def verify(con, is_pg):
    q = lambda s: con.execute(s).fetchone()[0]
    checks = {
        "row count == 1204": q("select count(*) from images") == 1204,
        "image_id unique": q("select count(distinct image_id) from images") == 1204,
        "sha256 unique": q("select count(distinct sha256) from images") == 1204,
        "4 images without coordinates (USCG)": q("select count(*) from images where west is null") == 4,
        "bounds sane (west<east, south<north)":
            q("select count(*) from images where west >= east or south >= north") == 0,
        "every paired_image_id resolves":
            q("select count(*) from images a where paired_image_id is not null and "
              "not exists (select 1 from images b where b.image_id = a.paired_image_id)") == 0,
        "pairs are symmetric":
            q("select count(*) from images a join images b on b.image_id = a.paired_image_id "
              "where b.paired_image_id is distinct from a.image_id") == 0
            if is_pg else
            q("select count(*) from images a join images b on b.image_id = a.paired_image_id "
              "where b.paired_image_id is not a.image_id") == 0,
    }
    ok = True
    for name, passed in checks.items():
        print(("PASS  " if passed else "FAIL  ") + name)
        ok &= bool(passed)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["sqlite", "postgres"], default="sqlite")
    args = ap.parse_args()
    rows = read_rows()
    print(f"read {len(rows)} rows from CSV")
    con = load_postgres(rows) if args.target == "postgres" else load_sqlite(rows)
    print(f"loaded into {args.target}")
    sys.exit(0 if verify(con, args.target == "postgres") else 1)


if __name__ == "__main__":
    main()
