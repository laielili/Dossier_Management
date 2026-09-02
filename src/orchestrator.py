"""
Pipeline orchestrator — per-project dossier pipeline + retrieval flow.

Entry point (used by the retrieval UI):
  retrieve_start()  — copy selected source files into retrieved/<name>/ and
                      run the full per-project chain on that cache folder.

Per-project chain (all stages, in order):
    scan -> classify -> ingest -> condense

Condense denoises every source dossier (drops noise pages) and writes a
cleaned copy into
    <PROJECT_ROOT>/Dossier_condensed/<project>/

Concurrency: a single global processing lock serializes pipeline work (COM
conversion and the index are not concurrency-safe), so a retrieval run and any
other pipeline pass never overlap.

All progress is appended to an in-memory activity feed the frontend polls
(GET /activity). Project names only are shown in the feed.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .config import (
    PROJECT_ROOT,
    REPORT_TYPES,
    RETRIEVED_DIR,
    project_data_dir,
)
from .classifier import Classifier
from .converter import _is_junk_filename
from .logger import get_logger
from .pipeline import DossierPipeline

logger = get_logger("orchestrator")

# Folder (under PROJECT_ROOT) that receives finished PDFs. Excluded from
# scanning.
CONDENSED_DIR_NAME = "Dossier_condensed"

# Deliverable root the user drags into the downstream AI client. The pipeline
# writes the typed category folders (CLINS/FE/CE) one level deeper, under a
# folder named after the project, so the layout is:
#   retrieved/<name>/AI_feed/<name>/{CLINS,FE,CE}/...
# The file explorer pops open at retrieved/<name>/AI_feed/ so the user sees a
# single <name> folder to drag wholesale into the downstream AI.
AI_FEED_DIR_NAME = "AI_feed"

DOSSIER_EXTS = (".pdf", ".pptx", ".docx")

STAGES = ["scan", "classify", "ingest", "condense"]

# One pipeline at a time — pipeline passes share this lock.
_processing_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Reveal output folder in the native file manager
# ---------------------------------------------------------------------------

def open_condensed_folder(
    project_name: str | None = None,
    explicit_path: str | None = None,
) -> None:
    """Open the OS file explorer at the deliverable folder.

    Prefer ``explicit_path`` when given (the retrieval flow uses it to reveal
    ``retrieved/<name>/AI_feed`` — the folder the user drags into the
    downstream AI client; inside it lives a single ``<name>`` folder holding the
    typed category subfolders). Otherwise fall back to the legacy layout:
    <PROJECT_ROOT>/Dossier_condensed/<project_name>/ (or the parent folder when
    ``project_name`` is omitted).

    Triggered after a pipeline run finishes (retrieval preprocess). Best-effort:
    any failure (headless server, no display session) is logged and swallowed
    so it never breaks the pipeline. The folder is created if it does not exist
    yet.
    """
    if explicit_path:
        target = Path(explicit_path)
        label = explicit_path
    else:
        condensed = PROJECT_ROOT / CONDENSED_DIR_NAME
        target = condensed / project_name if project_name else condensed
        label = f"{CONDENSED_DIR_NAME}/{project_name}" if project_name else CONDENSED_DIR_NAME
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"open_condensed_folder: cannot create {target}: {e}")
        return
    _open_in_explorer(target)
    add_event(f"Opened file explorer at {label}/", "success")


def _open_in_explorer(path: Path) -> None:
    """Reveal `path` in the platform file manager (cross-platform)."""
    p = str(path)
    try:
        if os.name == "nt":
            os.startfile(p)                       # opens in Explorer, no console
        elif sys.platform == "darwin":
            subprocess.run(["open", p], check=False)
        else:
            subprocess.run(["xdg-open", p], check=False)
        logger.info(f"Revealed folder in file explorer: {p}")
    except Exception as e:
        logger.warning(f"Could not open file explorer at {p}: {e}")


# ---------------------------------------------------------------------------
# Activity feed (frontend log)
# ---------------------------------------------------------------------------

_events: deque = deque(maxlen=500)
_events_lock = threading.Lock()
_event_id = 0


def add_event(message: str, level: str = "info") -> None:
    """Append one line to the activity feed the frontend polls."""
    global _event_id
    with _events_lock:
        _event_id += 1
        _events.append({
            "id": _event_id,
            "ts": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        })


def get_events(since: int = 0) -> dict:
    """Return feed entries with id > since (frontend incremental poll)."""
    with _events_lock:
        out = [e for e in _events if e["id"] > since]
        last = _event_id
    return {"events": out, "last_id": last}



def run_project_pipeline(
    project_name: str,
    stage_cb=None,
    folder: Optional[Path] = None,
    file_paths: Optional[list[Path]] = None,
    condense_dir: Optional[Path] = None,
) -> dict:
    """Run the full chain for ONE project folder or one explicit file list.

    stage_cb(stage_name) is called as each stage begins (for the frontend
    stage tracker). Caller must hold / respect the processing lock.

    Either ``folder`` OR ``file_paths`` is used:
      - ``folder``: per-project dossier folder (defaults to
        ``project_data_dir(project_name)``). Used by the legacy / CLI flow.
      - ``file_paths``: explicit list of absolute PDF paths. Used by the
        retrieval flow, which no longer copies sources into a cache folder.
        When supplied, files are read in place; classifier results are not
        applied as file moves (there is no folder to move INTO).

    ``condense_dir`` overrides where denoised per-document PDFs are written
    (defaults to ``<PROJECT_ROOT>/Dossier_condensed/<project_name>/``). The
    retrieval flow points this at ``retrieved/<name>/AI_feed/<name>``
    so the deliverable is one drag-in folder away from the downstream AI client.
    """
    def stage(name: str):
        if stage_cb:
            stage_cb(name)

    if file_paths is not None:
        return _run_project_pipeline_paths(
            project_name, stage_cb, file_paths, condense_dir,
        )

    folder = Path(folder) if folder is not None else project_data_dir(project_name)
    if not folder.exists():
        raise FileNotFoundError("Project folder not found: " + project_name)

    # -- 1) scan ------------------------------------------------------------
    stage("scan")
    top_level = [
        p.name for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in DOSSIER_EXTS
        and not _is_junk_filename(p.name)
    ]
    add_event(f"[{project_name}] scan: {len(top_level)} unclassified file(s) at top level")

    # -- 2) classify (auto-accept predicted types) ---------------------------
    stage("classify")
    classifier = Classifier(base_dir=folder)
    out = classifier.classify_inbox()
    results = out["results"]
    unprocessed = out.get("unprocessed", [])
    decisions = [
        {"filename": r["filename"], "report_type": r["report_type"]}
        for r in results if r["report_type"] in REPORT_TYPES
    ]
    moved = classifier.apply_decisions(decisions)
    unknown = [r["filename"] for r in results if r["report_type"] not in REPORT_TYPES]
    add_event(
        f"[{project_name}] classify: {len(results)} file(s) "
        f"({len(moved)} assigned dossier type, {len(unknown)} UNKNOWN "
        f"-- still processed)",
        "info",
    )
    for u in unprocessed:
        add_event(f"[{project_name}] skipped: {u['filename']} ({u['reason']})", "warn")

    # -- 3) ingest ------------------------------------------------------------
    stage("ingest")
    pipeline = DossierPipeline(project_name)
    pipeline.init()
    # ingest reads classified PDFs from the SAME folder scan/classify used.
    n_pages = pipeline.ingest(base_dir=folder)
    add_event(f"[{project_name}] ingest: {n_pages} page(s) indexed")
    if n_pages == 0:
        # Not a failure: a scanned / image-only PDF has no text layer, so no
        # text pages can be indexed (and OCR is disabled). Classification still
        # ran; the pipeline proceeds and the file is passed through unchanged.
        add_event(
            f"[{project_name}] 0 text pages indexed -- source PDF(s) appear to "
            f"have no extractable text layer (scanned / image-only). "
            f"Classification still completed; the file(s) will be passed "
            f"through to the deliverable unchanged (no text-based noise "
            f"removal is possible without OCR).",
            "warn",
        )

    # -- 4) condense: denoise + write cleaned per-doc PDFs --------------------
    stage("condense")
    if condense_dir is not None:
        project_out = Path(condense_dir)
    else:
        project_out = PROJECT_ROOT / CONDENSED_DIR_NAME / project_name
    result = pipeline.condense(output_dir=project_out, source_dir=folder)
    add_event(
        f"[{project_name}] condense: {result['sources_processed']} source(s), "
        f"{result['pages_dropped']} noise page(s) dropped -> "
        f"{project_out}/",
        "success",
    )

    return {
        "project": project_name,
        "files_classified": len(results),
        "unknown": unknown,
        "pages_ingested": n_pages,
        "output_dir": str(project_out),
        "files_written": result["files_written"],
    }


def _run_project_pipeline_paths(
    project_name: str,
    stage_cb,
    file_paths: list[Path],
    condense_dir: Optional[Path],
) -> dict:
    """Run the full chain on an explicit list of source paths.

    No source copies, no folder globbing, no file moves: the user-selected
    absolute paths are read in place. Classifier results are reported back
    but not applied as archive moves (there is no per-project folder to
    move INTO under the retrieval flow).
    """
    def stage(name: str):
        if stage_cb:
            stage_cb(name)

    # -- 1) scan ------------------------------------------------------------
    stage("scan")
    valid = [p for p in file_paths if p.exists() and p.is_file()]
    add_event(f"[{project_name}] scan: {len(valid)} selected file(s)")

    # -- 2) classify (no auto-archive; report only) --------------------------
    stage("classify")
    classifier = Classifier()
    out = classifier.classify_paths(valid)
    results = out["results"]
    unprocessed = out.get("unprocessed", [])
    unknown = [r["filename"] for r in results if r["report_type"] not in REPORT_TYPES]
    add_event(
        f"[{project_name}] classify: {len(results)} file(s) "
        f"({len(results) - len(unknown)} assigned dossier type, "
        f"{len(unknown)} UNKNOWN -- still processed)",
        "info",
    )
    for u in unprocessed:
        add_event(f"[{project_name}] skipped: {u['filename']} ({u['reason']})", "warn")

    # -- 3) ingest (in place) ----------------------------------------------
    stage("ingest")
    pipeline = DossierPipeline(project_name)
    pipeline.init()
    n_pages = pipeline.ingest_paths(valid)
    add_event(f"[{project_name}] ingest: {n_pages} page(s) indexed")
    if n_pages == 0:
        add_event(
            f"[{project_name}] 0 text pages indexed -- source PDF(s) appear "
            f"to have no extractable text layer (scanned / image-only). "
            f"Classification still completed; the file(s) will be passed "
            f"through to the deliverable unchanged (no text-based noise "
            f"removal is possible without OCR).",
            "warn",
        )

    # -- 4) condense (in place, write to deliverable) ----------------------
    stage("condense")
    if condense_dir is not None:
        project_out = Path(condense_dir)
    else:
        project_out = PROJECT_ROOT / CONDENSED_DIR_NAME / project_name
    result = pipeline.condense_paths(output_dir=project_out, file_paths=valid)
    add_event(
        f"[{project_name}] condense: {result['sources_processed']} source(s), "
        f"{result['pages_dropped']} noise page(s) dropped -> "
        f"{project_out}/",
        "success",
    )

    return {
        "project": project_name,
        "files_classified": len(results),
        "unknown": unknown,
        "pages_ingested": n_pages,
        "output_dir": str(project_out),
        "files_written": result["files_written"],
    }



# ---------------------------------------------------------------------------
# Retrieval flow — search -> select -> copy into retrieved/<name>/ -> pipeline
# ---------------------------------------------------------------------------

_retrieve_lock = threading.Lock()
_retrieve_job: dict = {
    "running": False,
    "finished": True,
    "project_name": None,
    "current_stage": None,
    "result": None,
    "error": None,
}


def retrieve_status() -> dict:
    with _retrieve_lock:
        return dict(_retrieve_job)


def _set_retrieve(**kw) -> None:
    with _retrieve_lock:
        _retrieve_job.update(kw)





def retrieve_start(project_name: str, files: list[str]) -> dict:
    """Begin a retrieval: run the pipeline on the user-selected source files in
    a background thread.

    No source files are copied. The pipeline reads from the absolute paths the
    user submitted and writes the processed deliverable to
    ``retrieved/<name>/AI_feed/<name>/{CLINS,FE,CE}/`` — there is no longer a
    raw-source cache folder.

    Returns {"ok": True, "started": True, "project_name": <final name>}.
    On invalid input returns {"ok": False, "detail": ...} (no thread started).
    """
    name = (project_name or "").strip()
    if not name:
        return {"ok": False, "detail": "project_name is required"}
    if not files:
        return {"ok": False, "detail": "no files selected"}

    # Resolve a free retrieved/<name>/ — suffix on collision so a re-run with
    # the same name never clobbers a previous retrieval's deliverable. (We keep
    # the deliverable under retrieved/<name>/AI_feed/<name>/ but also need a
    # distinct parent so two retrievals with the same name don't merge.)
    parent = RETRIEVED_DIR / name
    if parent.exists() and any(parent.iterdir()):
        i = 1
        while (RETRIEVED_DIR / f"{name}_{i}").exists() and \
                any((RETRIEVED_DIR / f"{name}_{i}").iterdir()):
            i += 1
        name = f"{name}_{i}"

    with _retrieve_lock:
        if _retrieve_job["running"]:
            return {"ok": False, "detail": "A retrieval is already running"}
        _retrieve_job.update(
            running=True, finished=False,
            project_name=name, current_stage=None,
            result=None, error=None,
        )

    t = threading.Thread(
        target=_retrieve_worker,
        args=(name, files),
        name="retrieve", daemon=True,
    )
    t.start()
    return {"ok": True, "started": True, "project_name": name}


def _retrieve_worker(project_name: str, files: list[str]) -> None:
    """Run scan->classify->ingest->condense on the user-selected source files
    in place (no copies). Runs under the global processing lock so it never
    overlaps another pipeline pass.
    """
    # Validate the selected paths up front so we can fail fast before grabbing
    # the processing lock.
    src_paths: list[Path] = []
    for f in files:
        p = Path(f)
        if not p.exists() or not p.is_file():
            add_event(f"retrieve: skipped missing file {p.name}", "warn")
            continue
        src_paths.append(p)
    if not src_paths:
        _set_retrieve(running=False, finished=True,
                      error="no valid selected files", current_stage=None)
        return

    add_event(
        f"[{project_name}] preprocessing {len(src_paths)} file(s) in place "
        f"(no source copies)",
        "info",
    )

    with _processing_lock:
        try:
            _set_retrieve(current_stage="scan")
            # Deliverable root the user drags into the downstream AI client:
            #   retrieved/<name>/AI_feed/                        (explorer opens here)
            #   retrieved/<name>/AI_feed/<name>/                 (condense output root)
            #   retrieved/<name>/AI_feed/<name>/{CLINS,FE,CE}/   (typed category folders)
            ai_feed_dir = RETRIEVED_DIR / project_name / AI_FEED_DIR_NAME
            drag_dir = ai_feed_dir / project_name
            res = run_project_pipeline(
                project_name,
                stage_cb=lambda s: _set_retrieve(current_stage=s),
                file_paths=src_paths,
                condense_dir=drag_dir,
            )
            add_event(
                f"[{project_name}] preprocess complete — "
                f"{res['files_written']} file(s) in "
                f"retrieved/{project_name}/{AI_FEED_DIR_NAME}/{project_name}/",
                "success",
            )
            try:
                # Open the explorer one level up so the user sees a single
                # <project_name> folder and drags that whole folder in.
                open_condensed_folder(explicit_path=str(ai_feed_dir))
            except Exception:
                pass
            _set_retrieve(running=False, finished=True, result=res,
                          current_stage=None)
        except Exception as e:
            logger.exception(f"Retrieve pipeline failed for '{project_name}'")
            add_event(f"[{project_name}] FAILED: {e}", "error")
            _set_retrieve(running=False, finished=True, error=str(e),
                          current_stage=None)
