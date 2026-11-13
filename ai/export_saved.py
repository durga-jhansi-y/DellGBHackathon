"""Export saved AI analyses from the local database to backend/saved_analyses.json.

The cloud copy of the API cannot reach the UTSA Qwen server, so it serves these
saved results instead. Run from the repo root after analyzing locally:
    python ai/export_saved.py
Each record keeps its provenance (model id, imagery date, when it was run) and is
marked unreviewed. Nothing here contains keys or server addresses.
"""
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "crystal.sqlite"
OUT = ROOT / "backend" / "saved_analyses.json"


def parse(answer: str):
    m = re.search(r"\{[\s\S]*\}", answer or "")
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("select * from findings order by finding_id").fetchall()
    latest = {}   # newest result per (kind, image)
    for r in rows:
        latest[(r["kind"], r["image_id"])] = r
    items = []
    for (kind, image_id), r in latest.items():
        rec = {"kind": kind, "image_id": image_id, "paired_image_id": r["paired_image_id"],
               "imagery_time": r["imagery_time"], "model_id": r["model_id"], "created_at": r["created_at"],
               "reviewed_by_human": False}
        if kind == "change":
            p = parse(r["answer"]) or {}
            rec.update({"flooding_visible_after": p.get("flooding_visible_after"), "changes": p.get("changes"),
                        "confidence": p.get("confidence")})
        else:
            rec.update({"question": r["question"], "answer": r["answer"]})
        items.append(rec)
    OUT.write_text(json.dumps({"note": "Saved analyses generated on the UTSA Qwen server; model output, not human-reviewed.",
                               "items": items}, indent=1), encoding="utf-8")
    kinds = {}
    for i in items:
        kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    print(f"wrote {OUT} with {len(items)} analyses {kinds}")


if __name__ == "__main__":
    main()
