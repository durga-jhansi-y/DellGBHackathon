# Crystal Geo: Presentation Script (about 3 minutes)

Speaker notes in *italics*. Each person can read their own part.

---

## 1. The problem (20 seconds)

When a hurricane or flood hits, the first things to fail are cell towers and power.
That is exactly when responders, insurers, and local officials most need to know
what changed on the ground, and they need it *offline*. Even when they have
imagery, two questions are hard to answer in the field: **"Can I trust this
file?"** and **"What am I actually looking at?"**

## 2. What we built (30 seconds)

**Crystal Geo** is an offline-first field app for aerial imagery. You download a
verified imagery package before you lose connectivity. In the field you can
browse it, see exactly where it came from, and ask an AI to compare before and
after photos of a flood.

Our test data is real: **1,204 aerial images of Texas flooding**, including
**575 Hurricane Harvey before-and-after pairs** from 2016 and 2017, plus Hill
Country flood images from July 2025.

## 3. Who did what (60 seconds)

**Durga: data and AI.**
- Built the image catalog: schema, loader, and 1,204 verified records. A
  Postgres/PostGIS schema for **Tiger Data** is ready alongside the local
  database we run today.
- Connected **Qwen3-VL-8B**, running on the **Dell GB10**, to answer questions
  about an image and to compare a before-and-after pair.
- Every answer is saved with its imagery date, model name, and settings, and is
  labeled **unreviewed**, so nobody mistakes it for confirmed fact.
- Built the **FastAPI** service that connects the catalog and the AI to the app.

**Greatness: the interface.**
- Designed and built the app's front end: the map workspace, dataset library,
  findings view, first-run guide, and clear "verified" and "unverified" states.

**Shruthi: compression and trust.**
- Built the offline package format: tiles compressed with lossless **WebP**,
  indexed in **SQLite**, with **SHA-256** hashes for every piece.
- Built the verifier in **Python and Rust**: a bad or half-downloaded package
  is rejected, and the app keeps using the last good one.
- Started the continuous-recognition pipeline that will feed verified tiles to
  the model.

## 4. Tools (15 seconds)

Python, FastAPI, SQLite, Postgres/PostGIS on Tiger Data, Qwen3-VL-8B on the Dell
GB10 through an OpenAI-compatible API, Hugging Face for the dataset, Rust,
WebP and SHA-256 for compression and integrity, HTML/CSS/JavaScript for the app,
and GitHub for working in parallel.

## 5. Why it matters (40 seconds)

- **It works when everything else is down.** Disaster response happens exactly
  where connectivity doesn't.
- **It's trustworthy by design.** Every image is checked against its published
  fingerprint before it's shown. If a file was damaged or altered, the app
  refuses to display it instead of quietly showing something wrong.
- **AI that shows its work.** Findings carry the date, the source, the model
  version and an "unreviewed" flag. The AI speeds up triage; a person decides.
- **Speed where it counts.** Comparing before and after photos by eye across
  hundreds of locations is slow. The model gives responders a first pass in
  seconds, so people spend their time on the places that need them.
- **Runs on local hardware.** The model runs on a Dell GB10 we control, so
  sensitive imagery doesn't have to go to an outside cloud service.

## 6. Honest status (15 seconds)

*Say this out loud. It makes everything above more credible.*

In an early spot check, the model correctly said "no flooding" on three dry
2016 images and "flooding" on three flooded 2017 images. That is a sanity check,
not a measured accuracy, and the next step is a labeled test set. Our verified
offline package is a development format for one demo area and is unsigned. The
full signed, multi-area version is the roadmap.

## Close (10 seconds)

Crystal Geo turns imagery into something a responder can trust and act on, even
when the network is gone.

---

## Before you present, confirm

1. Do the front end and the API run together on the demo laptop? If so, say
   "connected to live data" in section 2. If not, say "prototype".
2. Which database is the demo running on? Today it is the local one, with the
   Tiger Data schema ready. Do not say "running on Tiger" unless the connection
   works from this network.
3. Is the GB10 reachable from the demo network? If not, use the saved findings
   in the database and say they were generated earlier.
4. Pick two examples to show live, for example `TX001` and `TX005`, and check
   their answers before you go on stage.
5. Don't quote the 0.95 confidence number as accuracy. It is the model's own
   self-report, and it was the same on every pair.
6. Dataset license is CC-BY-NC-4.0: describe this as a research and
   demonstration project, not a commercial product.
