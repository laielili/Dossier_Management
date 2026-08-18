"""
FastAPI server — Dossier_Management Document Pipeline

Endpoints:
  GET  /browse-folders        — Directory picker backend (returns subfolders)
  POST /project/scan     — Scan PROJECT_ROOT/<name>/ for dossiers
  POST /classify         — Auto-classify dossiers in the project folder
  POST /classify/confirm — Apply final type decisions (move files)
  GET  /classify/profiles     — Read type profiles from classify/*.txt
  POST /classify/profiles/save — Save type profiles to classify/*.txt
  POST /ingest           — Trigger ingest (reads <project>/{CLINS,FE,CE}/)
  POST /package          — Trigger condense (denoise source dossiers → per-doc PDFs)
  POST /run              — One-click: ingest + condense
  GET  /status           — Index stats
  GET  /queries          — Read the unified query lexicon from queries/query.txt
  POST /queries/save     — Save the unified query lexicon to queries/query.txt
  POST /reset            — Reset project (index + screenshots only)
  POST /clear            — Full wipe of past runs: Dossier_condensed contents +
                           derived state (index + screenshots)
  GET  /activity         — Incremental activity feed for the frontend log
  GET  /html2pptx        — Serves the HTML → PPTX converter page
  GET  /config/pptx-output  — Read the PPTX output folder (default: Downloads)
  POST /config/pptx-output  — Save the PPTX output folder
  POST /html2pptx/save   — Write a browser-generated PPTX (base64) to disk
  GET  /svg2ppt         — Serves the SVG -> PPTX converter page
  POST /svg2ppt/build   — Build a deck from AI SVG-component XML (server-side)
  GET  /svg2ppt/files/{run_id}/{filename} — Download a generated artifact
  GET  /                — Serves the frontend UI
"""

import base64
import binascii
import calendar
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    INDEX_DIR,
    PROJECT_ROOT,
    REPORT_TYPES,
    RETRIEVED_DIR,
    SCREENSHOTS_DIR,
    default_pptx_output_dir,
    delete_search_path,
    get_pptx_output_dir,
    get_search_paths,
    set_pptx_output_dir,
    set_search_path,
    project_data_dir,
    NOISE_CATEGORY_LABELS,
)
# NOTE: DATA_DIR (the legacy global data/ folder) is intentionally no longer
# imported here — the pipeline now reads/writes per-project folders
# (PROJECT_ROOT/<project_id>/{CLINS,FE,CE}/) instead of a shared data/ tree.
from .logger import get_logger
from .pipeline import (
    DossierPipeline,
    load_queries_from_files,
    save_queries_to_files,
)
from .classifier import (
    Classifier,
    load_profiles_from_files as load_classify_profiles,
    save_profiles_to_files as save_classify_profiles,
)
from .retriever import list_veto_terms, reset_veto_terms
from .converter import _is_junk_filename
from .orchestrator import (
    CONDENSED_DIR_NAME,
    get_events,
    open_condensed_folder,
    retrieve_start,
    retrieve_status,
)
from .svg2ppt.api import router as svg2ppt_router

logger = get_logger("api")


def _is_dossier_ext(name: str) -> bool:
    """True for the document extensions the pipeline accepts."""
    return Path(name).suffix.lower() in (".pdf", ".pptx", ".docx")


app = FastAPI(title="Dossier_Management Document Pipeline", version="2.0")

# SVG -> PPTX deck builder (new, additive). Serves /svg2ppt and builds decks
# entirely server-side. Existing html2pptx / dossier endpoints are untouched.
app.include_router(svg2ppt_router)

# Serve static files (CSS, JS, etc.)
app.mount(
    "/static",
    StaticFiles(directory=str(PROJECT_ROOT / "static")),
    name="static",
)
# Serve the vendored html-to-pptx bundle. It is a browser-only library (it reads
# getComputedStyle / getBoundingClientRect), so the conversion runs inside an
# off-screen iframe on the client; the server only serves the bundle and writes
# the resulting bytes to disk. See html-to-pptx/README.md.
app.mount(
    "/html-to-pptx",
    StaticFiles(directory=str(PROJECT_ROOT / "html-to-pptx")),
    name="html-to-pptx",
)
# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PackageRequest(BaseModel):
    project_id: str = "default"
    top_n: Optional[int] = None  # cap of pages PER report type; -1 = All (no cap); None = config default
    project_owner: str = ""  # name of the project owner, shown on the PDF cover
    target_formula: str = ""  # final target formula; baked into the PDF cover


class RunRequest(BaseModel):
    project_id: str = "default"
    top_n: Optional[int] = None  # cap of pages PER report type; -1 = All (no cap); None = config default
    project_owner: str = ""  # name of the project owner, shown on the PDF cover
    target_formula: str = ""  # final target formula; baked into the PDF cover


class ScanRequest(BaseModel):
    project_name: str  # folder name under PROJECT_ROOT to scan for dossiers


class QueriesSaveRequest(BaseModel):
    queries: dict[str, str]


class PptxOutputRequest(BaseModel):
    path: str  # absolute folder where generated .pptx files are written


class Html2PptxSaveRequest(BaseModel):
    """A PPTX produced in the browser, handed to the server for writing.

    ``data_base64`` is the raw pptx (zip) payload produced by
    ``HtmlToPptx.exportHtmlToPpt(pageClass, "base64")``. It may arrive as a
    bare base64 string or as a ``data:...;base64,`` URI — both are accepted.
    """

    filename: str = "presentation"
    data_base64: str
    output_dir: Optional[str] = None   # one-off override; omit to use the saved folder
    overwrite: bool = False            # False → auto-suffix "name (2).pptx"

class ClassifyConfirmRequest(BaseModel):
    decisions: list[dict] = []   # [{"filename": ..., "report_type": ...}]
    # project_id is taken from the query string (consistent with /classify),
    # NOT the body — the frontend sends it as ?project_id=... alongside the
    # decisions payload. Declaring it here too would shadow/silently default
    # it to "default" and break per-project sorting.

class ClassifyProfileSaveRequest(BaseModel):
    profiles: dict[str, str]


class SearchPathRequest(BaseModel):
    path: str  # absolute folder to search inside (saved as a history entry)


class SearchRequest(BaseModel):
    """Search a target folder (recursively) for dossier files.

    Keywords are matched against the FILE NAME only (case-insensitive
    substring, OR logic — a file matches if ANY non-empty keyword appears).
    1–3 keywords may be supplied; empty entries are ignored. The last-modified
    filter is optional; when enabled only files modified within
    ``modified_years`` years + ``modified_months`` months are returned.
    """
    target_path: str
    keywords: list[str] = []
    modified_enabled: bool = False
    modified_years: int = 1
    modified_months: int = 0


class RetrieveStartRequest(BaseModel):
    """Kick off preprocessing for a set of selected files.

    The server copies each file into retrieved/<project_name>/ (collision-safe)
    and runs the pipeline (scan -> classify -> ingest -> condense) on that
    cache folder. Non-PDF files (pptx/docx) are converted to PDF by the
    pipeline's classifier when Office is available; xlsx is left unprocessed.
    """
    project_name: str
    files: list[str]

# ---------------------------------------------------------------------------
# Global pipeline state (one project at a time)
# ---------------------------------------------------------------------------

_pipeline: DossierPipeline | None = None


def _get_pipeline(project_id: str = "default") -> DossierPipeline:
    global _pipeline
    if _pipeline is None or _pipeline.project_id != project_id:
        _pipeline = DossierPipeline(project_id)
        _pipeline.init()
    return _pipeline


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the frontend UI (Dossier Search — the new homepage)."""
    index_path = PROJECT_ROOT / "static" / "search.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>Frontend not found. Place search.html in static/</h2>")


@app.get("/FileListener", response_class=HTMLResponse)
async def file_listener():
    """Serve the original pipeline UI (Listen Folder + Run Full Pipeline), branded File Listener."""
    page = PROJECT_ROOT / "static" / "file_listener.html"
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>file_listener.html not found in static/</h2>", status_code=404)


@app.get("/html2pptx", response_class=HTMLResponse)
async def html2pptx_page():
    """Serve the HTML → PPTX converter page."""
    page = PROJECT_ROOT / "static" / "html2pptx.html"
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>html2pptx.html not found in static/</h2>", status_code=404)


@app.get("/config/params")
async def get_config_params():
    """Return the user-tunable pipeline parameters.

    Noise-based deletion is always on. This endpoint exposes the active noise
    categories and the veto terms (reused from queries/query.txt) so the
    frontend can render them.
    """
    noise_categories = []
    for k in NOISE_CATEGORY_LABELS:
        noise_categories.append({
            "key": k,
            "label": NOISE_CATEGORY_LABELS.get(k, k),
            "active": True,
        })
    return {
        "ok": True,
        "noise_enabled": True,
        "noise_categories": noise_categories,
        "veto_terms": list_veto_terms(),
    }






@app.get("/config/search-paths")
async def get_search_paths_config():
    """Return the saved search target paths (history, most-recent first).

    The frontend renders these as a re-loadable / deletable list in the
    search UI. The path value is intentionally NEVER written to logs.
    """
    paths = get_search_paths()
    return {
        "ok": True,
        "paths": paths,
        "active": paths[0] if paths else "",
    }


@app.post("/config/search-paths")
async def set_search_path_config(req: SearchPathRequest):
    """Persist a search target path to search_paths.txt (history)."""
    if not req.path or not req.path.strip():
        raise HTTPException(400, "path is required")
    set_search_path(req.path.strip())
    return {"ok": True, "path": req.path.strip()}


@app.delete("/config/search-paths")
async def delete_search_path_config(path: str = ""):
    """Delete a single saved search target path from the history list."""
    if not path or not path.strip():
        raise HTTPException(400, "path is required")
    removed = delete_search_path(path.strip())
    if not removed:
        raise HTTPException(404, "path not found in saved list")
    return {
        "ok": True,
        "removed": path.strip(),
        "paths": get_search_paths(),
    }


@app.get("/browse-folders")
async def browse_folders(path: str = ""):
    """Directory-picker backend: list subfolders under `path`.

    With no `path`, returns available drives (Windows) or the root (posix).
    The frontend renders this as a navigable tree and returns the chosen
    absolute path. The path value is never logged.
    """
    if not path:
        if os.name == "nt":
            drives = [
                f"{chr(d)}:\\"
                for d in range(ord("A"), ord("Z") + 1)
                if os.path.exists(f"{chr(d)}:\\")
            ]
            return {"ok": True, "path": None, "parent": None, "drives": drives, "dirs": []}
        root = Path("/")
        dirs = sorted(
            str(c) for c in root.iterdir()
            if c.is_dir() and not c.is_symlink()
        )
        return {"ok": True, "path": "/", "parent": None, "drives": [], "dirs": dirs}

    current = Path(path)
    if not current.exists() or not current.is_dir():
        raise HTTPException(404, f"Folder not found: {path}")

    parent = str(current.parent) if current.parent != current else None
    dirs = []
    try:
        for child in sorted(current.iterdir()):
            if child.is_dir() and not child.is_symlink():
                dirs.append(str(child))
    except OSError:
        pass
    return {
        "ok": True,
        "path": str(current),
        "parent": parent,
        "drives": [],
        "dirs": dirs,
    }


@app.post("/project/scan")
async def scan_project(req: ScanRequest):
    """Scan a project folder for dossier files.

    The project folder is PROJECT_ROOT/<project_name>/. Top-level dossier
    files (pdf/pptx/docx, excluding Office lock files / OS cruft) are listed.
    Already-classified subfolders (CLINS/FE/CE) are ignored so a re-scan
    does not double-count.
    """
    name = (req.project_name or "").strip()
    if not name:
        raise HTTPException(400, "project_name is required")

    folder = project_data_dir(name)
    if not folder.exists():
        raise HTTPException(
            404,
            f"Project folder not found: {folder}",
        )

    files = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        if _is_junk_filename(p.name):
            continue
        if not _is_dossier_ext(p.name):
            continue
        files.append({
            "filename": p.name,
            "size_kb": round(p.stat().st_size / 1024, 1),
            "type": p.suffix.lower().lstrip(".").upper(),
        })

    return {
        "ok": True,
        "project_name": name,
        "folder": str(folder),
        "files": files,
        "count": len(files),
    }


@app.post("/ingest")
async def ingest(project_id: str = "default"):
    """Run ingest: parse all PDFs in PROJECT_ROOT/<project_id>/{CLINS,FE,CE}/ and build the index."""
    pipeline = _get_pipeline(project_id)
    try:
        count = pipeline.ingest()
        return {
            "ok": True,
            "project_id": project_id,
            "pages_ingested": count,
            "total_pages": pipeline.total_pages,
        }
    except Exception as e:
        logger.exception("Ingest failed")
        raise HTTPException(500, str(e))


@app.post("/package")
async def package(req: PackageRequest):
    """Run condense: denoise each source dossier -> cleaned per-doc PDFs.

    Body (JSON):
        project_id: str  (default "default")
        top_n: int | null  (optional per-type page cap)
    The deliverable is written to <PROJECT_ROOT>/Dossier_condensed/<project_id>/.
    """
    pipeline = _get_pipeline(req.project_id)
    try:
        out_dir = PROJECT_ROOT / CONDENSED_DIR_NAME / req.project_id
        result = pipeline.condense(output_dir=out_dir, top_n=req.top_n)
        return {
            "ok": True,
            "project_id": req.project_id,
            "output_dir": str(out_dir),
            "files_written": result["files_written"],
            "pages_dropped": result["pages_dropped"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Package failed")
        raise HTTPException(500, str(e))


@app.post("/run")
async def run_pipeline(req: RunRequest):
    """One-click: ingest + condense.

    Body (JSON):
        project_id: str  (default "default")
        top_n: int | null  (optional per-type page cap)
    The deliverable (cleaned per-doc PDFs) is written to
    <PROJECT_ROOT>/Dossier_condensed/<project_id>/.
    """
    pipeline = _get_pipeline(req.project_id)
    try:
        n = pipeline.ingest()
        logger.info(f"Ingested {n} pages for project '{req.project_id}'")
        out_dir = PROJECT_ROOT / CONDENSED_DIR_NAME / req.project_id
        result = pipeline.condense(output_dir=out_dir, top_n=req.top_n)
        logger.info(f"Condense complete -> {out_dir}")
        return {
            "ok": True,
            "project_id": req.project_id,
            "pages_ingested": n,
            "output_dir": str(out_dir),
            "files_written": result["files_written"],
            "pages_dropped": result["pages_dropped"],
            "total_pages": pipeline.total_pages,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Run pipeline failed")
        raise HTTPException(500, str(e))


@app.get("/status")
async def status(project_id: str = "default"):
    """Get retriever / index status."""
    pipeline = _get_pipeline(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        "retriever_mode": "lexical",
        "pages_indexed": pipeline.total_pages,
    }


@app.get("/download/{project_id}")
async def download(project_id: str):
    """Deprecated.

    Output is now a folder of per-document PDFs under
    <PROJECT_ROOT>/Dossier_condensed/<project_id>/, not a single file, so there
    is nothing to download here. Kept as a 404 so any stale client fails clearly.
    """
    raise HTTPException(
        404, "Output is now a folder; see <PROJECT_ROOT>/Dossier_condensed/<project_id>/"
    )


@app.post("/reset")
async def reset(project_id: str = "default"):
    """Reset project: clear index and screenshots."""
    pipeline = _get_pipeline(project_id)
    pipeline.reset()
    return {"ok": True, "project_id": project_id}


# NOTE: /clear wipes the Dossier_condensed export folder plus derived state
# (index + screenshots). The listen-folder project-subfolder deletion was retired
# with the listen-folder configuration feature.

@app.post("/clear")
async def clear_residual():
    """Permanently delete the residual left by past processing runs.

    Scope (IRREVERSIBLE — the frontend requires an explicit confirm first):
      1) all contents of <PROJECT_ROOT>/Dossier_condensed/ (the exported PDFs);
      2) all *derived* state from previous runs — the page-text index
         (index_projects/) and the screenshot cache (screenshots/).

    The Dossier_condensed folder itself (not its contents) is kept so the next
    run can export into it immediately. Project paths are intentionally NEVER
    written to the server log.
    """
    base_p = PROJECT_ROOT

    removed: list[str] = []
    errors: list[dict] = []



    # 1) Everything inside /Dossier_condensed (keep the folder itself).
    condensed = base_p / CONDENSED_DIR_NAME
    if condensed.exists():
        for item in sorted(condensed.iterdir()):
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                else:
                    shutil.rmtree(item)
                removed.append(str(item))
            except OSError as e:
                logger.warning(f"clear: could not remove {item}: {e}")
                errors.append({"path": str(item), "error": str(e)})

    # 2) Derived state (global, lives under PROJECT_ROOT, keyed by project_id).
    #    Wipe it wholesale — it is cheap to regenerate and is exactly the
    #    "residual of past runs" this button targets.
    #    (a) page-text index
    if INDEX_DIR.exists():
        for f in INDEX_DIR.glob("*.json"):
            try:
                f.unlink()
                removed.append(str(f))
            except OSError as e:
                logger.warning(f"clear: could not delete index {f}: {e}")
                errors.append({"path": str(f), "error": str(e)})
    #    (b) screenshot cache
    if SCREENSHOTS_DIR.exists():
        for rt in REPORT_TYPES:
            rt_dir = SCREENSHOTS_DIR / rt
            if not rt_dir.exists():
                continue
            for item in list(rt_dir.iterdir()):
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                    else:
                        shutil.rmtree(item)
                    removed.append(str(item))
                except OSError as e:
                    logger.warning(f"clear: could not remove {item}: {e}")
                    errors.append({"path": str(item), "error": str(e)})
    logger.info(
        f"Clear residual: removed {len(removed)} item(s), "
        f"{len(errors)} error(s)"
    )
    return {"ok": True, "removed": removed, "errors": errors}


# ---------------------------------------------------------------------------
# Auto-classification endpoints (no upload — dossiers are read from the
# per-project folder PROJECT_ROOT/<project_id>/)
# ---------------------------------------------------------------------------

@app.post("/classify")
async def classify_inbox(project_id: str = "default"):
    """Classify every dossier in the project folder.

    High-confidence matches are auto-archived into
    PROJECT_ROOT/<project_id>/{type}/. Low-confidence / UNKNOWN files stay in
    the project folder, flagged for review.

    Returns:
        {"ok": true, "results": [ {filename, report_type, confidence,
          scores, low_confidence, archived, current_path}, ... ]}
    """
    pipeline = _get_pipeline(project_id)
    base_dir = project_data_dir(project_id)
    classifier = Classifier(base_dir=base_dir)
    try:
        out = classifier.classify_inbox()
        return {
            "ok": True,
            "results": out["results"],
            "unprocessed": out.get("unprocessed", []),
        }
    except Exception as e:
        logger.exception("Classification failed")
        raise HTTPException(500, str(e))


@app.post("/classify/confirm")
async def confirm_classification(req: ClassifyConfirmRequest, project_id: str = "default"):
    """Apply final type decisions, moving files into <project>/{type}/.

    Query param:
        project_id: str  (the project folder name)
    Body (JSON):
        decisions: [ {"filename": "...", "report_type": "CLINS"}, ... ]

    Already-correct files are skipped; low-confidence and corrected files
    are moved to their chosen folder.
    """
    pipeline = _get_pipeline(project_id)
    base_dir = project_data_dir(project_id)
    classifier = Classifier(base_dir=base_dir)
    try:
        moved = classifier.apply_decisions(req.decisions)
        return {"ok": True, "moved": moved, "count": len(moved)}
    except Exception as e:
        logger.exception("Confirm classification failed")
        raise HTTPException(500, str(e))


@app.get("/classify/profiles")
async def get_classify_profiles():
    """Read current type profiles from classify/*.txt."""
    try:
        profiles = load_classify_profiles()
        return {"ok": True, "profiles": profiles}
    except Exception as e:
        logger.exception("Failed to load classify profiles")
        raise HTTPException(500, str(e))


@app.post("/classify/profiles/save")
async def save_classify_profiles_endpoint(req: ClassifyProfileSaveRequest):
    """Save type profiles to classify/*.txt.

    Body (JSON):
        profiles: {"CLINS": "...", "FE": "...", "CE": "..."}
    """
    try:
        for rt in req.profiles:
            if rt not in REPORT_TYPES:
                raise HTTPException(
                    400,
                    f"Invalid report_type '{rt}'. Must be one of {REPORT_TYPES}",
                )
        save_classify_profiles(req.profiles)
        return {"ok": True, "saved": list(req.profiles.keys())}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to save classify profiles")
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Query management endpoints
# ---------------------------------------------------------------------------

@app.get("/queries")
async def get_queries():
    """Read the unified query lexicon from queries/query.txt.

    Returns the same text replicated for every report type so the frontend can
    populate a single editable box and the retriever can build veto terms per
    type (Title Anchors + Table Features sections):
        {"CLINS": "...", "FE": "...", "CE": "..."}
    """
    try:
        queries = load_queries_from_files()
        return {"ok": True, "queries": queries}
    except Exception as e:
        logger.exception("Failed to load queries")
        raise HTTPException(500, str(e))


@app.post("/queries/save")
async def save_queries(req: QueriesSaveRequest):
    """Save the unified query lexicon to queries/query.txt.

    Body (JSON):
        queries: {"CLINS": "...", "FE": "...", "CE": "..."}  (all identical —
                  the UI edits one merged file and sends it for every type)
    """
    try:
        # Validate that all keys are known report types
        for rt in req.queries:
            if rt not in REPORT_TYPES:
                raise HTTPException(
                    400,
                    f"Invalid report_type '{rt}'. Must be one of {REPORT_TYPES}",
                )
        save_queries_to_files(req.queries)
        reset_veto_terms()   # freshly saved query.txt takes effect immediately
        return {"ok": True, "saved": list(req.queries.keys())}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to save queries")
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Dossier retrieval — search a target folder, then preprocess selected files
# ---------------------------------------------------------------------------

SEARCH_EXTS = {".pdf", ".pptx", ".docx", ".xlsx"}


def _cutoff_datetime(years: int, months: int) -> datetime:
    """A datetime ``years`` years + ``months`` months before now.

    Used by the optional last-modified filter. Computed by rolling the calendar
    back (clamping the day to the target month's length) rather than a naive
    day-count approximation, so "1 year 0 months" is exact.
    """
    now = datetime.now()
    total_months = max(0, int(years) * 12 + int(months))
    y = now.year - total_months // 12
    m = now.month - (total_months % 12)
    while m <= 0:
        y -= 1
        m += 12
    last = calendar.monthrange(y, m)[1]
    d = min(now.day, last)
    return now.replace(year=y, month=m, day=d)


@app.post("/search")
async def search_files(req: SearchRequest):
    """Search a target folder (recursively, incl. subfolders) for dossiers.

    Matches file NAME against 1–3 keywords with OR logic (case-insensitive
    substring). When ``modified_enabled`` is true, only files whose
    last-modified time is within the requested window are returned.
    """
    target = Path(req.target_path).expanduser()
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, f"Folder not found: {req.target_path}")

    keywords = [k.strip().lower() for k in (req.keywords or []) if k and k.strip()]
    cutoff = None
    # Apply the window only when the filter is enabled AND a non-zero span is
    # requested. A user setting 0 years + 0 months (with the filter on) almost
    # certainly means "no date limit" rather than "modified within 0 days", so
    # treat that case as no filter to avoid a confusingly empty result.
    if req.modified_enabled and (int(req.modified_years) or int(req.modified_months)):
        try:
            cutoff = _cutoff_datetime(req.modified_years, req.modified_months)
        except Exception:
            cutoff = None

    files = []
    for p in target.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in SEARCH_EXTS:
            continue
        if _is_junk_filename(p.name):
            continue
        name_l = p.name.lower()
        if keywords and not any(kw in name_l for kw in keywords):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        mtime = datetime.fromtimestamp(st.st_mtime)
        if cutoff and mtime < cutoff:
            continue
        files.append({
            "path": str(p),
            "name": p.name,
            "ext": ext.lstrip(".").upper(),
            "size_kb": round(st.st_size / 1024, 1),
            "modified": mtime.strftime("%Y-%m-%d %H:%M"),
        })
    files.sort(key=lambda f: f["name"].lower())
    # The target path is user data — never write it to the server log.
    return {"ok": True, "count": len(files), "files": files}


@app.post("/retrieve/start")
async def retrieve_start_endpoint(req: RetrieveStartRequest):
    """Copy selected files into retrieved/<project_name>/ and start the
    pipeline on that cache folder. Returns the resolved project name."""
    out = retrieve_start(req.project_name, req.files)
    if not out.get("ok"):
        raise HTTPException(400, out.get("detail", "could not start retrieval"))
    return out


@app.get("/retrieve/status")
async def retrieve_status_endpoint():
    """Progress of the retrieval preprocessing run (stage tracker data)."""
    return {"ok": True, **retrieve_status()}


@app.get("/activity")
async def activity(since: int = 0):
    """Incremental activity feed for the frontend log (id > since)."""
    return {"ok": True, **get_events(since)}


# ---------------------------------------------------------------------------
# HTML → PPTX  (conversion runs in the browser; the server only persists bytes)
#
# html-to-pptx walks a *rendered* DOM (getComputedStyle / getBoundingClientRect),
# so it cannot run under a Python HTML parser. The frontend renders the pasted
# markup in an off-screen iframe, converts it there, and POSTs the base64 pptx
# here so it can land in a user-chosen folder (a plain browser download cannot
# target a specific directory).
# ---------------------------------------------------------------------------

# Hard cap on an accepted upload. A pptx of slide-deck size is far below this;
# the limit exists so a malformed/hostile payload cannot exhaust memory or disk.
MAX_PPTX_BYTES = 80 * 1024 * 1024      # 80 MB decoded
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# PPTX is a ZIP: every valid file starts with the local-file-header magic "PK\x03\x04".
_ZIP_MAGIC = b"PK\x03\x04"


def _safe_pptx_filename(raw: str) -> str:
    """Reduce user input to a bare, safe ``*.pptx`` file name.

    Strips any directory component (defeats ``../`` traversal and absolute
    paths), removes characters Windows rejects, trims trailing dots/spaces,
    caps the length, and guarantees the .pptx extension.
    """
    name = (raw or "").strip()
    # Kill both separators regardless of host OS, then take the last segment.
    name = name.replace("\\", "/").split("/")[-1]
    if name.lower().endswith(".pptx"):
        name = name[:-5]
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip().strip(".")
    if not name or set(name) <= {"."}:
        name = "presentation"
    return name[:120] + ".pptx"


def _unique_path(directory: Path, filename: str) -> Path:
    """``dir/name.pptx`` → first free ``dir/name (n).pptx``."""
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(2, 1000):
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(500, "Could not find a free filename in the output folder")


@app.get("/config/pptx-output")
async def get_pptx_output_config():
    """Return the folder generated PPTX files are written to.

    ``is_default`` tells the frontend the value is the system Downloads folder
    (a suggestion), not something the user explicitly saved.
    """
    current = get_pptx_output_dir()
    default = default_pptx_output_dir()
    return {
        "ok": True,
        "path": current,
        "default_path": default,
        "is_default": current == default,
        "exists": Path(current).is_dir(),
    }


@app.post("/config/pptx-output")
async def set_pptx_output_config(req: PptxOutputRequest):
    """Persist the folder generated PPTX files are written to."""
    raw = (req.path or "").strip()
    if not raw:
        raise HTTPException(400, "path is required")
    folder = Path(raw).expanduser()
    if folder.exists() and not folder.is_dir():
        raise HTTPException(400, "path exists but is not a folder")
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Cannot create folder: {e}")
    saved = set_pptx_output_dir(str(folder))
    # The path itself is user data — log only that a change happened.
    logger.info("PPTX output folder updated")
    return {"ok": True, "path": saved}


@app.post("/html2pptx/save")
async def html2pptx_save(req: Html2PptxSaveRequest):
    """Write a browser-generated PPTX to the configured output folder.

    Body (JSON):
        filename:    str  — base name; sanitised, ".pptx" enforced
        data_base64: str  — pptx bytes, bare base64 or a data: URI
        output_dir:  str | null — one-off folder override
        overwrite:   bool — False (default) auto-suffixes "name (2).pptx"

    Returns {"ok", "path", "filename", "size_kb"}.
    """
    payload = (req.data_base64 or "").strip()
    if not payload:
        raise HTTPException(400, "data_base64 is required")
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    payload = "".join(payload.split())          # drop any embedded whitespace/newlines

    # Reject before decoding: base64 inflates 4→3, so this bounds decoded size.
    if len(payload) > (MAX_PPTX_BYTES // 3) * 4 + 8:
        raise HTTPException(413, "PPTX payload too large")

    try:
        blob = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "data_base64 is not valid base64")
    if not blob:
        raise HTTPException(400, "Decoded PPTX is empty")
    if len(blob) > MAX_PPTX_BYTES:
        raise HTTPException(413, "PPTX payload too large")
    if not blob.startswith(_ZIP_MAGIC):
        # A pptx is an OOXML zip; anything else means the browser handed us junk.
        raise HTTPException(400, "Payload is not a valid PPTX (bad ZIP header)")

    folder = Path((req.output_dir or "").strip() or get_pptx_output_dir()).expanduser()
    if folder.exists() and not folder.is_dir():
        raise HTTPException(400, "Output path exists but is not a folder")
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Cannot create output folder: {e}")

    filename = _safe_pptx_filename(req.filename)
    target = folder / filename if req.overwrite else _unique_path(folder, filename)

    try:
        target.write_bytes(blob)
    except OSError as e:
        logger.exception("Failed to write PPTX")
        raise HTTPException(500, f"Could not write file: {e}")

    size_kb = round(len(blob) / 1024, 1)
    # Do not log the folder path (user data) — only the file name and size.
    logger.info(f"HTML→PPTX saved: {target.name} ({size_kb} KB)")
    return {
        "ok": True,
        "path": str(target),
        "filename": target.name,
        "size_kb": size_kb,
    }


# ---------------------------------------------------------------------------

