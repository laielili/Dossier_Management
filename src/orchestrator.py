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
from .pdf_parser import pdf_has_text
from .ocr import ocr_ensure_text

logger = get_logger("orchestrator")

# Folder (under PROJECT_ROOT) that receives finished PDFs. Excluded from
# scanning.
CONDENSED_DIR_NAME = "Dossier_condensed"

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
    ``retrieved/<name>/AI_FEED_DRAG_INTO_GPT`` — the folder the user drags into
    the downstream AI client). Otherwise fall back to the legacy layout:
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
    condense_dir: Optional[Path] = None,
) -> dict:
    """Run the full chain for ONE project folder. Returns a result dict.

    stage_cb(stage_name) is called as each stage begins (for the frontend
    stage tracker). Caller must hold / respect the processing lock.

    ``folder`` overrides the resolved project folder (defaults to
    ``project_data_dir(project_name)``). The retrieval flow passes the
    ``retrieved/<name>/`` cache folder here so the pipeline runs on the
    copied files rather than the live source tree.

    ``condense_dir`` overrides where denoised per-document PDFs are written
    (defaults to ``<PROJECT_ROOT>/Dossier_condensed/<project_name>/``). The
    retrieval flow points this at ``retrieved/<name>/AI_FEED_DRAG_INTO_GPT``
    so the deliverable is one drag-in folder away from the downstream AI client.
    """
    def stage(name: str):
        if stage_cb:
            stage_cb(name)

    folder = Path(folder) if folder is not None else project_data_dir(project_name)
    if not folder.exists():
        raise FileNotFoundError(f"Project folder not found: {project_name}")

    # -- 1) scan ------------------------------------------------------------
    stage("scan")
    top_level = [
        p.name for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in DOSSIER_EXTS
        and not _is_junk_filename(p.name)
    ]
    add_event(f"[{project_name}] scan: {len(top_level)} unclassified file(s) at top level")

    # Detect scanned / image-only PDFs (no text layer) and run the OCR pre-pass
    # so they gain a real text layer before classify/ingest. Files that already
    # have text are returned unchanged.
    image_only = [
        name for name in top_level
        if name.lower().endswith(".pdf") and not pdf_has_text(folder / name)
    ]
    ocr_failed: list[str] = []
    if image_only:
        add_event(
            f"[{project_name}] {len(image_only)} scanned/image-only PDF(s) with no "
            f"text layer — running OCR pre-pass: {', '.join(image_only)}",
            "warn",
        )
        for name in image_only:
            try:
                ocr_path = ocr_ensure_text(folder / name)
                if Path(ocr_path).resolve() != (folder / name).resolve():
                    shutil.copy2(ocr_path, folder / name)
                    add_event(
                        f"[{project_name}] OCR applied to {name} "
                        f"(text layer synthesized)",
                        "info",
                    )
            except Exception as e:
                ocr_failed.append(name)
                logger.exception(f"OCR pre-pass failed for {name}")
                add_event(
                    f"[{project_name}] OCR FAILED for {name}: {e}",
                    "error",
                )

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
        f"({len(moved)} assigned to CLINS/FE/CE, {len(unknown)} UNKNOWN "
        f"— still indexed & denoised)",
        "info",
    )
    for u in unprocessed:
        add_event(f"[{project_name}] skipped: {u['filename']} ({u['reason']})", "warn")

    # -- 3) ingest ------------------------------------------------------------
    stage("ingest")
    pipeline = DossierPipeline(project_name)
    pipeline.init()
    # ingest reads classified PDFs from the SAME folder scan/classify used
    # (retrieved/<name>/ for the retrieval flow, not project_data_dir which is a
    # different path). Without base_dir, ingest looks in the wrong place and
    # indexes 0 pages.
    n_pages = pipeline.ingest(base_dir=folder)
    add_event(f"[{project_name}] ingest: {n_pages} page(s) indexed")
    if n_pages == 0:
        if ocr_failed:
            raise RuntimeError(
                f"No pages ingested for '{project_name}': "
                f"{len(ocr_failed)} scanned/image-only file(s) could not be "
                f"OCR'd ({', '.join(ocr_failed)}). Install/repair easyocr "
                f"(pip install easyocr) or check the scan quality."
            )
        if image_only:
            # Scanned PDFs with no usable text layer — OCR was skipped
            # (disabled) or ran but produced nothing.
            raise RuntimeError(
                f"No pages ingested for '{project_name}': "
                f"{len(image_only)} scanned/image-only file(s) have no "
                f"extractable text layer ({', '.join(image_only)}). The "
                f"pipeline needs OCR for these — enable/repair easyocr "
                f"(pip install easyocr) or drop them from the retrieval."
            )
        raise RuntimeError(
            f"No pages ingested for '{project_name}' — nothing to package"
        )

    # -- 4) condense: denoise + write cleaned per-doc PDFs --------------------
    stage("condense")
    if condense_dir is not None:
        project_out = Path(condense_dir)
    else:
        project_out = PROJECT_ROOT / CONDENSED_DIR_NAME / project_name
    result = pipeline.condense(output_dir=project_out)
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


def _copy_selected(files: list[str], dest_dir: Path) -> list[str]:
    """Copy the user-selected source files into ``dest_dir``.

    Collision-safe: if two selected files share a base name (e.g. they came
    from different subfolders of the search target), the later one is suffixed
    ``name_2.ext`` so nothing is overwritten. Returns the list of copied paths.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src in files:
        s = Path(src)
        if not s.exists() or not s.is_file():
            add_event(f"retrieve: skipped missing file {s.name}", "warn")
            continue
        dest = dest_dir / s.name
        if dest.exists():
            stem = s.stem
            i = 1
            while (dest_dir / f"{stem}_{i}{s.suffix}").exists():
                i += 1
            dest = dest_dir / f"{stem}_{i}{s.suffix}"
        try:
            shutil.copy2(s, dest)
            copied.append(str(dest))
        except OSError as e:
            add_event(f"retrieve: could not copy {s.name}: {e}", "error")
    return copied


def retrieve_start(project_name: str, files: list[str]) -> dict:
    """Begin a retrieval: copy selected files into retrieved/<name>/ and run
    the pipeline on that folder in a background thread.

    Returns {"ok": True, "started": True, "project_name": <final name>}.
    On invalid input returns {"ok": False, "detail": ...} (no thread started).
    """
    name = (project_name or "").strip()
    if not name:
        return {"ok": False, "detail": "project_name is required"}
    if not files:
        return {"ok": False, "detail": "no files selected"}

    # Resolve a free retrieved/<name>/ — suffix on collision so a re-run with
    # the same name never clobbers a previous retrieval's cache.
    folder = RETRIEVED_DIR / name
    if folder.exists() and any(folder.iterdir()):
        i = 1
        while (RETRIEVED_DIR / f"{name}_{i}").exists() and \
                any((RETRIEVED_DIR / f"{name}_{i}").iterdir()):
            i += 1
        name = f"{name}_{i}"
        folder = RETRIEVED_DIR / name

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
    """Copy selected files, then run scan->classify->ingest->condense on the
    retrieved cache folder. Runs under the global processing lock so it never
    overlaps another pipeline pass."""
    folder = RETRIEVED_DIR / project_name
    try:
        copied = _copy_selected(files, folder)
        add_event(
            f"[{project_name}] copied {len(copied)} file(s) into "
            f"retrieved/{project_name}/",
            "info",
        )
    except Exception as e:
        logger.exception(f"Retrieve copy failed for '{project_name}'")
        add_event(f"[{project_name}] copy FAILED: {e}", "error")
        _set_retrieve(running=False, finished=True,
                      error=str(e), current_stage=None)
        return

    with _processing_lock:
        try:
            _set_retrieve(current_stage="scan")
            # Deliverable folder the user drags into the downstream AI client:
            # retrieved/<name>/AI_FEED_DRAG_INTO_GPT/
            drag_dir = RETRIEVED_DIR / project_name / "AI_FEED_DRAG_INTO_GPT"
            res = run_project_pipeline(
                project_name,
                stage_cb=lambda s: _set_retrieve(current_stage=s),
                folder=folder,
                condense_dir=drag_dir,
            )
            add_event(
                f"[{project_name}] preprocess complete — "
                f"{res['files_written']} file(s) in "
                f"retrieved/{project_name}/AI_FEED_DRAG_INTO_GPT/",
                "success",
            )
            try:
                open_condensed_folder(explicit_path=str(drag_dir))
            except Exception:
                pass
            _set_retrieve(running=False, finished=True, result=res,
                          current_stage=None)
        except Exception as e:
            logger.exception(f"Retrieve pipeline failed for '{project_name}'")
            add_event(f"[{project_name}] FAILED: {e}", "error")
            _set_retrieve(running=False, finished=True, error=str(e),
                          current_stage=None)
