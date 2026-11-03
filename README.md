# CoreLog

**Drill-core photo → structured lithology log you can check, not a guess you must trust.**

Photograph a core tray (and optionally dictate a note). CoreLog reads the label and the depths, proposes a lithology log, then **cross-checks it with plain code**, **abstains when evidence is weak**, and hands every uncertain case to a **human for validation** before CSV/JSON export.

Built for the Meta Global AI Developer Hackathon (Indie track), 26 Oct – 4 Nov 2026. Bilingual French/English PWA, used from a phone.

Live demo: https://corelog.onrender.com (free tier: sleeps after 15 min, ~50 s to wake). Access is by code; a demo code is provided with the submission.

## The problem

Core logging is still largely manual and subjective: two geologists can describe the same core differently, and a long hole takes days or weeks. Seequent's announcement of its Imago image-analysis tool (13 Oct 2026) describes core logging as "manual and qualitative". Photos exist; trustworthy structured logs from them are the bottleneck.

## Why this is not "just an image analyzer"

A model that names a rock from a picture is easy and unreliable. The value here is everything around the model:

- **Deterministic checks in code** on what the model read: depth arithmetic (start, end, drilled, recovered), order, gaps and overlaps between runs, hole-ID consistency across photos.
- **Note vs photo**: the dictated note is compared with the photo reading by a code lexicon; a rock in the note that contradicts the domain you chose is flagged.
- **Voting and abstention**: two independent readings, a third to break ties, majority vote; `unknown` is a valid answer and never counted as a disagreement.
- **"Auto" never means "validated"**: it means "light review". Every uncertainty goes to a human, and the export records the human's decision.

## Pipeline

| Step | Who | Role |
|---|---|---|
| Quality gate | OpenCV (code) | Blur, light, framing; rejects unusable photos before any paid call |
| Main reading | **Muse Spark Open** (`meta-models/muse-spark-open-1026`, Baseten) | Strict JSON: label, depths, tape, lithology intervals, fractures |
| Second reading | **Muse Spark 1.3** (Meta API) in the default `mix` mode | Independent second opinion from a different model; a third reading breaks ties |
| Second reading, designed | **Glimmer** (Meta, self-hosted) | Part of the designed workflow as the independent second reader (`GLIMMER_URL`); see the note below: it could not be run |
| Label re-read | **SAM 3.1** + Spark Open | SAM finds the paper label; the code crops it and the label is re-read sharper |
| Dictation | **Muse Voice Transcribe 1.0** | Real-time WebSocket transcription (French) |
| Checks | Python code | Depth arithmetic, order, gaps/overlaps, hole-ID vote, note vs photo |
| Decision | Human | Validate, correct, comment, export CSV/JSON |

Per photo, the user picks the rock domain: **Sedimentary** (8 classes), **Crystalline** (10 classes, including `other`/`unknown`) or **Not sure** (all 16). The choice restricts the JSON-schema enum and the description rules the model sees, and is used to check the note. A **core-only** mode accepts photos without label or tape, with optional typed depths.

## Meta models used (and what each really does)

| Model | Role | Status |
|---|---|---|
| Muse Spark Open | Main reader, tiebreaker | Used |
| Muse Spark 1.3 (Meta API) | Independent second reader (`mix`, default) | Used |
| SAM 3.1 | Finds the label for a sharper re-read. Finds label and tape on 10/10 photos. Isolating the core itself did not work (tested, disabled) | Used |
| Muse Voice Transcribe 1.0 | Dictated note → text, compared with the photo reading | Used |
| Muse Wonder 1.0 | Second reader of the `meta` mode, a complete alternative pipeline (Spark 1.3 reads, Wonder checks) that works without Baseten | Implemented, run on the 10-photo comparison, kept as the **fallback** if the Baseten path is unavailable; not default (about 3x the cost, no gain on depths) |
| Glimmer | Designed second reader (`GLIMMER_URL`), wired in the code | **Never run**, see below; none of the results here involve it |

### About Glimmer

Glimmer is part of the intended workflow: the plan was to host it on Modal with the hackathon credits and use it as the independent second reader. The author could not activate the credits: Modal refused every payment card he tried (several cards, including virtual ones; obtaining an international card is very hard in Côte d'Ivoire). The integration is in the code and switches on when `GLIMMER_URL` is set, but it has never been run, so we make no claim about its behaviour. In the meantime the second reader is Muse Spark 1.3 (default `mix`).

## Why a vision model can read rock from pixels (author's position)

The author's working premise, as a geologist: a core photograph carries more than what a person consciously sees. Colour, grain texture, bedding and fissility, weathering and fracture surfaces are all encoded in the pixel values, and a vision model can use that information in ways that go beyond a quick human look. CoreLog builds on that premise but does not rely on it blindly: the model's reading is a proposal that code checks and a human validates.

## What we measured (and what we did not)

All numbers below come from the author's own photos of real core trays (borehole PH-016B, sedimentary). **A systematic ground-truth validation against the author's own descriptions is not finished, so no accuracy percentage is claimed.** Agreement between runs is not accuracy.

Three configurations on the same 10 photos (`CORELOG_MODE`):

| | `open` (Spark Open reads twice) | `meta` (Spark 1.3 + Wonder) | `mix` (Spark Open + Spark 1.3) |
|---|---|---|---|
| Cost, 10 photos | $0.45 | $1.25 | $0.42 |
| Photos sent to human review | 5/10 | 3/10 | 3/10 |
| Depths identical across modes | 10/10 | 10/10 | 10/10 |
| Lithology identical to `open` | – | 7/10 | 8/10 |

Reading: depth reading does not depend on the model; `meta` costs ~3x for no gain; `open` raises more alerts because one model contradicts itself on claystone vs shale (a real ambiguity, mostly a fissility question), whereas two different models are compared by rock family. One photo (the top 0–2.5 m, soil/fill vs sandstone) is read three different ways and is correctly sent to human review in every mode.

Earlier measurements on the `open` pipeline:

- Cost ≈ $0.045 per photo as counted by the app (provider billing may differ slightly); a dictated note costs ≈ $0.0004; SAM ≈ $0.0025 per call.
- Lithology: 9/10 consistent with an independent re-reading of the photos by an AI assistant (**not** a geologist's ground truth).
- Decision reproducibility between two identical runs: 7/10.
- Fracture counts are unreliable (one photo read between 5 and 14); they are flagged when readings diverge and never presented as a count.
- A public texture dataset (Mendeley "Lithology dataset", 2 cm crops, ~100 px): 3 correct of 15 with 10 abstentions. Not representative of the product (no label, no tray, tiny crops) and its class names were not independently verified; it shows the model abstains instead of guessing, nothing more.

## Limits

- Crystalline/basement classes have **never been evaluated on real photos**.
- No validation by a field geologist yet, and the author's own ground-truth pass over the photos is not finished: no accuracy figure is claimed.
- Free hosting: sleeps after 15 min, ephemeral disk (export after each hole), one analysis at a time.
- Analysis needs the network (only the app shell is cached offline).
- Ruler/tape often incomplete in photos; labels sometimes ambiguous (e.g. 21.35 vs 21.95 m): the inconsistency is flagged, not silently fixed.
- Voice transcription is French only.

## Run it

```
pip install -r requirements.txt     # ffmpeg must be installed
export META_API_KEY=...  BASETEN_API_KEY=...  CORELOG_TOKEN=an-admin-code
uvicorn server:app --port 7860
```

Render (free Docker): see `render.yaml`. Keys live only in environment variables, never in the repository or the browser. Without `CORELOG_TOKEN`, anyone who knows the URL spends your credits: always set one online.

Offline tests with fake models: `PYTHONPATH=stub:. python test_server.py` (also `test_roles.py`, `test_core_only.py`, `test_domaine.py`). UI without keys: `PYTHONPATH=stub:. python dev_fake.py`. `stub/` is for tests only.

Environment variables: `BASETEN_MODEL` (default `meta-models/muse-spark-open-1026`, the only model the hackathon credits cover), `CORELOG_MODE` (`mix` default, `open`, `meta`), `CORELOG_DOMAIN` (`auto` default), `GLIMMER_URL` / `GLIMMER_KEY`, `DEMO_MAX_PHOTOS` (default 40), `DATA_DIR`.

## Access: admin and testers

`CORELOG_TOKEN` is the admin code (sees everything, including the Architecture screen with model status). `CORELOG_DEMO_TOKENS` is a comma-separated list of demo codes: each has a **private** workspace (one tester never sees another's photos), no Architecture screen, and a photo quota. Remove a code from the variable to revoke a tester. The Sondages home screen has a Log out button.

## Rock domains are data, not code

`domains/<id>.json` defines lithologies (id, FR/EN names, colour, family), the description rules given to the model and the note lexicon. To add a domain: copy a file, keep `other` and `unknown`, and **measure on real photos before trusting it**.

## Languages

French and English (FR/EN button, phone language by default). UI, agent messages and exports follow the chosen language; translation rules are in `static/i18n.json`. A message without a rule stays in French.

## Built during the hackathon

First commit 29 Oct 2026; everything in this repository was written for this event (git history is the record). The author acted as architect and domain expert (problem, pipeline design, checks, rock domains, photos, acceptance); the code was written with AI assistance (Claude).

## Author

Souleymane Touré. DTS (bachelor-level, Licence-equivalent) in Mining and Geology, petroleum option, INP-HB Yamoussoukro, Côte d'Ivoire. MIT licence.
