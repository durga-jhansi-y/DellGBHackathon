"""Qwen vision-language client for the GB10 (OpenAI-compatible endpoint).

Reads settings from the environment (.env at the repo root, git-ignored):
  QWEN_BASE_URL   e.g. http://<gb10-ip>/v1
  QWEN_MODEL      e.g. qwen3-vl-8b-instruct
  QWEN_API_KEY    the key you were given (never commit it)

CLI (repo root, after `python db/load.py`):
  python ai/vlm_client.py ask    TX001_2017_NOAA "Is there flooding visible?"
  python ai/vlm_client.py change  TX001_2017_NOAA          # before/after pair
Every result is saved to the findings table with provenance. Output is
model-generated and unverified: reviewed_by_human stays 0.
"""
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "crystal.sqlite"
IMG_DIR = ROOT / "data" / "images"
HF = "https://huggingface.co/datasets/jennifer-fowler/UTSA-flood-images/resolve/main/"
MAX_SIDE = 1024          # downscale before sending; keeps requests fast
TEMPERATURE = 0.2

load_dotenv(ROOT / ".env")
MODEL = os.environ.get("QWEN_MODEL", "qwen3-vl-8b-instruct")


def client() -> OpenAI:
    key = os.environ.get("QWEN_API_KEY")
    base = os.environ.get("QWEN_BASE_URL")
    if not key or not base:
        sys.exit("Set QWEN_BASE_URL and QWEN_API_KEY in .env (see .env.example).")
    return OpenAI(base_url=base, api_key=key, timeout=120, max_retries=2)


def db():
    if not DB.exists():
        sys.exit("No database yet. Run: python db/load.py")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def image_row(con, image_id):
    row = con.execute("select * from images where image_id=?", (image_id,)).fetchone()
    if not row:
        sys.exit(f"Unknown image_id: {image_id}")
    return row


def local_image(row) -> Path:
    """Download (once) and verify the image against its published sha256."""
    local = ROOT / "data" / "hf" / row["image_path"]     # full local download, if present
    path = local if local.exists() else IMG_DIR / Path(row["image_path"]).name
    if not path.exists():
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(HF + row["image_path"], path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        if path != local:
            path.unlink()
        sys.exit(f"SHA-256 mismatch for {row['image_id']}; refusing to use it.")
    return path


def data_url(path: Path) -> str:
    img = Image.open(path).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def chat(content):
    r = client().chat.completions.create(
        model=MODEL, temperature=TEMPERATURE, max_tokens=700,
        messages=[{"role": "user", "content": content}],
    )
    return r.choices[0].message.content.strip()


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def save(con, kind, row, paired, question, answer, confidence, prompt):
    settings = {"temperature": TEMPERATURE, "max_side_px": MAX_SIDE,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16]}
    con.execute(
        "insert into findings (kind,image_id,paired_image_id,question,answer,confidence,"
        "imagery_time,model_id,settings_json) values (?,?,?,?,?,?,?,?,?)",
        (kind, row["image_id"], paired["image_id"] if paired else None, question, answer,
         confidence, row["acquisition_date"], MODEL, json.dumps(settings)))
    con.commit()


def ask(image_id, question):
    con = db()
    row = image_row(con, image_id)
    prompt = (f"{question}\nAnswer from what is visible in this aerial image only. "
              "Say 'uncertain' if the pixels do not establish an answer.")
    ans = chat([{"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url(local_image(row))}}])
    save(con, "qa", row, None, question, ans, None, prompt)
    return ans


CHANGE_PROMPT = (
    "These are two aerial photos of the same place. Image 1 is BEFORE ({d1}); image 2 is "
    "AFTER ({d2}, Hurricane Harvey response). Describe visible changes related to flooding "
    "(standing water, inundated roads or buildings, debris). Respond with JSON only: "
    '{{"flooding_visible_after": "yes|no|uncertain", "changes": "<short description>", '
    '"confidence": <number 0 to 1>}}. Do not claim more than the pixels show.')


def change(image_id):
    con = db()
    row = image_row(con, image_id)
    if not row["paired_image_id"]:
        sys.exit(f"{image_id} has no before/after partner.")
    other = image_row(con, row["paired_image_id"])
    before, after = sorted([row, other], key=lambda r: r["acquisition_date"])
    prompt = CHANGE_PROMPT.format(d1=before["acquisition_date"], d2=after["acquisition_date"])
    text = chat([{"type": "text", "text": prompt},
                 {"type": "image_url", "image_url": {"url": data_url(local_image(before))}},
                 {"type": "image_url", "image_url": {"url": data_url(local_image(after))}}])
    parsed = parse_json(text) or {}
    conf = parsed.get("confidence")
    save(con, "change", after, before, "before/after flood change", text,
         float(conf) if isinstance(conf, (int, float)) else None, prompt)
    return parsed or {"raw": text}


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "ask":
        print(ask(sys.argv[2], " ".join(sys.argv[3:])))
    elif len(sys.argv) == 3 and sys.argv[1] == "change":
        print(json.dumps(change(sys.argv[2]), indent=2))
    else:
        print(__doc__)
