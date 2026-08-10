# Dossier Management — Document Listening & Packaging Pipeline

A **local, offline, human-in-the-loop** pipeline that watches a folder of project
dossiers (CLINS / FE / CE), auto-classifies them, keeps only the
information-bearing pages, and merges those pages (300 DPI screenshot +
provenance footer) into one PDF for a downstream multimodal LLM to synthesize.

> The pipeline **prepares evidence**; it does **not** draw the final conclusions.
> Output = `output/synthesis_input_{project_id}.pdf`, also exported to
> `<Listen Folder>/Dossier_condensed/{project}_synthesis.pdf`.

Everything runs on the local machine. No vector database, no embedding model,
no cloud call — retrieval is transparent TF-IDF over user-editable term lists.

---

## Report Types

| Type    | Meaning             | First-page signal used for auto-classification |
| ------- | ------------------- | ---------------------------------------------- |
| `CLINS` | Clinical            | clinical study / dermatological signals        |
| `FE`    | Sensory             | sensory evaluation signals                     |
| `CE`    | Consumer Evaluation | consumer test / panel signals                  |

---

## Source Formats

The pipeline ingests **PDF, PPTX and DOCX**. Non-PDF files are auto-converted to
a sibling PDF via locally-installed **Microsoft Office COM automation**
(`comtypes`, reusing the Office license you already own — no external commercial
license, no watermark) **before** classification / indexing / screenshotting. The
consumed `.pptx` / `.docx` source is removed once its PDF is confirmed on disk,
so every downstream step stays PDF-only. Office lock files (`~$*`) and OS cruft
(`Thumbs.db`, `.DS_Store`, `*.tmp`) are rejected up front.

*Requires Windows + an interactive desktop session with PowerPoint & Word. On any
other setup `ConverterUnavailable` is raised, caught, and the run continues with
the PDFs it already has.*

---

## Architecture — 5 Layers

```
┌────────────────────────────────────────────────────────────────┐
│  1. Interface Layer                                            │
│     static/ (index.html · app.js · style.css)                  │
│     src/api.py (FastAPI)  — one-click run, auto-watch toggle,  │
│     config modal, live activity log                            │
├────────────────────────────────────────────────────────────────┤
│  2. Orchestration Layer                                        │
│     src/orchestrator.py — run-all job, folder Watcher,         │
│                           activity feed, processing lock       │
│     src/pipeline.py     — DossierPipeline (ingest / package)   │
│     main.py             — CLI (serve/classify/ingest/package/  │
│                           run/reset)                           │
├────────────────────────────────────────────────────────────────┤
│  3. Processing Layer                                           │
│     converter (Office COM) · pdf_parser (PyMuPDF)              │
│     classifier (lexical)   · page_index (JSON page store)      │
│     retriever (TF-IDF + structure) · pdf_generator (reportlab) │
├────────────────────────────────────────────────────────────────┤
│  4. Storage Layer                                              │
│     index_projects/*.json · screenshots/ · output/             │
│     <Listen Folder>/Dossier_condensed/                         │
├────────────────────────────────────────────────────────────────┤
│  5. Config Layer                                               │
│     src/config.py · listen_folder.txt · config_overrides.json  │
│     queries/*.txt (page selection) · classify/*.txt (sorting)  │
└────────────────────────────────────────────────────────────────┘
```

| Layer                 | Responsibility                                                                                              |
| --------------------- | ----------------------------------------------------------------------------------------------------------- |
| **Interface**   | What the user touches — single-screen web UI + REST API.                                                    |
| **Orchestration** | Sequences the per-project chain, serializes concurrent work, streams progress to the UI.                    |
| **Processing**  | Stateless workers: convert → parse → classify → index → select pages → build output PDF.                |
| **Storage**     | Page-text index (JSON, no vectors), 300 DPI screenshots, synthesis PDF, exported copies.                    |
| **Config**      | Paths, thresholds, and the user-editable term lists. Runtime overrides persisted to `config_overrides.json`. |

---

## The Workflow

### 1 · Point the app at a Listen Folder

The **Listen Folder** is the base directory that holds your project folders.
Saved to `listen_folder.txt` as an ordered history — the first line is the
*active* folder, the rest is a re-selectable list in the Browse modal. The path
is intentionally **never written to the log files**.

```
<Listen Folder>/
├── PROJ-001/                 ← one project = one folder = one project_id
│   ├── report_a.pdf          ← unclassified, top level
│   ├── deck_b.pptx
│   ├── CLINS/ FE/ CE/        ← created by the classifier
├── PROJ-002/
└── Dossier_condensed/        ← results land here (excluded from scan & watch)
```

### 2 · Run — one click, or fully automatic

| Mode                  | Trigger                            | Scope                                          |
| --------------------- | ---------------------------------- | ---------------------------------------------- |
| **Run Full Pipeline** | button                             | every eligible project folder, sequentially    |
| **Auto-Watch**        | toggle                             | any **new** project folder dropped in while ON |

Both drive the same 5-stage chain per project, shown live in the stage tracker:

```
scan → classify → ingest → package → export
```

* **scan** — list top-level dossier files (pdf/pptx/docx, junk filtered).
* **classify** — first 1–2 pages of each doc scored against `classify/*.txt`;
  confident matches are moved into `CLINS/` `FE/` `CE/`; `UNKNOWN` stays put and
  is reported as a warning.
* **ingest** — convert → parse every page → write `index_projects/<project>.json`
  (text + structural signals). No embeddings.
* **package** — dual-track page selection → 300 DPI screenshots → merged PDF.
* **export** — copy to `<Listen Folder>/Dossier_condensed/<project>_synthesis.pdf`.

Safety properties: a single global lock means a manual run and a watcher run can
never overlap (COM automation and the index are not concurrency-safe); a new
folder is only processed after a **stability probe** (file count + total bytes
unchanged across two 4 s snapshots) so half-copied uploads are never ingested;
folders present when the watch is switched on are treated as known and are not
retro-processed.

### 3 · Tune (optional, `Config` button)

* **Classification Config** → `classify/{CLINS,FE,CE}.txt` — what a first page of
  each type looks like.
* **Page-Selection Config** → `queries/{CLINS,FE,CE}.txt` — what a *keepable*
  page looks like (see below).
* **Deletion Floor** → persisted to `config_overrides.json`, applies to every
  subsequent run including the one-click run.

**You give:** a folder of raw dossiers.
**You get:** one ordered, annotated PDF per project, ready for the LLM.

---

## How pages are selected (the core idea)

There is no static `is_summary` tag. **Every page is indexed equally**, then at
package time each page is scored on **two independent, parallel tracks** and
low-value pages are **deleted**.

### Track A — keyword relevance (weighted TF-IDF)

`queries/{type}.txt` is a **sectioned 4-dimension lexicon**:

```
# Title Anchors      → section/heading signals   weight 3.0
# Metric Keywords    → outcome/measurement terms weight 2.5
# Table Features     → statistical markers       weight 2.0
# Other              → project-specific vocab    weight 0.5  (anti-miss)
```

```
score = Σ_t  TF(t) · IDF(t) · W(dim) · Pos(t)
```

* **TF** — sublinear, `1 + log(count)`.
* **IDF** — BM25-style, computed **per report type over that project's pages**, so
  boilerplate that appears on every page is pushed toward zero automatically.
* **Pos** — a Title Anchor hitting the top 25 % of the page (the heading zone)
  gets ×2.0; a Table Feature co-occurring with a *detected* table gets ×1.5.
* Text is normalised (punctuation stripped, whitespace collapsed) so
  `Skin-elasticity` == `Skin elasticity`, and matching is whole-word / phrase —
  no more `log` matching `biology`.
* A flat, section-less file still works (legacy fallback → `metric_keywords`).

### Track B — structural richness

Independent of any keyword: `figures×1 + table×2 + bullet-list×1`, computed at
parse time by PyMuPDF (`get_images`, `find_tables`, bullet regex; a dense vector
drawing ≥ 20 paths also counts as a figure). This catches the result page that is
one big chart with almost no prose.

### DELETE mode — a floor, not a ranking

```
deleted  ⇔  keyword_score < FLOOR  AND  structure_score < FLOOR
```

Union semantics: **either** track passing keeps the page, so a page with a
relevant table but off-topic text (or the reverse) is never falsely removed. This
deliberately replaced the old "keep the top-N ranked pages", which silently
dropped mid-rank but still-valid pages.

* `DELETE_SCORE_FLOOR = 3.0` (absolute, global, same for all types). Calibrated
  on real indexed dossiers: `3.0` ≈ 9–12 % deleted, `4.0` ≈ 14–31 % (starts
  dropping low-structure single-figure pages). Tune from the UI.
* **TOC / cover pages are zeroed on both tracks** and therefore always deleted —
  a table of contents lists every section heading, i.e. exactly the high-weight
  Title Anchors, and in the heading zone too, so it would otherwise score at the
  top of Track A while its long list looked "dense" to Track B.
* `DELETE_MIN_KEEP = 3` — if an entire type is boilerplate and deletion would
  empty it, the 3 highest-value pages are kept and a warning is logged.
* `top_n` is an **optional ceiling applied after deletion**, off by default
  (`None`). Pass `--top-n N` / `top_n=N` only as a safety net; `-1` = no cap.
* Survivors are tagged `selected_by: ["keyword"|"structure"]` and ordered
  chosen-by-both first, then by combined relevance.

---

## The output PDF

Landscape A4, one source page per PDF page.

* **Cover** — project id, optional owner, page counts per type, timestamp, and
  the optional **target-formula banner**: a red-highlighted box (CJK-capable
  `STSong-Light` font) reading *"The final target formula for this Synthesis is:
  …; any data concerning other formulas is provided for development-reference
  purposes only."* — metadata injection that anchors the downstream LLM so it
  does not drift across the several formulas present in a dossier.
* **Body page** — the 300 DPI screenshot sized to fill the page, with a 7 pt
  provenance footer pinned to the bottom of the *same* page:
  `#idx [TYPE] filename — Page N | Source: <project-relative path> | Key terms: …`
  (key terms sorted by their TF·IDF·weight contribution, most salient first).
* Raw extracted text is deliberately **not** embedded — the screenshot is the
  authoritative visual, and raw extraction only adds OCR-ish noise (diagram
  fragments, stray numbering, leaked paths) that distracts the LLM.

---

## Quick Start

```bash
pip install -r requirements.txt

# Web UI (recommended)
python main.py serve --port 8000        # → http://localhost:8000
                                        #   API docs at /docs
```

CLI, for scripting a single project (`<project_id>` = the folder name under the
Listen Folder; falls back to the repo root when no Listen Folder is saved):

```bash
python main.py classify --project-id PROJ-001   # sort top-level dossiers into CLINS/FE/CE
python main.py ingest   --project-id PROJ-001   # parse + build the page index
python main.py package  PROJ-001                # select pages → screenshot → merge PDF
python main.py run      --project-id PROJ-001   # ingest + package in one shot
python main.py reset    --project-id PROJ-001   # clear index + screenshots

# package/run extras
  --top-n 12              optional per-type ceiling (-1 = no cap; default: none)
  --target-formula "..."  bake the target-formula banner into the cover
```

> `serve` hot-reloads **source only** (`reload_dirs = src/, static/`). This is
> deliberate: on a OneDrive-synced checkout, watching the whole tree makes sync
> events under `venv/` reload the server in an endless loop.

---

## REST API

| Method       | Path                       | Purpose                                                     |
| ------------ | -------------------------- | ----------------------------------------------------------- |
| `GET`        | `/`                        | Pipeline UI                                                 |
| `GET`        | `/html2pptx`               | HTML → PPTX utility page                                    |
| `GET/POST`   | `/config/listen-folder`    | Read / save the active Listen Folder                        |
| `GET`        | `/config/listen-folders`   | Full saved-folder history                                   |
| `DELETE`     | `/config/listen-folder`    | Remove one saved folder (`?path=`)                          |
| `GET`        | `/browse-folders`          | Folder-picker backend (`?path=`; drives when empty)         |
| `GET/POST`   | `/config/params`           | Read / save `delete_floor`                                  |
| `POST`       | `/run-all`                 | One-click chain for **all** project folders (background)    |
| `GET`        | `/run-all/status`          | Stage-tracker state of the running job                      |
| `GET`        | `/activity?since=<id>`     | Incremental activity feed for the UI log                    |
| `GET/POST`   | `/watch`                   | Auto-watch state / toggle                                   |
| `POST`       | `/project/scan`            | List unclassified dossiers in one project folder            |
| `POST`       | `/classify`                | Classify one project folder                                 |
| `POST`       | `/classify/confirm`        | Apply manual type decisions (move files)                    |
| `GET/POST`   | `/classify/profiles[/save]`| Read / write `classify/*.txt`                               |
| `GET/POST`   | `/queries[/save]`          | Read / write `queries/*.txt`                                |
| `POST`       | `/ingest` `/package` `/run`| Per-project pipeline steps                                  |
| `GET`        | `/status`                  | Index stats                                                 |
| `GET`        | `/download/{project_id}`   | Download the synthesis PDF                                  |
| `POST`       | `/reset`                   | Clear index + screenshots                                   |
| `POST`       | `/clear-reset`             | Safe reset: index + screenshots + output PDF **only**       |
| `GET/POST`   | `/config/pptx-output`      | Read / save the PPTX output folder                          |
| `POST`       | `/html2pptx/save`          | Persist a browser-generated PPTX                            |

> **Reset is deliberately non-destructive.** `/clear-reset` and
> `pipeline.reset()` only remove *derived* state (index, screenshots, generated
> PDF). Dossier files inside a project folder are the user's source of truth —
> they live in a synced directory and are never deleted.

---

## Configuration Reference

| Knob                            | Where                          | Default | Meaning                                     |
| ------------------------------- | ------------------------------ | ------- | ------------------------------------------- |
| Listen Folder                   | `listen_folder.txt` (UI)       | —       | Base dir for project folders; line 1 active |
| `DELETE_SCORE_FLOOR`            | `config.py` / UI → overrides   | `3.0`   | Both-tracks-below ⇒ page deleted            |
| `DELETE_MIN_KEEP`               | `config.py`                    | `3`     | Floor-emptied a type ⇒ keep N best + warn   |
| `TOP_N_PER_TYPE`                | `config.py` / `--top-n`        | `12`    | Ceiling, **only** when explicitly requested |
| `DIM_WEIGHTS`                   | `config.py`                    | 3/2.5/2/0.5 | Lexicon dimension weights               |
| `TITLE_ANCHOR_TOP_REGION/BOOST` | `config.py`                    | `0.25` / `2.0` | Heading-zone size / multiplier       |
| `TABLE_FEATURE_SYNERGY`         | `config.py`                    | `1.5`   | Table term × detected table                 |
| `SCREENSHOT_DPI`                | `config.py`                    | `300`   | Page screenshot resolution                  |
| `CLASSIFY_MIN_SCORE` / `_MARGIN`| `config.py`                    | `1` / `1` | Auto-file gate (else manual review)       |
| `pptx_output_dir`               | `config_overrides.json` (UI)   | Downloads | Where HTML → PPTX writes                  |

`classify/*.txt` and `queries/*.txt` are **two different things and both are
needed**: the former decides *which bucket a document goes into*, the latter
decides *which pages of it survive*. The classifier is intentionally left on the
simple lexical scorer — the TF-IDF work applies to page selection only.

---

## Side Utility — HTML → PPTX (`/html2pptx`)

A second page, reachable from the **HTML → PPTX** button in the header, turns
AI-generated slide **markup** into a real `.pptx`. AI tools return HTML *code*,
not files, so the page takes pasted source — no upload.

```
paste HTML code → [Convert] → <output folder>/<name>.pptx
```

**Where the conversion runs.** The vendored `html-to-pptx` library is not an HTML
parser — it walks a *rendered* DOM and reads `getComputedStyle()` /
`getBoundingClientRect()` / `offsetWidth`, values that only exist after a real
CSS layout engine has run. So the pasted markup is rendered in an **off-screen,
same-origin iframe** in the browser (the browser *is* the layout engine),
converted there, and the resulting bytes are POSTed to the backend, which writes
the file into the chosen folder. **Zero extra Python dependencies** — no
Playwright, no headless Chromium.

| Concern         | Behaviour                                                                                                                  |
| --------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Slide detection | `auto` tries `page` / `slide` / `h-ppt-page` / `ppt-page`; otherwise top-level blocks become slides. Override with a class name. Hidden / zero-size elements are skipped. |
| Output folder   | Defaults to the system **Downloads** folder; editable + Browse picker, persisted in `config_overrides.json` (`pptx_output_dir`). |
| Naming          | Sanitised, `.pptx` enforced, auto-suffixed `name (2).pptx` instead of overwriting.                                         |
| Code fences     | Markdown ```` ```html ```` wrappers are stripped automatically.                                                            |
| CDN decks       | Tailwind / Chart.js CDNs work — fonts, images and runtime CSS are awaited before measuring.                                |
| Safety          | Pasted scripts **do execute locally** during rendering (required for CDN decks). The server validates the ZIP magic, caps payloads at 80 MB, and strips any path component from the file name. |

---

## Downstream Companion Prompt

`prompt/new_system.md` is the system prompt for the downstream multimodal LLM
that consumes the synthesis PDF. One merged file, two mutually-isolated modes
routed by a leading token:

* `@extract` — parse the evidence PDF/screenshots into **strict JSON** (dynamic
  metric discovery, zero hallucination, transcribe-don't-invent traffic-light
  `status`, anti-truncation pagination).
* `@summarize` — consume **only** that JSON and render the synthesis report;
  every claim must be traceable to a `source` (file + page).

The focus meta-instruction ("when `@extract`, treat the `@summarize` section as
non-existent, and vice versa") exists to counter long-context attention drift.

---

## Directory Layout

```
main.py                     CLI entry point
src/                        core modules (api, orchestrator, pipeline,
                            retriever, classifier, converter, pdf_parser,
                            page_index, pdf_generator, config, logger)
static/                     frontend — index.html · app.js · style.css
                            + html2pptx.html · html2pptx.js
html-to-pptx/               vendored browser-side HTML→PPTX converter
                            (dist/html-to-pptx.min.js is the only runtime file)
queries/{CLINS,FE,CE}.txt   4-dimension page-selection lexicons  (editable)
classify/{CLINS,FE,CE}.txt  first-page classification anchors     (editable)
prompt/new_system.md        downstream LLM system prompt (@extract / @summarize)
listen_folder.txt           saved Listen Folder history (line 1 = active)
config_overrides.json       UI-persisted overrides (delete_floor, pptx dir)
index_projects/             page-text index, one JSON per project (no vectors)
screenshots/<TYPE>/<doc>/   300 DPI page screenshots (auto-generated)
output/                     synthesis_input_<project>.pdf
logs/                       runtime logs (never contain the Listen Folder path)
data/                       legacy global inbox layout — fallback only
汇报/                        local presentation material (git-ignored)
```

Per-project dossier folders live **outside** the repo, under the Listen Folder.

---

## Tech Stack

PyMuPDF · reportlab · Pillow · FastAPI · uvicorn · comtypes (Office COM) · numpy

### Design constraints worth keeping

* **No vector DB, no embedding model.** The corpus is a small, structured set of
  project reports and the analysis frame is already expressed as an editable term
  list, so lexical TF-IDF is sufficient, deterministic and fully explainable.
* **No static summary flag.** Selection is recomputed every run from the current
  lexicon, so retuning is instant and reversible.
* **Deletion, not ranking.** Rank-based truncation silently loses data; an
  absolute floor with union semantics does not.
* **Source files are sacred.** The pipeline reads dossiers and writes only
  derived artefacts. Nothing in the user's project folder is ever deleted except
  an Office source whose PDF has been verified on disk.
