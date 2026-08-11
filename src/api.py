"""
FastAPI server — Dossier_Management Document Pipeline

Endpoints:
  GET  /config/listen-folder  — Read the user-configured watch folder
  POST /config/listen-folder  — Save the user-configured watch folder
  GET  /browse-folders        — Directory picker backend (returns subfolders)
  POST /project/scan     — Scan <listen>/<name>/ (or PROJECT_ROOT/<name>/) for dossiers
  POST /classify         — Auto-classify dossiers in the project folder
  POST /classify/confirm — Apply final type decisions (move files)
  GET  /classify/profiles     — Read type profiles from classify/*.txt
  POST /classify/profiles/save — Save type profiles to classify/*.txt
  POST /ingest           — Trigger ingest (reads <project>/{CLINS,FE,CE}/)
  POST /package          — Trigger package → generate synthesis PDF
  POST /run              — One-click: ingest + package
  GET  /status           — Index stats
  GET  /download/{pid}  — Download the output PDF
  GET  /queries          — Read per-type query texts from queries/*.txt
  POST /queries/save     — Save per-type query texts to queries/*.txt
  POST /reset            — Reset project (index + screenshots only)
  POST /clear-reset      — Safe reset: index + screenshots + output PDF only
  POST /run-all          — One-click: full chain for ALL project folders,
                           export PDFs to <listen>/Dossier_condensed/
  GET  /run-all/status   — Progress of the one-click run (stage tracker)
  GET  /activity         — Incremental activity feed for the frontend log
  GET  /watch            — Auto-watch state
  POST /watch            — Enable/disable the listen-folder watcher
  GET  /html2pptx        — Serves the HTML → PPTX converter page
  GET  /config/pptx-output  — Read the PPTX output folder (default: Downloads)
  POST /config/pptx-output  — Save the PPTX output folder
  POST /html2pptx/save   — Write a browser-generated PPTX (base64) to disk
  GET  /                — Serves the frontend UI
"""

import base64
import binascii
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
    OUTPUT_DIR,
    PROJECT_ROOT,
    REPORT_TYPES,
    SCREENSHOTS_DIR,
    default_pptx_output_dir,
    delete_listen_folder,
    get_listen_folders,
    get_listen_folder,
    get_pptx_output_dir,
    get_drop_decorative_image,
    set_drop_decorative_image,
    set_pptx_output_dir,
    project_data_dir,
    set_listen_folder,
    NOISE_CATEGORY_LABELS,
    DROP_DECORATIVE_IMAGE,
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
from .retriever import list_veto_terms
from .converter import _is_junk_filename
from .page_index import delete_index, index_exists
from .orchestrator import (
    CONDENSED_DIR_NAME,
    get_events,
    run_all_start,
    run_all_status,
    watcher,
)

logger = get_logger("api")


def _is_dossier_ext(name: str) -> bool:
    """True for the document extensions the pipeline accepts."""
    return Path(name).suffix.lower() in (".pdf", ".pptx", ".docx")


app = FastAPI(title="Dossier_Management Document Pipeline", version="2.0")

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
    queries: Optional[dict[str, str]] = None
    top_n: Optional[int] = None  # cap of pages PER report type; -1 = All (no cap); None = config default
    project_owner: str = ""  # name of the project owner, shown on the PDF cover
    target_formula: str = ""  # final target formula; baked into the PDF cover
    delete_floor: Optional[float] = None  # deletion floor override; None = config/frontend default


class RunRequest(BaseModel):
    project_id: str = "default"
    queries: Optional[dict[str, str]] = None
    top_n: Optional[int] = None  # cap of pages PER report type; -1 = All (no cap); None = config default
    project_owner: str = ""  # name of the project owner, shown on the PDF cover
    target_formula: str = ""  # final target formula; baked into the PDF cover
    delete_floor: Optional[float] = None  # deletion floor override; None = config/frontend default


class ScanRequest(BaseModel):
    project_name: str  # folder name under the listen folder (or PROJECT_ROOT) to scan for dossiers


class ParamsRequest(BaseModel):
    drop_decorative_image: Optional[bool] = None  # opt-in decorative-image dropping


class ListenFolderRequest(BaseModel):
    path: str  # absolute path of the user-configured watch folder


class WatchRequest(BaseModel):
    enabled: bool  # turn the auto-watch on/off


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
    """Serve the frontend UI."""
    index_path = PROJECT_ROOT / "static" / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>Frontend not found. Place index.html in static/</h2>")


@app.get("/html2pptx", response_class=HTMLResponse)
async def html2pptx_page():
    """Serve the HTML → PPTX converter page."""
    page = PROJECT_ROOT / "static" / "html2pptx.html"
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>html2pptx.html not found in static/</h2>", status_code=404)


# ---------------------------------------------------------------------------
# Listen-folder config + folder browser (persisted to listen_folder.txt)
# NOTE: the path value is intentionally NEVER written to logs.
# ---------------------------------------------------------------------------

def _default_listen_folder() -> str:
    """Default listen folder: %HOMEDRIVE%%HOMEPATH%/Documents.

    Used when the user has not saved any listen folder yet, so the input is
    pre-filled with a sensible per-user location.
    """
    drive = os.environ.get("HOMEDRIVE", "")
    home = os.environ.get("HOMEPATH", "")
    base = Path(drive + home) if (drive or home) else Path.home()
    return str(base / "Documents")


@app.get("/config/listen-folder")
async def get_listen_folder_config():
    """Return the user-configured watch folder (base path for projects).

    Falls back to %HOMEDRIVE%%HOMEPATH%/Documents when nothing is saved yet
    (`is_default` tells the frontend the value is a suggestion, not saved).
    """
    saved = get_listen_folder()
    if saved:
        return {"ok": True, "path": saved, "is_default": False}
    return {"ok": True, "path": _default_listen_folder(), "is_default": True}


@app.post("/config/listen-folder")
async def set_listen_folder_config(req: ListenFolderRequest):
    """Persist the user-configured watch folder to listen_folder.txt."""
    if not req.path or not req.path.strip():
        raise HTTPException(400, "path is required")
    set_listen_folder(req.path.strip())
    return {"ok": True, "path": get_listen_folder() or ""}


@app.get("/config/params")
async def get_config_params():
    """Return the user-tunable pipeline parameters.

    Noise-based deletion is always on. This endpoint exposes the active noise
    categories, the veto terms (reused from queries/*.txt), and the opt-in
    decorative-image drop flag so the frontend can render them.
    """
    noise_categories = []
    for k in NOISE_CATEGORY_LABELS:
        active = get_drop_decorative_image() if k == "decorative" else True
        noise_categories.append({
            "key": k,
            "label": NOISE_CATEGORY_LABELS.get(k, k),
            "active": active,
        })
    return {
        "ok": True,
        "noise_enabled": True,
        "noise_categories": noise_categories,
        "veto_terms": list_veto_terms(),
        "drop_decorative_image": get_drop_decorative_image(),
        "default_drop_decorative_image": bool(DROP_DECORATIVE_IMAGE),
    }


@app.post("/config/params")
async def set_config_params(req: ParamsRequest):
    """Persist a user-tunable pipeline parameter.

    Body (JSON, all optional):
        drop_decorative_image: bool — opt-in dropping of near-full-page
                                   decorative images (risky: can also catch
                                   chart screenshots). Persisted so it applies
                                   to every run, including Run Full Pipeline.
    """
    if req.drop_decorative_image is not None:
        set_drop_decorative_image(bool(req.drop_decorative_image))
    return {
        "ok": True,
        "drop_decorative_image": get_drop_decorative_image(),
        "default_drop_decorative_image": bool(DROP_DECORATIVE_IMAGE),
    }


@app.get("/config/listen-folders")
async def get_listen_folders_config():
    """Return the full ordered history of saved listen folders.

    `active` is the first entry (used as the base dir for projects). The
    frontend folder picker renders `paths` as a re-selectable history list
    with per-row delete controls.
    """
    folders = get_listen_folders()
    return {
        "ok": True,
        "paths": folders,
        "active": folders[0] if folders else "",
    }


@app.delete("/config/listen-folder")
async def delete_listen_folder_config(path: str = ""):
    """Delete a single saved listen folder from the history list.

    Query param `path` is the absolute folder to remove. The path value is
    intentionally never written to logs.
    """
    if not path or not path.strip():
        raise HTTPException(400, "path is required")
    removed = delete_listen_folder(path.strip())
    if not removed:
        raise HTTPException(404, "path not found in saved list")
    folders = get_listen_folders()
    return {
        "ok": True,
        "removed": path.strip(),
        "paths": folders,
        "active": folders[0] if folders else "",
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
    """Run package: per-type lexical match → group → screenshot → merge PDF.

    Body (JSON):
        project_id: str  (default "default")
        queries: dict[str, str] | null  (optional per-type query overrides)
    """
    pipeline = _get_pipeline(req.project_id)
    try:
        output_path = pipeline.package(
            req.queries, top_n=req.top_n,
            project_owner=req.project_owner, target_formula=req.target_formula,
            delete_floor=req.delete_floor,
        )
        return {
            "ok": True,
            "project_id": req.project_id,
            "output_file": output_path.name,
            "output_path": str(output_path),
        }
    except Exception as e:
        logger.exception("Package failed")
        raise HTTPException(500, str(e))


@app.post("/run")
async def run_pipeline(req: RunRequest):
    """One-click: ingest + package.

    Body (JSON):
        project_id: str  (default "default")
        queries: dict[str, str] | null  (optional per-type query overrides)
    """
    pipeline = _get_pipeline(req.project_id)
    try:
        n = pipeline.ingest()
        logger.info(f"Ingested {n} pages for project '{req.project_id}'")
        output_path = pipeline.package(
            req.queries, top_n=req.top_n,
            project_owner=req.project_owner, target_formula=req.target_formula,
            delete_floor=req.delete_floor,
        )
        logger.info(f"Package complete -> {output_path}")
        return {
            "ok": True,
            "project_id": req.project_id,
            "pages_ingested": n,
            "output_file": output_path.name,
            "output_path": str(output_path),
            "total_pages": pipeline.total_pages,
        }
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
    """Download the generated synthesis PDF."""
    output_path = OUTPUT_DIR / f"synthesis_input_{project_id}.pdf"
    if not output_path.exists():
        raise HTTPException(404, f"Output not found for project '{project_id}'")

    return FileResponse(
        str(output_path),
        media_type="application/pdf",
        filename=output_path.name,
    )


@app.post("/reset")
async def reset(project_id: str = "default"):
    """Reset project: clear index and screenshots."""
    pipeline = _get_pipeline(project_id)
    pipeline.reset()
    return {"ok": True, "project_id": project_id}


@app.post("/clear-reset")
async def clear_reset(project_id: str = "default"):
    """Safe per-project reset for an OneDrive-synced workspace.

    Only derived state is cleared: the page-text index, the screenshot cache,
    and the generated synthesis PDF. The user's dossier files inside the
    project folder (PROJECT_ROOT/<project_id>/) are NEVER touched — they are
    the source of truth and live in a synced directory, so deleting them would
    be destructive and surprising.
    """
    cleared = 0

    # 1) Page-text index.
    if index_exists(project_id):
        try:
            delete_index(project_id)
            cleared += 1
        except OSError as e:
            logger.warning(f"Could not delete index for {project_id}: {e}")

    # 2) Screenshot cache (global, not inside the project folder).
    if SCREENSHOTS_DIR.exists():
        for rt in REPORT_TYPES:
            rt_dir = SCREENSHOTS_DIR / rt
            if not rt_dir.exists():
                continue
            for item in list(rt_dir.iterdir()):
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                        cleared += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        cleared += 1
                except OSError as e:
                    logger.warning(f"Could not remove {item}: {e}")

    # 3) Generated synthesis PDF.
    out_pdf = OUTPUT_DIR / f"synthesis_input_{project_id}.pdf"
    if out_pdf.exists():
        try:
            out_pdf.unlink()
            cleared += 1
        except OSError as e:
            logger.warning(f"Could not delete {out_pdf}: {e}")

    logger.info(
        f"Safe reset for '{project_id}': cleared {cleared} derived item(s) "
        f"(index + screenshots + output PDF). Dossier files untouched."
    )
    return {"ok": True, "project_id": project_id, "cleared": cleared}


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
    """Read current per-type query texts from queries/*.txt files.

    Returns:
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
    """Save per-type query texts to queries/*.txt files.

    Body (JSON):
        queries: {"CLINS": "...", "FE": "...", "CE": "..."}
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
        return {"ok": True, "saved": list(req.queries.keys())}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to save queries")
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# One-click full workflow + auto-watch (orchestrator)
# ---------------------------------------------------------------------------

@app.post("/run-all")
async def run_all():
    """One-click workflow: for EVERY eligible project folder under the listen
    folder run scan -> classify -> ingest -> package, then export the PDF to
    <listen>/Dossier_condensed/. Runs in a background thread; poll
    /run-all/status for progress.
    """
    if not get_listen_folder():
        raise HTTPException(400, "No listen folder configured — save one first")
    return run_all_start()


@app.get("/run-all/status")
async def run_all_job_status():
    """Progress of the one-click run (stage tracker data)."""
    return {"ok": True, **run_all_status()}


@app.get("/activity")
async def activity(since: int = 0):
    """Incremental activity feed for the frontend log (id > since)."""
    return {"ok": True, **get_events(since)}


@app.get("/watch")
async def watch_status():
    """Current auto-watch state."""
    return {"ok": True, **watcher.status()}


@app.post("/watch")
async def watch_toggle(req: WatchRequest):
    """Enable/disable the listen-folder watcher.

    When ON, any NEW project folder dropped into the listen folder (excluding
    /Dossier_condensed) is auto-processed once its upload finishes.
    """
    try:
        if req.enabled:
            if not get_listen_folder():
                raise HTTPException(
                    400, "No listen folder configured — save one first"
                )
            watcher.start()
        else:
            watcher.stop()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Watch toggle failed")
        raise HTTPException(500, str(e))
    return {"ok": True, **watcher.status()}


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

