# Dossier Management — Document Listening & Denoise Pipeline

A **local, offline, human-in-the-loop** pipeline that watches a folder of project
dossiers (CLINS / FE / CE), auto-classifies them, and **denoises each source
dossier in place** — dropping provably-noise pages (cover, TOC, boilerplate,
blank, closing) while keeping every evidence-bearing page — then writes a cleaned
copy of each dossier into a deliverable folder for a downstream multimodal LLM to
read.

> The pipeline **prepares evidence**; it does **not** draw the final conclusions.
> Deliverable = one denoised PDF **per source dossier**, under
> `<Listen Folder>/Dossier_condensed/<project>/<CLINS|FE|CE>/<filename>.pdf`.
>
> The former single merged **synthesis PDF** was discontinued: the downstream
> client accepts many files but rejects any single file over 50 MB, so dossiers
> are kept as separate, cleaned documents instead of one giant combined file.

Everything runs on the local machine. No vector database, no embedding model,
no cloud call — page denoising is transparent, rule-based noise classification
over user-editable term lists.

---

## Report Types

| Type      | Meaning             | First-page signal used for auto-classification |
| --------- | ------------------- | ---------------------------------------------- |
| `CLINS` | Clinical            | clinical study / dermatological signals        |
| `FE`    | Sensory             | sensory evaluation signals                     |
| `CE`    | Consumer Evaluation | consumer test / panel signals                  |

---

## Source Formats

The pipeline ingests **PDF, PPTX and DOCX**. Non-PDF files are auto-converted to
a sibling PDF via locally-installed **Microsoft Office COM automation**
(`comtypes`, reusing the Office license you already own — no external commercial
license, no watermark) **before** classification / indexing. The consumed
`.pptx` / `.docx` source is removed once its PDF is confirmed on disk, so every
downstream step stays PDF-only. Office lock files (`~$*`) and OS cruft
(`Thumbs.db`, `.DS_Store`, `*.tmp`) are rejected up front.

*Requires Windows + an interactive desktop session with PowerPoint & Word. On any
other setup `ConverterUnavailable` is raised, caught, and the run continues with
the PDFs it already has.*

---

## Architecture — 5 Layers

```
┌────────────────────────────────────────────────────────────────┐
│  1. Interface Layer                                            │
│     static/ — 4 pages sharing style.css:                       │
│       search.html    (Dossier Search, new homepage "/")        │
│       file_listener.html (File Listener pipeline UI, "/FileListener") │
│       svg2ppt.html   (SVG → PPTX deck builder)                │
│     src/api.py (FastAPI) — run, auto-watch, retrieval,         │
│     config modal, live activity log                            │
├────────────────────────────────────────────────────────────────┤
│  2. Orchestration Layer                                        │
│     src/orchestrator.py — run-all job, folder Watcher,         │
│                           activity feed, processing lock       │
│     src/pipeline.py     — DossierPipeline (ingest / condense)  │
│     main.py             — CLI (serve/classify/ingest/package/ │
│                           run/reset)                           │
├────────────────────────────────────────────────────────────────┤
│  3. Processing Layer                                           │
│     converter (Office COM) · pdf_parser (PyMuPDF)              │
│     classifier (lexical)   · page_index (JSON page store)      │
│     retriever (noise-deletion)                                 │
├────────────────────────────────────────────────────────────────┤
│  4. Storage Layer                                              │
│     index_projects/*.json · screenshots/ · Dossier_condensed/  │
├────────────────────────────────────────────────────────────────┤
│  5. Config Layer                                               │
│     src/config.py · listen_folder.txt · config_overrides.json  │
│     queries/query.txt (veto terms) · classify/*.txt (sorting) │
└────────────────────────────────────────────────────────────────┘
```

| Layer                   | Responsibility                                                                                                               |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Interface**     | What the user touches — single-screen web UI + REST API.                                                                    |
| **Orchestration** | Sequences the per-project chain, serializes concurrent work, streams progress to the UI.                                     |
| **Processing**    | Stateless workers: convert → parse → classify → index → denoise → write cleaned PDF copies.                             |
| **Storage**       | Page-text index (JSON, no vectors), screenshot cache, denoised per-document PDFs under`Dossier_condensed/<project>/`.      |
| **Config**        | Paths, noise-deletion thresholds, and the user-editable term lists. Runtime overrides persisted to`config_overrides.json`. |

---

## The Workflow

### 1 · Point the app at a Folder

The selected **Folder** is the base directory that holds your project folders.
The path is intentionally **never written to the log files**.

```
<Listen Folder>/
├── PROJ-001/                 ← one project = one folder = one project_id
│   ├── report_a.pdf          ← unclassified, top level
│   ├── deck_b.pptx
│   ├── CLINS/ FE/ CE/        ← created by the classifier
├── PROJ-002/
└── Dossier_condensed/        ← results land here (excluded from scan & watch)
    └── PROJ-001/
        ├── CLINS/report_a.pdf   ← denoised copy of the source dossier
        └── FE/deck_b.pdf
```

### 2 · Run — one click, or fully automatic

| Mode                        | Trigger | Scope                                               |
| --------------------------- | ------- | --------------------------------------------------- |
| **Run Full Pipeline** | button  | every eligible project folder, sequentially         |
| **Auto-Watch**        | toggle  | any**new** project folder dropped in while ON |

Both drive the same 4-stage chain per project, shown live in the stage tracker:

```
scan → classify → ingest → condense
```

* **scan** — list top-level dossier files (pdf/pptx/docx, junk filtered).
* **classify** — first 1–2 pages of each doc scored against `classify/*.txt`;
  confident matches are moved into `CLINS/` `FE/` `CE/`; `UNKNOWN` stays put and
  is reported as a warning. PDFs with no extractable text layer are left
  UNKNOWN but still indexed and de-noised like any other file.
* **ingest** — convert → parse every page → write `index_projects/<project>.json`
  (text + structural signals) and the 300 DPI screenshot cache. No embeddings.
* **condense** — denoise every source dossier: drop the noise pages the retriever
  classifies (`cover` / `toc` / `boilerplate` / `blank` / `closing`), then write
  a faithful vector copy of each surviving document to
  `<Listen Folder>/Dossier_condensed/<project>/<type>/<filename>.pdf`.
  **Source dossiers are never modified** — only cleaned copies are written.

Safety properties: a single global lock means a manual run and a watcher run can
never overlap (COM automation and the index are not concurrency-safe); a new
folder is only processed after a **stability probe** (file count + total bytes
unchanged across two 4 s snapshots) so half-copied uploads are never ingested;
folders present when the watch is switched on are treated as known and are not
retro-processed.

The file manager is opened automatically at the end of a run so you can drag the
finished PDFs straight into a downstream AI client — at the **project subfolder**
(`Dossier_condensed/<project>/`) for a single project (Watcher / manual run), or
at the **parent `Dossier_condensed/`** after a multi-project Run Full Pipeline.

### 3 · Tune (optional, `Config` button)

* **Classification Config** → `classify/{CLINS,FE,CE}.txt` — what a first page of
  each type looks like.
* **Page-Selection / Veto Config** → `queries/query.txt` — the high-value terms
  (Title Anchors + Table Features) that force-keep an otherwise-noise page.
* **Noise categories** — the five noise types the denoiser drops are shown (read
  only); the underlying thresholds live in `src/config.py`.

**You give:** a folder of raw dossiers.
**You get:** a folder of denoised dossiers — one cleaned folder with source file,
ready for the LLM, under `<AI_feed>`.

---

## Dossier Search & Retrieval (new primary entry point)

The homepage (`/`) is now **Dossier Search** — a keyword-driven way to pull
specific dossiers from *anywhere* on disk and preprocess just those, instead of
pointing the whole pipeline at one Listen Folder.

1. **Search & Select** — enter a target path (searched recursively for
   `pdf` / `pptx` / `docx` / `xlsx`); keyword-filter the hits and tick the
   files you want. The target-path history is saved to `search_paths.txt`.
2. **Preprocess** — the selected files are copied into
   `retrieved/<project_name>/` and the same chain
   (`scan → classify → ingest → condense`) runs
   on that cache folder. The
   denoised per-document PDFs land under
   `retrieved/<project_name>/Dossier_condensed/<type>/`, and the file manager
   opens that folder at the end so you can drag the results straight into a
   downstream AI client.

The original Listen-Folder pipeline (Run Full Pipeline / Auto-Watch) is still
available at **`/FileListener`** (`static/file_listener.html`) and behaves exactly as
described in [The Workflow](#the-workflow) above.

---

## How pages are denoised (the core idea)

There is no static `is_summary` tag. **Every page is indexed equally**, then at
condense time each page is classified as **noise** or kept. The model is
**keep-all, drop-noise**: a page is deleted only when it is *provably* noise, so
genuinely evidence-bearing pages (including a pure-text conclusion) are never
lost. There is no TF-IDF, no score floor, and no ranking/truncation.

### Noise classification

`retriever.classify_noise()` returns a category only on strong structural
evidence:

| Category        | Detected when                                                                  |
| --------------- | ------------------------------------------------------------------------------ |
| `toc`         | A table-of-contents header appears in the first few lines.                     |
| `cover`       | Low text, large title font, no table/list, fewer than`COVER_MAX_FIGURES`.    |
| `blank`       | Little text, no figure/table/list, and a small font.                           |
| `closing`     | Short page whose text matches a closing marker (e.g. "Thank you", "Appendix"). |
| `boilerplate` | Text is almost entirely lines repeated across many pages of the same type.     |

A cross-page pass (`_detect_boilerplate`) flags template / footer-only pages.
`UNKNOWN`-type (unclassified) files **are** still indexed and denoised — only
the typed-folder routing is skipped. They are written to
`Dossier_condensed/<project>/UNKNOWN/` (instead of `CLINS/` `FE/` `CE/`) and
flagged as a warning so you can review or re-classify them later. This keeps the
denoise pass working on documents that simply don't match any word list.

### Veto layer — keep the pages that matter

Before a noise page is dropped, it is checked against a **veto** regex built from
the high-value terms in `queries/query.txt` (`Title Anchors` + `Table Features`
sections, with `VETO_SEED_TERMS` as fallback). A noise page whose text contains
such a term is **force-kept** (`selected_by = ["veto"]`).

Two noise types are **veto-immune** — always deleted regardless of any high-value
term, because the report title + type are already carried by the folder/file
layout:

* `toc` and `cover` (pure navigation);
* a `boilerplate` page whose unique-char count is below `VETO_MIN_UNIQUE_CHARS`
  (pure template, nothing worth rescuing).

### Ordering & safety nets

* Survivors keep their **original report order** (`source_path`, `page_index`),
  never re-ranked.
* `top_n` is an **optional ceiling applied after deletion** only as a safety net
  (default `None`; `-1` = no cap, `N > 0` keeps at most the first N per type).
* `DELETE_MIN_KEEP` — if a whole type classifies entirely as noise (so deletion
  would empty it), the first `DELETE_MIN_KEEP` pages by original order are kept
  and a warning is logged, so the deliverable is never empty.
* Kept pages are tagged `selected_by: ["content"|"veto"]` for traceability.

---

## The output — denoised per-document PDFs

No merged file, no rendered cover, no screenshots-in-PDF, no footers. Each source
dossier is rewritten with PyMuPDF `doc.select(...)` so the surviving pages are
emitted as a **faithful vector copy** of the original document:

* **Text, images, and layout are preserved exactly** — nothing is re-rasterised
  or re-rendered, so visual fidelity is 1:1 and the file stays a real PDF (not an
  image stack).
* **Noise pages are removed; survivors stay in their original order.**
* **One cleaned file per source dossier**, written to
  `Dossier_condensed/<project>/<type>/<same-filename>.pdf`.
* **Outputs are re-compressed losslessly on save.** The surviving pages are
  re-packed with `garbage=3 / clean / deflate / use_objstms`, so the cleaned
  file is typically *smaller* than the source even though every vector and
  bitmap is preserved byte-for-byte (no re-rasterisation). This adds head-room
  under the downstream client's 50 MB per-file ceiling, on top of the page-drop
  itself.

Because each deliverable is just a trimmed copy of one moderate-sized source, no
single file balloons past the downstream client's 50 MB limit — which is exactly
why the old single merged PDF was retired.

---

## Quick Start

```bash
py -m venv venv

venv/scripts/activate

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
python main.py package  PROJ-001                # denoise each source dossier → cleaned per-doc PDFs
python main.py run      --project-id PROJ-001   # ingest + condense (package) in one shot
python main.py reset    --project-id PROJ-001   # clear index + screenshots

# package/run extras
  --top-n 12              optional per-type ceiling (-1 = no cap; default: none)
```

> `serve` hot-reloads **source only** (`reload_dirs = src/, static/`). This is
> deliberate: on a OneDrive-synced checkout, watching the whole tree makes sync
> events under `venv/` reload the server in an endless loop.

---

## REST API

| Method              | Path                                   | Purpose                                                                            |
| ------------------- | -------------------------------------- | ---------------------------------------------------------------------------------- |
| `GET`             | `/`                                  | Dossier Search UI (new homepage)                                                   |
| `GET/POST`        | `/config/listen-folder`              | Read / save the active Listen Folder                                               |
| `GET`             | `/config/listen-folders`             | Full saved-folder history                                                          |
| `DELETE`          | `/config/listen-folder`              | Remove one saved folder (`?path=`)                                               |
| `GET`             | `/browse-folders`                    | Folder-picker backend (`?path=`; drives when empty)                              |
| `GET/POST`        | `/config/params`                     | Read the active noise categories + veto terms                                      |
| `POST`            | `/run-all`                           | One-click chain for**all** project folders (background)                      |
| `GET`             | `/run-all/status`                    | Stage-tracker state of the running job                                             |
| `GET`             | `/activity?since=<id>`               | Incremental activity feed for the UI log                                           |
| `GET/POST`        | `/watch`                             | Auto-watch state / toggle                                                          |
| `POST`            | `/project/scan`                      | List unclassified dossiers in one project folder                                   |
| `POST`            | `/classify`                          | Classify one project folder                                                        |
| `POST`            | `/classify/confirm`                  | Apply manual type decisions (move files)                                           |
| `GET/POST`        | `/classify/profiles[/save]`          | Read / write`classify/*.txt`                                                     |
| `GET/POST`        | `/queries[/save]`                    | Read / write`queries/query.txt`                                                  |
| `POST`            | `/ingest`                            | Per-project: parse + build the page index                                          |
| `POST`            | `/package` `/run`                  | Per-project pipeline steps (denoise source dossiers)                               |
| `GET`             | `/status`                            | Index stats                                                                        |
| `POST`            | `/reset`                             | Clear index + screenshots (derived state only)                                     |
| `POST`            | `/clear`                             | Full wipe: project folders + Dossier_condensed + derived state (index/screenshots) |
| `GET/POST`        | `/config/pptx-output`                | Read / save the PPTX output folder                                                 |
| `GET`             | `/FileListener`                      | Original Listen-Folder pipeline UI (File Listener)                                 |
| `POST`            | `/search`                            | Keyword search for dossier files under a target path                               |
| `POST`            | `/retrieve/start`                    | Copy selected files →`retrieved/<name>/` + start pipeline                       |
| `GET`             | `/retrieve/status`                   | Retrieval preprocessing progress (stage tracker)                                   |
| `GET/POST/DELETE` | `/config/search-paths`               | Read / save / remove the search-path history                                       |
| `GET`             | `/svg2ppt`                           | SVG → PPTX deck-builder page                                                      |
| `POST`            | `/svg2ppt/build`                     | Build a 5-region PPTX deck from`<deck>` XML                                      |
| `GET`             | `/svg2ppt/files/{run_id}/{filename}` | Download a built deck / preview                                                    |
| `GET`             | `/download/{project_id}`             | Download a project's denoised deliverables                                         |

> **`/reset` is deliberately non-destructive** — it only removes *derived* state
> (index, screenshots). The `/clear` button is the opposite: it permanently
> deletes past runs, including the dossier source folders, `Dossier_condensed/`
> contents, and all derived state (index + screenshots). It requires an explicit
> confirm in the UI. The former `/clear-reset` endpoint was removed — its scope
> is now fully covered by `/clear`.

---

## Configuration Reference

| Knob                                 | Where                          | Default       | Meaning                                                                                                                                                                                                                                                                                                      |
| ------------------------------------ | ------------------------------ | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Listen Folder                        | `listen_folder.txt` (UI)     | —            | Base dir for project folders; line 1 active                                                                                                                                                                                                                                                                  |
| `DELETE_MIN_KEEP`                  | `config.py`                  | `3`         | Type emptied by deletion ⇒ keep N best + warn                                                                                                                                                                                                                                                               |
| `TOP_N_PER_TYPE`                   | `config.py` / `--top-n`    | `12`        | Ceiling,**only** when explicitly requested                                                                                                                                                                                                                                                             |
| `SCREENSHOT_DPI`                   | `config.py`                  | `300`       | Page screenshot resolution (ingest cache)                                                                                                                                                                                                                                                                    |
| `CLASSIFY_MIN_SCORE` / `_MARGIN` | `config.py`                  | `1` / `1` | Auto-file gate (else manual review)                                                                                                                                                                                                                                                                          |
| Noise thresholds                     | `config.py`                  | —            | `BLANK_MAX_CHARS` (30), `BLANK_MAX_FONT` (18), `COVER_MIN_FONT` (20), `COVER_MAX_CHARS` (60), `COVER_MAX_FIGURES` (3), `CLOSING_MAX_CHARS` (120), `BOILERPLATE_MIN_PAGES` (10), `BOILERPLATE_MIN_UNIQUE_CHARS` (40), `VETO_MIN_UNIQUE_CHARS` (10) — code-defined, not user-tunable via UI |
| `pptx_output_dir`                  | `config_overrides.json` (UI) | Downloads     | Where HTML → PPTX writes                                                                                                                                                                                                                                                                                    |

`classify/*.txt` and `queries/query.txt` are **two different things and both are
needed**: the former decides *which bucket a document goes into*, the latter
supplies the *veto terms* that rescue an otherwise-noise page (its Title Anchors

+ Table Features sections; Metric Keywords / Other sections are not used for
  scoring). The classifier is intentionally left on the simple lexical scorer and
  the denoise stage is rule-based noise classification — neither uses TF-IDF.

---

## Side Utility — SVG → PPTX (`/svg2ppt`)

Reachable from the **SVG → PPTX** button in the
header. Where XML → PPTX reverses *rendered* CSS geometry (error-prone), this
builder consumes **content SVG components** whose geometry is already exact, so
layout mistakes are structurally eliminated.

The downstream `@summarize` stage (see
[Downstream Companion Prompt](#downstream-companion-prompt)) now emits a
`<deck>` XML of typed SVG components (`heading` / `table` / `metric-card` /
`image` / `text-block` …). This module — `src/svg2ppt/` — routes each component
into a 5-region page template (from `prompt/system.md`: `top_banner` /
`meta_row` / `left_column` / `middle_column` / `right_column`, canvas
1000×562.5), paginates with a soft `max_pages` cap, and renders the deck
**server-side, offline** via PyMuPDF → PNG → python-pptx. The AI supplies
*content + a semantic `type`* only; all coordinates, pagination and chrome
(banner, side-tab, section titles) are decided by the layout engine.

```
paste/upload <deck> XML → [Build] → output/<run>/synthesis_deck.pptx + preview.html
```

| Concern   | Behaviour                                                                                          |
| --------- | -------------------------------------------------------------------------------------------------- |
| Input     | `<deck>` XML (components carry `type` for region routing; AI writes no x/y).                   |
| Layout    | 5-region template, vertical stacking, overflow → continuation page (repeats chrome, middle only). |
| Rendering | PyMuPDF rasterises each component SVG → Pillow composites the page → python-pptx 16:9 slide.     |
| Output    | Image-type PPTX (visually faithful, not shape-editable) + HTML preview for human QA.               |
| Theme     | L'Oréal palette from`prompt/system.md`; canvas fixed 16:9.                                      |
| Deps      | `python-pptx` + `Pillow` + `PyMuPDF` (no browser, no headless).                              |

Design notes: `synthesis_deck_design.md`. Module layout:
`src/svg2ppt/{schema,layout,render,api}.py` + `templates/deck_5region.json`.

---

## Downstream AI Prompt

`prompt/system.md` is the system prompt for the downstream multimodal LLM
that consumes the denoised dossier PDFs. Two mutually-isolated modes are routed
by a leading token:

* `@extract` — parse the evidence PDFs into **strict JSON** (dynamic
  metric discovery, zero hallucination, transcribe-don't-invent traffic-light
  `status`, anti-truncation pagination).
* `@summarize` — consume **only** that JSON and emit the synthesis as a
  `<deck>` XML of typed SVG components (see
  [SVG → PPTX](#side-utility--svg--pptx-svg2ppt)); every figure must be
  traceable to a `source` (file + page). The XML is fed to `src/svg2ppt/`
  to build the deliverable PPTX deck.

The focus meta-instruction ("when `@extract`, treat the `@summarize` section as
non-existent, and vice versa") exists to counter long-context attention drift.

---

## Directory Layout

```
main.py                     CLI entry point
src/                        core modules (api, orchestrator, pipeline,
                            retriever, classifier, converter, pdf_parser,
                            page_index, config, logger)
src/svg2ppt/                SVG → PPTX deck builder (schema, layout, render,
                            api; templates/deck_5region.json)
static/                     frontend pages (shared style.css):
                              search.html · search-app.js   (Dossier Search, "/")
                              file_listener.html · app.js    (File Listener pipeline, "/FileListener")
                              svg2ppt.html · svg2ppt-app.js (SVG → PPTX)
queries/query.txt           unified veto-term lexicon (Title Anchors +
                            Table Features; editable)
classify/{CLINS,FE,CE}.txt  first-page classification anchors     (editable)
prompt/system.md        downstream LLM system prompt (@extract / @summarize)
listen_folder.txt           saved Listen Folder history (line 1 = active)
config_overrides.json       UI-persisted overrides (pptx dir)
index_projects/             page-text index, one JSON per project (no vectors)
screenshots/<TYPE>/<doc>/   300 DPI page screenshots (auto-generated, cached)
<Dossier_condensed>/        DELIVERABLES — one subfolder per project, each
  <project>/                  holding denoised per-document PDFs under
    <CLINS|FE|CE>/            <type>/<same-filename>.pdf
logs/                       runtime logs (never contain the Listen Folder path)
data/                       legacy global inbox layout — fallback only
汇报/                        local presentation material (git-ignored)
retrieved/                  retrieval cache — selected files + their
                              Dossier_condensed/ deliverables (git-ignored)
search_paths.txt            saved search-path history (git-ignored)
synthesis_deck_design.md    svg2ppt design notes (companion doc)
```

Per-project dossier folders live **outside** the repo, under the Listen Folder.
`pdf_generator.py` (the old reportlab-based merged-PDF generator) has been retired
to `_trash/`; deliverables are now the per-document denoised PDFs above.

---

## Tech Stack

PyMuPDF · Pillow · python-pptx · FastAPI · uvicorn · pydantic · comtypes (Office COM)
