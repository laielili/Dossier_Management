"""
Pipeline orchestrator — one-click full workflow + auto-watch.

Two entry points, both driving the same per-project pipeline chain:

  1) run_all_start()   — manual trigger: process every eligible project
                         folder under the active listen folder.
  2) Watcher           — background thread: when enabled, polls the listen
                         folder and auto-processes any NEW project folder
                         dropped into it (excluding /Dossier_condensed).

Per-project chain (all stages, in order):
    scan -> classify -> ingest -> condense

Condense denoises every source dossier (drops noise pages) and writes a
cleaned copy into
    <listen_folder>/Dossier_condensed/<project>/

Concurrency: a single global processing lock serializes pipeline work so a
manual run and a watcher-triggered run can never process concurrently (COM
conversion and the index are not concurrency-safe).

All progress is appended to an in-memory activity feed the frontend polls
(GET /activity). Listen-folder paths are intentionally NEVER logged to the
server log files; the activity feed shows project names only.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .config import (
    REPORT_TYPES,
    get_listen_folder,
    project_data_dir,
)
from .classifier import Classifier
from .converter import _is_junk_filename
from .logger import get_logger
from .pipeline import DossierPipeline

logger = get_logger("orchestrator")

# Folder (under the listen folder) that receives finished PDFs. Excluded
# from scanning and watching.
CONDENSED_DIR_NAME = "Dossier_condensed"

DOSSIER_EXTS = (".pdf", ".pptx", ".docx")

STAGES = ["scan", "classify", "ingest", "condense"]

# One pipeline at a time — manual run and watcher share this lock.
_processing_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Reveal output folder in the native file manager
# ---------------------------------------------------------------------------

def open_condensed_folder(project_name: str | None = None) -> None:
    """Open the OS file explorer at the condensed output folder.

    When ``project_name`` is given, reveals the per-project deliverable folder
    <listen>/Dossier_condensed/<project_name>/ (the popup after a single
    project finishes). When omitted, reveals the parent Dossier_condensed/
    folder (used after a multi-project run-all, which produces many subfolders).

    Triggered when Auto-Watch is switched on and after a pipeline run finishes
    (manual run-all and watcher auto-processing). Best-effort: any failure
    (headless server, no display session) is logged and swallowed so it never
    breaks the pipeline. The folder is created if it does not exist yet.
    """
    base = get_listen_folder()
    if not base:
        return
    condensed = Path(base) / CONDENSED_DIR_NAME
    target = condensed / project_name if project_name else condensed
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"open_condensed_folder: cannot create {target}: {e}")
        return
    _open_in_explorer(target)
    label = f"{CONDENSED_DIR_NAME}/{project_name}" if project_name else CONDENSED_DIR_NAME
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


# ---------------------------------------------------------------------------
# Project folder discovery
# ---------------------------------------------------------------------------

def _has_dossier_files(folder: Path) -> bool:
    """True if the folder holds dossier files (top level or in type subdirs)."""
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in DOSSIER_EXTS \
                    and not _is_junk_filename(p.name):
                return True
        for rt in REPORT_TYPES:
            sub = folder / rt
            if sub.is_dir():
                for p in sub.iterdir():
                    if p.is_file() and p.suffix.lower() == ".pdf" \
                            and not _is_junk_filename(p.name):
                        return True
    except OSError:
        pass
    return False


def list_project_folders(base: Path) -> list[str]:
    """Eligible project folder names under the listen folder.

    Excludes /Dossier_condensed, hidden/system folders, and folders that
    contain no dossier files at all.
    """
    names: list[str] = []
    if not base.exists():
        return names
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        if p.name == CONDENSED_DIR_NAME:
            continue
        if p.name.startswith(".") or p.name.startswith("~"):
            continue
        if _has_dossier_files(p):
            names.append(p.name)
    return names


# ---------------------------------------------------------------------------
# Per-project pipeline chain
# ---------------------------------------------------------------------------

def run_project_pipeline(project_name: str, stage_cb=None) -> dict:
    """Run the full chain for ONE project folder. Returns a result dict.

    stage_cb(stage_name) is called as each stage begins (for the frontend
    stage tracker). Caller must hold / respect the processing lock.
    """
    def stage(name: str):
        if stage_cb:
            stage_cb(name)

    folder = project_data_dir(project_name)
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
        f"({len(moved)} moved, {len(unknown)} UNKNOWN left in place)",
        "warn" if unknown else "info",
    )
    for u in unprocessed:
        add_event(f"[{project_name}] skipped: {u['filename']} ({u['reason']})", "warn")

    # -- 3) ingest ------------------------------------------------------------
    stage("ingest")
    pipeline = DossierPipeline(project_name)
    pipeline.init()
    n_pages = pipeline.ingest()
    add_event(f"[{project_name}] ingest: {n_pages} page(s) indexed")
    if n_pages == 0:
        raise RuntimeError(
            f"No pages ingested for '{project_name}' — nothing to package"
        )

    # -- 4) condense: denoise + write cleaned per-doc PDFs --------------------
    stage("condense")
    base = get_listen_folder()
    if not base:
        raise RuntimeError("No listen folder configured — cannot export")
    condensed = Path(base) / CONDENSED_DIR_NAME
    condensed.mkdir(parents=True, exist_ok=True)
    project_out = condensed / project_name
    result = pipeline.condense(output_dir=project_out)
    add_event(
        f"[{project_name}] condense: {result['sources_processed']} source(s), "
        f"{result['pages_dropped']} noise page(s) dropped -> "
        f"{CONDENSED_DIR_NAME}/{project_name}/",
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
# Manual one-click run (all projects), background job
# ---------------------------------------------------------------------------

_job_lock = threading.Lock()
_job: dict = {
    "running": False,
    "projects": [],          # ordered project names in this run
    "current_project": None,
    "current_stage": None,   # one of STAGES
    "done": [],              # finished project names
    "results": [],
    "errors": [],            # [{project, error}]
    "finished": True,
}


def run_all_status() -> dict:
    with _job_lock:
        return dict(_job)


def _set_job(**kw) -> None:
    with _job_lock:
        _job.update(kw)


def _run_all_worker() -> None:
    base = get_listen_folder()
    if not base:
        add_event("Run aborted: no listen folder configured", "error")
        _set_job(running=False, finished=True)
        return
    projects = list_project_folders(Path(base))
    _set_job(projects=projects, done=[], results=[], errors=[])
    if not projects:
        add_event("No eligible project folders found in the listen folder", "warn")
        _set_job(running=False, finished=True,
                 current_project=None, current_stage=None)
        return

    add_event(
        f"Full pipeline started for {len(projects)} project(s): "
        + ", ".join(projects)
    )
    with _processing_lock:
        for name in projects:
            _set_job(current_project=name, current_stage="scan")
            try:
                res = run_project_pipeline(
                    name,
                    stage_cb=lambda s: _set_job(current_stage=s),
                )
                with _job_lock:
                    _job["results"].append(res)
                    _job["done"].append(name)
            except Exception as e:  # keep going with remaining projects
                logger.exception(f"Pipeline failed for project '{name}'")
                add_event(f"[{name}] FAILED: {e}", "error")
                with _job_lock:
                    _job["errors"].append({"project": name, "error": str(e)})
                    _job["done"].append(name)

    ok = len(_job["results"])
    bad = len(_job["errors"])
    add_event(
        f"Full pipeline finished: {ok} succeeded, {bad} failed. "
        f"PDFs in {CONDENSED_DIR_NAME}/",
        "success" if bad == 0 else "warn",
    )
    # Reveal the output folder so the user can drag the finished (denoised)
    # files into a downstream AI client. For a run-all we open the parent
    # Dossier_condensed/ (it now holds one subfolder per project).
    open_condensed_folder()
    _set_job(running=False, finished=True,
             current_project=None, current_stage=None)


def run_all_start() -> dict:
    """Start the one-click run in a background thread (if idle)."""
    with _job_lock:
        if _job["running"]:
            return {"ok": False, "detail": "A run is already in progress"}
        _job.update(
            running=True, finished=False,
            projects=[], done=[], results=[], errors=[],
            current_project=None, current_stage=None,
        )
    t = threading.Thread(target=_run_all_worker, name="run-all", daemon=True)
    t.start()
    return {"ok": True, "started": True}


# ---------------------------------------------------------------------------
# Auto-watch (background polling thread)
# ---------------------------------------------------------------------------

WATCH_POLL_SECONDS = 5        # listen-folder poll interval
STABLE_CHECK_SECONDS = 4      # gap between stability snapshots
STABLE_CHECKS_REQUIRED = 2    # consecutive identical snapshots => stable


def _folder_snapshot(folder: Path) -> tuple:
    """(file count, total bytes) over the whole subtree — copy-progress probe."""
    count = 0
    total = 0
    try:
        for p in folder.rglob("*"):
            if p.is_file():
                count += 1
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return (count, total)


class Watcher:
    """Polls the listen folder; auto-runs the pipeline on NEW project folders.

    - Folders existing when the watch is enabled are treated as known (not
      auto-processed) — only folders that APPEAR while watching trigger runs.
    - /Dossier_condensed is always excluded.
    - A new folder is processed only after its contents stop changing
      (stability probe), so half-copied uploads are never ingested.
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.enabled = False

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self.enabled:
                return
            base = get_listen_folder()
            if not base:
                raise RuntimeError("No listen folder configured")
            self.enabled = True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="folder-watcher", daemon=True
            )
            self._thread.start()
        add_event(
            f"Auto-watch ENABLED (new project folders trigger the pipeline; "
            f"/{CONDENSED_DIR_NAME} excluded)",
            "success",
        )

    def stop(self) -> None:
        with self._lock:
            if not self.enabled:
                return
            self.enabled = False
            self._stop.set()
        add_event("Auto-watch disabled", "info")

    def status(self) -> dict:
        return {"enabled": self.enabled}

    # -- internals ---------------------------------------------------------

    def _known_dirs(self, base: Path) -> set[str]:
        try:
            return {
                p.name for p in base.iterdir()
                if p.is_dir()
                and p.name != CONDENSED_DIR_NAME
                and not p.name.startswith(".")
                and not p.name.startswith("~")
            }
        except OSError:
            return set()

    def _loop(self) -> None:
        base_str = get_listen_folder()
        if not base_str:
            self.enabled = False
            return
        base = Path(base_str)
        known = self._known_dirs(base)
        logger.info(f"Watcher started ({len(known)} existing folder(s) marked known)")

        while not self._stop.wait(WATCH_POLL_SECONDS):
            # Follow listen-folder changes made while watching.
            current_base = get_listen_folder()
            if current_base and current_base != base_str:
                base_str = current_base
                base = Path(base_str)
                known = self._known_dirs(base)
                add_event("Auto-watch: listen folder changed — baseline reset", "warn")
                continue

            now = self._known_dirs(base)
            new_dirs = sorted(now - known)
            known = now
            for name in new_dirs:
                if self._stop.is_set():
                    return
                add_event(f"New project folder detected: {name}", "warn")
                self._process_new_folder(base / name)

        logger.info("Watcher stopped")

    def _wait_until_stable(self, folder: Path) -> bool:
        """Block until the folder's contents stop changing. False if aborted."""
        prev = _folder_snapshot(folder)
        stable = 0
        while stable < STABLE_CHECKS_REQUIRED:
            if self._stop.wait(STABLE_CHECK_SECONDS):
                return False
            cur = _folder_snapshot(folder)
            stable = stable + 1 if cur == prev else 0
            prev = cur
        return True

    def _process_new_folder(self, folder: Path) -> None:
        if not self._wait_until_stable(folder):
            return
        if not _has_dossier_files(folder):
            add_event(
                f"[{folder.name}] ignored: no dossier files found inside", "warn"
            )
            return
        add_event(f"[{folder.name}] upload complete — pipeline triggered")
        with _processing_lock:
            try:
                run_project_pipeline(folder.name)
                add_event(
                    f"[{folder.name}] pipeline complete — output in "
                    f"{CONDENSED_DIR_NAME}/{folder.name}/",
                    "success",
                )
                open_condensed_folder(folder.name)
            except Exception as e:
                logger.exception(f"Watcher pipeline failed for '{folder.name}'")
                add_event(f"[{folder.name}] FAILED: {e}", "error")


# Module-level singleton used by the API.
watcher = Watcher()
