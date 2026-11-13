"""Crystal Geo service API: image catalog + Qwen AI features.

Run from the repo root:
    python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
Interactive docs (try every endpoint in the browser): http://localhost:8000/docs

Endpoints
    GET  /health
    GET  /images             list/filter (source, role, date_from, date_to, bbox, limit, offset)
    GET  /images/{id}        one image's metadata (+ pair); ?full=true adds all 273 source columns
    GET  /images/{id}/file   the image itself (SHA-256 verified before it is served)
    GET  /pairs/{id}         the before/after pair for an image
    POST /ai/ask             {image_id, question}  -> Qwen answer, saved as a finding
    POST /ai/change          {image_id}            -> before/after flood change, saved as a finding
    GET  /findings           saved AI results (optional ?image_id=)

AI output is model-generated and unreviewed; every finding carries imagery date,
model id and settings. A verification failure never serves the unverified bytes.
"""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ai"))
import vlm_client as vlm  # noqa: E402  (reads QWEN_* from .env)

DB = ROOT / "data" / "crystal.sqlite"
SUMMARY = ("image_id, pair_id, paired_image_id, image_role, acquisition_date, locality, "
           "latitude, longitude, west, south, east, north, source_agency, platform, "
           "width_px, height_px, sha256")

app = FastAPI(title="Crystal Geo API", version="0.1.0")
# Dev-friendly: lets a Flutter web/desktop client on another origin call us.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def connect():
    if not DB.exists():
        raise HTTPException(503, "Database missing. Run: python db/load.py")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def summary(row) -> dict:
    d = dict(row)
    d["image_url"] = f"/images/{d['image_id']}/file"
    d["has_coordinates"] = d.get("west") is not None
    return d


def run_ai(fn, *args):
    """vlm_client exits with a message on bad input; turn that into an HTTP error."""
    try:
        return fn(*args)
    except SystemExit as e:
        msg = str(e.code) if e.code not in (None, 0) else "AI request failed"
        status = 404 if "Unknown image_id" in msg else 400
        raise HTTPException(status, msg)
    except Exception as e:  # network/model errors from the GB10
        raise HTTPException(502, f"AI backend error: {type(e).__name__}")


class AskBody(BaseModel):
    image_id: str
    question: str = Field(min_length=1, max_length=500)


class ChangeBody(BaseModel):
    image_id: str


@app.get("/health")
def health():
    with connect() as con:
        n = con.execute("select count(*) from images").fetchone()[0]
        f = con.execute("select count(*) from findings").fetchone()[0]
    return {"status": "ok", "images": n, "findings": f, "model": vlm.MODEL}


@app.get("/config")
def public_config():
    """Browser-safe settings. Only a Mapbox *public* token (pk.*) is ever returned; a secret
    token (sk.*) or anything else is withheld."""
    token = (os.environ.get("MAPBOX_TOKEN") or "").strip()
    return {"mapbox_token": token if token.startswith("pk.") else None}


@app.get("/images")
def list_images(source: str | None = None, role: str | None = None,
                date_from: str | None = None, date_to: str | None = None,
                bbox: str | None = Query(None, description="west,south,east,north (WGS84)"),
                paired_only: bool = False,
                limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, args = [], []
    if source:
        where.append("source_agency = ?"); args.append(source)
    if role:
        where.append("image_role = ?"); args.append(role)
    if date_from:
        where.append("acquisition_date >= ?"); args.append(date_from)
    if date_to:
        where.append("acquisition_date <= ?"); args.append(date_to)
    if paired_only:
        where.append("paired_image_id is not null")
    if bbox:
        try:
            w, s, e, n = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be west,south,east,north")
        where.append("west is not null and west <= ? and east >= ? and south <= ? and north >= ?")
        args += [e, w, n, s]
    clause = ("where " + " and ".join(where)) if where else ""
    with connect() as con:
        total = con.execute(f"select count(*) from images {clause}", args).fetchone()[0]
        rows = con.execute(f"select {SUMMARY} from images {clause} order by image_id limit ? offset ?",
                           args + [limit, offset]).fetchall()
    return {"total": total, "limit": limit, "offset": offset, "items": [summary(r) for r in rows]}


@app.get("/images/{image_id}")
def get_image(image_id: str, full: bool = False):
    with connect() as con:
        row = con.execute("select * from images where image_id = ?", (image_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Unknown image_id: {image_id}")
        d = summary(dict(row))
        raw = json.loads(d.pop("raw_json"))
        d.update({k: row[k] for k in ("license_url", "attribution", "image_bytes",
                                      "native_resolution_min_m", "event_name", "state")})
        if full:
            d["raw"] = raw
        return d


@app.get("/images/{image_id}/file")
def get_image_file(image_id: str):
    with connect() as con:
        row = con.execute("select image_path, sha256 from images where image_id = ?",
                          (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Unknown image_id: {image_id}")
    path = ROOT / "data" / "hf" / row["image_path"]
    if not path.exists():
        raise HTTPException(404, "Image file not downloaded on this server")
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        raise HTTPException(500, "PayloadHashMismatch: refusing to serve unverified image")
    return FileResponse(path, headers={"X-Image-SHA256": row["sha256"],
                                       "Cache-Control": "public, max-age=3600"})


@app.get("/pairs/{image_id}")
def get_pair(image_id: str):
    with connect() as con:
        row = con.execute(f"select {SUMMARY} from images where image_id = ?", (image_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Unknown image_id: {image_id}")
        if not row["paired_image_id"]:
            raise HTTPException(404, f"{image_id} has no before/after partner")
        other = con.execute(f"select {SUMMARY} from images where image_id = ?",
                            (row["paired_image_id"],)).fetchone()
    a, b = sorted([summary(row), summary(other)], key=lambda r: r["acquisition_date"])
    return {"before": a, "after": b}


@app.post("/ai/ask")
def ai_ask(body: AskBody):
    answer = run_ai(vlm.ask, body.image_id, body.question)
    return {"image_id": body.image_id, "question": body.question, "answer": answer,
            "model": vlm.MODEL, "reviewed_by_human": False,
            "notice": "Model-generated; not verified."}


@app.post("/ai/change")
def ai_change(body: ChangeBody):
    result = run_ai(vlm.change, body.image_id)
    return {"image_id": body.image_id, "result": result, "model": vlm.MODEL,
            "reviewed_by_human": False, "notice": "Model-generated; not verified."}


@app.get("/findings")
def list_findings(image_id: str | None = None, limit: int = Query(50, ge=1, le=500)):
    q, args = "select * from findings", []
    if image_id:
        q += " where image_id = ?"; args.append(image_id)
    q += " order by finding_id desc limit ?"; args.append(limit)
    with connect() as con:
        rows = [dict(r) for r in con.execute(q, args).fetchall()]
    for r in rows:
        r["reviewed_by_human"] = bool(r["reviewed_by_human"])
        r["settings"] = json.loads(r.pop("settings_json") or "null")
    return {"items": rows}


# Serve the web UI from the same origin as the API: http://localhost:8000/app/
if (ROOT / "app").is_dir():
    app.mount("/app", StaticFiles(directory=ROOT / "app", html=True), name="app")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")
