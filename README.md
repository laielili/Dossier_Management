# Dossier Management — Document Listening & Packaging Pipeline

A **local, offline, human-in-the-loop** pipeline that extracts the summary pages
(high-resolution screenshot + structured annotation) from project reports
(CLINS / FE / CE), and merges them into a single PDF for a downstream
multimodal LLM (L'OréalGPT) to synthesize.

> The pipeline **prepares evidence**; it does **not** draw the final conclusions.
> Output = `output/synthesis_input_{project_id}.pdf` for the LLM to consume.

---

## Report Types

| Type         | Meaning             | First-page signal used for auto-classification |
| ------------ | ------------------- | ---------------------------------------------- |
| `CLINS` | Clinical            | clinical study / dermatological signals        |
| `FE`       | Sensory             | sensory evaluation signals                     |
| `CE`       | Consumer Evaluation | consumer test / panel signals                  |

---

## Source Formats

The pipeline ingests **PDF, PPTX, and DOCX**. Non-PDF files are auto-converted
to a sibling PDF (via locally-installed **Microsoft Office COM automation**, reusing
the Office license you already own — no external commercial license needed)
**before** classification / indexing / screenshotting. The converted PDF
lands next to the source in the same folder, so every downstream step stays
PDF-only. *Requires Windows + an interactive desktop session with PowerPoint &
Word installed.*

---

## Architecture — 5 Layers

```
┌────────────────────────────────────────────────────────────┐
│  1. Interface Layer                                          │
│     static/ (HTML/CSS/JS)  +  src/api.py (FastAPI)          │
│     Configure project, classify, edit queries, run, download│
├────────────────────────────────────────────────────────────┤
│  2. Orchestration Layer                                      │
│     main.py (CLI)  +  src/pipeline.py (DossierPipeline)     │
│     ingest → package → run → reset ; classify                │
├────────────────────────────────────────────────────────────┤
│  3. Processing Layer                                         │
│     pdf_parser (PyMuPDF)  ·  retriever (lexical)            │
│     classifier (lexical)  ·  pdf_generator (reportlab)      │
├────────────────────────────────────────────────────────────┤
│  4. Storage Layer                                           │
│     index_projects/ (page-text JSON)  ·  screenshots/       │
│     ·  output/                                               │
├────────────────────────────────────────────────────────────┤
│  5. Config Layer                                           │
│     src/config.py  ·  queries/*.txt  ·  classify/*.txt      │
└────────────────────────────────────────────────────────────┘
```

| Layer                   | Responsibility                                                                                       |
| ----------------------- | ---------------------------------------------------------------------------------------------------- |
| **Interface**     | What the user touches — web UI + REST API.                                                          |
| **Orchestration** | Sequences the steps of the pipeline; single source of truth for the workflow.                        |
| **Processing**    | Stateless workers: parse PDF, retrieve summary pages by keyword scoring, classify, build output PDF. |
| **Storage**       | Lightweight page-text index (JSON), 300 DPI page screenshots, final merged PDF.                      |
| **Config**        | Paths, thresholds, and the user-editable query & classification term lists.                          |

---

## User Journey — what they give, what they get

1. **Configure project** — enter a **project name**. The app looks for a folder
   with that name under the project root (`PROJECT_ROOT/<project_name>/`) and
   scans the dossier files (pdf/pptx/docx) inside it. (No manual upload — the
   source folder *is* the input.)
2. **Auto-classify** — each doc's first page is scored (keyword match) against
   `classify/*.txt` anchors. High-confidence docs are auto-filed into
   `<project_name>/CLINS` / `FE` / `CE`; low-confidence docs stay in the
   project folder, flagged in the UI for review.
3. **(Optional) Tune the analysis frame** — edit the per-type queries in the
   UI (persisted to `queries/*.txt`). This defines *what a "summary page" looks like*.
4. **Press Run** — the pipeline ingests, indexes, lexically discovers the summary
   pages per report type, screenshots them, and merges everything into one PDF.
5. **Download** — grab `output/synthesis_input_{project_id}.pdf` and feed it to L'OréalGPT.

**They give:** a project folder of raw report PDFs (+ optional query tuning).
**They get:** one clean, ordered PDF of the summary pages, ready for the LLM.

---

## How summary pages are found (the core idea)

No static `is_summary` tag. **Every page is ingested equally**, then at package time
the pipeline scores each page against the per-type query in `queries/{type}.txt`
(keyword / term-list matching — no embedding model, no vector database). Pages above
a score threshold are kept, then capped per type so the output PDF stays focused.
This keeps summary discovery flexible, fully user-configurable, and explainable.

---

## Quick Start

```bash
pip install -r requirements.txt

# Web UI (recommended)
python main.py serve --port 8000        # then open http://localhost:8000

# CLI — <project_id> names a folder under the project root that holds the
# raw dossiers; classified files are written to <project_id>/{CLINS,FE,CE}/.
python main.py classify --project-id PROJ-001   # propose + file types for PROJ-001/
python main.py ingest   --project-id PROJ-001
python main.py package  PROJ-001
python main.py run      --project-id PROJ-001   # ingest + package in one shot
python main.py reset    --project-id PROJ-001   # clear index + screenshots
```

---

## Side Utility — HTML → PPTX (`/html2pptx`)

A second page, reachable from the **HTML → PPTX** button in the header, turns
AI-generated slide **markup** into a real `.pptx`. AI tools return HTML *code*,
not files, so the page takes pasted source — no upload.

```
paste HTML code → [执行 / Convert] → <output folder>/<name>.pptx
```

**Where the conversion runs.** The vendored `html-to-pptx` library
(`html-to-pptx/`) is not an HTML parser — it walks a *rendered* DOM and reads
`getComputedStyle()` / `getBoundingClientRect()` / `offsetWidth`, values that
only exist after a real CSS layout engine has run. So the pasted markup is
rendered in an **off-screen, same-origin iframe** in the user's browser (the
browser *is* the layout engine), converted there, and the resulting bytes are
POSTed to the backend, which writes the file into the chosen folder. This adds
**zero Python dependencies** — no Playwright, no headless Chromium.

| Concern         | Behaviour                                                                 |
| --------------- | ------------------------------------------------------------------------- |
| Slide detection | `auto` tries `page` / `slide` / `h-ppt-page` / `ppt-page`; otherwise the document's top-level blocks are tagged as slides. Override with an exact class name. |
| Output folder   | Defaults to the system **Downloads** folder; editable + Browse picker, persisted in `config_overrides.json` (`pptx_output_dir`). |
| Naming          | Sanitised, `.pptx` enforced, auto-suffixed `name (2).pptx` instead of overwriting. |
| Code fences     | Markdown ```` ```html ```` wrappers are stripped automatically.             |
| CDN decks       | Tailwind / Chart.js CDNs work — fonts, images and runtime CSS are awaited before measuring. |
| Safety          | Pasted scripts **do execute locally** during rendering (required for CDN decks). The server validates the ZIP magic, caps payloads at 80 MB, and strips any path component from the file name. |

Endpoints: `GET /html2pptx`, `GET|POST /config/pptx-output`, `POST /html2pptx/save`.
Frontend: `static/html2pptx.{html,js}`. Library: `html-to-pptx/` (see its README).

---

## Directory Layout

```
html-to-pptx/               vendored browser-side HTML→PPTX converter
                            (dist/html-to-pptx.min.js is the only runtime file)
<project_name>/              a project folder under the project root; holds the
                            raw dossiers to classify (the input — no upload)
<project_name>/CLINS,
<project_name>/FE,
<project_name>/CE           classified outputs (report_type inferred from folder)
queries/{CLINS,FE,CE}.txt      per-type query term lists (editable)
classify/{CLINS,FE,CE}.txt     per-type classification anchors (editable)
index_projects/             lightweight page-text index (JSON, keyed by project, no vectors)
screenshots/                300 DPI page screenshots (auto-generated)
output/                     final synthesis PDF
logs/                       runtime logs
src/                        core modules
static/                     frontend (index.html, style.css, app.js)
```

## Tech Stack

PyMuPDF · reportlab · Pillow · FastAPI · uvicorn · comtypes (Office COM)

> No vector database and no embedding model are used. Retrieval is pure
> keyword / term-list scoring, which is sufficient for the small, structured
> corpus this pipeline handles and keeps the setup dependency-light.
