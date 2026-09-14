#!/usr/bin/env python
"""Rebuild dist/dossier/dossier.exe in one command.

Run from the repository root:

    venv/Scripts/python.exe build/rebuild.py

which leaves:

    dist/dossier/
        dossier.exe             the application
        _internal/              read-only bundle (interpreter + runtime assets)
        Installation_SOP.docx   companion document, staged by this script

Several things bite when rebuilding on this machine, and this script handles
all of them:

  1. Build-only dependencies. PyInstaller lives in requirements-build.txt,
     NOT requirements.txt, so a checkout that only ran the runtime install
     has no builder available.

  2. A locked dist/dossier. If the app is still running its exe holds the
     folder open, and the rename aside fails. But PyInstaller's own failure
     mode here is nastier than it looks, so the rename is done with
     os.rename (never shutil.move, which silently degrades to a full copy
     plus a bulk delete). preflight_dist() surfaces the lock in about a
     second, before any build time is spent.

  3. Two delete points inside PyInstaller. Before analysing, it wipes its
     work directory (build/_pyi/<name>/, thousands of files); before COLLECT
     it wipes the previous dist/dossier. Where a delete guard rewrites bulk
     removals into a recycle-bin move, each wipe is interrupted and the build
     exits non-zero -- sometimes after the exe has already been linked, which
     makes it look like a phantom failure. Both folders are therefore renamed
     aside first, and if the build still fails it retries once with a fresh
     work directory.

  4. Verification. Checks that the exe and every runtime asset actually
     landed, that the icon is really inside the exe, and runs the frozen-root
     fixture first, before spending time on a build that would be broken
     anyway.

  5. Companion documents. Installation_SOP.docx ships beside dossier.exe.
     PyInstaller's OneFolder layout would put it inside the read-only
     _internal/ folder if it were declared as data, so this script copies it
     in after the build, and refuses to finish when it is missing, empty or
     the wrong size. Add more files to SHIPPED_DOCS to extend the set.

  The icon is not a special case of anything above: it is a generated
  artifact (app-icon.svg -> build/app-icon.ico), and PyInstaller silently
  builds without it when the .ico is absent. This script regenerates it when
  the SVG is newer and verifies it landed, so the icon can no longer go
  missing quietly.

Flags:
    --skip-install   never call pip, even if PyInstaller is missing
    --no-verify      skip the frozen-root fixture
    --skip-icon      do not regenerate build/app-icon.ico from app-icon.svg
    --prune          delete the stashed leftovers and exit

Notes on the local delete guard, worth knowing before editing this:
  * A rename within one volume is fine. Moving to another volume is a
    copy+delete and gets intercepted, so the stash stays on this drive.
  * Deleting a whole build tree in one call always exceeds the per-turn bulk
    threshold, which is why --prune is a separate invocation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
SPEC = BUILD / "dossier.spec"
WORK = BUILD / "_pyi"
WORK_APP = WORK / "dossier"          # PyInstaller's per-target work folder
DIST = REPO / "dist"
APP = DIST / "dossier"
EXE = APP / "dossier.exe"
TRASH = REPO / "_trash"
REQ_BUILD = REPO / "requirements-build.txt"
VERIFY = TRASH / "verify_frozen_roots_20260914.py"
SOP_DOCX = REPO / "Installation_SOP.docx"   # companion document, shipped beside the exe
ICON_SVG = REPO / "app-icon.svg"          # source of truth for the app icon
ICON_ICO = BUILD / "app-icon.ico"         # generated; what dossier.spec reads
MAKE_ICON = BUILD / "make_icon.py"

# Assets the app reads at run time, relative to dist/dossier. A missing entry
# means the spec's datas list and the code's ASSET_ROOT resolution have drifted
# apart, which otherwise only shows up as a 404 or a crash far downstream.
BUNDLED = {
    "frontend page (search)": "_internal/static/search.html",
    "frontend page (svg2ppt)": "_internal/static/svg2ppt.html",
    "stylesheet": "_internal/static/style.css",
    "study-region badges": "_internal/static/study_region/_48/cn.png",
    "classify default": "_internal/classify/CLINS.txt",
    "query default": "_internal/queries/query.txt",
    "svg2ppt template": "_internal/src/svg2ppt/templates/deck_5region.json",
    "pptx default deck": "_internal/pptx/templates/default.pptx",
}

# Must live next to the exe (APP_ROOT), never inside the read-only bundle.
RUNTIME_STATE = {"logs", "data", "output", "index_projects",
                 "screenshots", "retrieved", "Dossier_condensed"}


# Written next to the exe, so the shipped folder is dossier.exe + _internal/ +
# these. They are deliberately NOT in BUNDLED: nothing imports them at run time,
# and a PyInstaller `datas` entry would bury them inside the read-only bundle.
SHIPPED_DOCS = [SOP_DOCX]


def say(tag: str, msg: str) -> None:
    print(f"[{tag:<5}] {msg}", flush=True)


def tree_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1024 / 1024


def _free_slot(prefix: str) -> Path:
    TRASH.mkdir(parents=True, exist_ok=True)
    i = 1
    while (TRASH / f"{prefix}_{i}").exists():
        i += 1
    return TRASH / f"{prefix}_{i}"


# --------------------------------------------------------------------------
# 1. builder availability
# --------------------------------------------------------------------------
def ensure_pyinstaller(skip_install: bool) -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        say("OK", f"PyInstaller {probe.stdout.strip()} ({sys.executable})")
        return True

    if skip_install:
        say("ERROR", "PyInstaller is not installed and --skip-install was given.")
        return False

    if not REQ_BUILD.exists():
        say("ERROR", f"missing {REQ_BUILD}")
        return False

    say("SETUP", f"installing build dependencies from {REQ_BUILD.name} ...")
    rc = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--disable-pip-version-check", "--no-input", "-r", str(REQ_BUILD)]
    ).returncode
    if rc != 0:
        say("ERROR", "pip install failed")
        return False
    return ensure_pyinstaller(skip_install=True)


# --------------------------------------------------------------------------
# 2. clear the delete points out of the way (and refuse to build when locked)
# --------------------------------------------------------------------------
class StashFailed(RuntimeError):
    """dist/dossier could not be moved aside - do not start a build."""


def _locked_explanation(src: Path) -> str:
    """Best-effort culprit for a rename failure, phrased as an instruction."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq dossier.exe", "/NH"],
            capture_output=True, text=True, timeout=20,
        ).stdout.lower()
    except Exception:                            # noqa: BLE001
        out = ""
    if "dossier.exe" in out:
        return ("dossier.exe is still running and holds the folder open. "
                "Close the app window (or end dossier.exe in Task Manager), "
                "then run this again.")
    return ("Something else has the folder open - an Explorer window showing "
            "it, a preview pane, an antivirus scan, or a second build. Close "
            "it and run this again.")


def preflight_dist() -> bool:
    """Rename dist/dossier aside and straight back, before spending any time.

    On this machine that rename is the single operation that fails when the
    app is still running, and the cost of finding out late is a wasted build
    (or, with shutil.move, a duplicated 90 MB tree and a dead process).
    """
    if not APP.exists():
        return True
    probe = DIST / (APP.name + ".lockprobe")
    try:
        os.rename(str(APP), str(probe))
        os.rename(str(probe), str(APP))
    except OSError as e:
        say("ERROR", f"{APP.relative_to(REPO)} is locked: {e}")
        say("ERROR", _locked_explanation(APP))
        return False
    return True


def stash(*targets: tuple[Path, str]) -> list[str]:
    """Rename each existing folder aside; a rename never trips the guard.

    os.rename on purpose, NOT shutil.move: shutil falls back to copytree plus
    rmtree when the rename fails, which turns a locked folder into a duplicate
    tree followed by a bulk delete -- and the bulk delete trips this machine's
    guard, which kills the build. A same-volume rename either works instantly
    or fails for a reason worth reporting.
    """
    moved = []
    for src, prefix in targets:
        if not src.exists():
            continue
        dst = _free_slot(prefix)
        try:
            os.rename(str(src), str(dst))
            moved.append(f"{src.relative_to(REPO)} -> {TRASH.name}/{dst.name}")
        except OSError as e:
            say("ERROR", f"cannot move {src.relative_to(REPO)} aside: {e}")
            say("ERROR", _locked_explanation(src))
            raise StashFailed(str(src)) from e
    return moved


def stash_before_build(include_workdir: bool) -> list[str]:
    items = [(APP, "dist_prev")]
    if include_workdir:
        items.append((WORK_APP, "pyi_prev"))
    return stash(*items)


def stash_victims() -> list[Path]:
    """Every folder this script has parked, including ones mid-purge."""
    stage_root = TRASH / ".purge"
    found = set(TRASH.glob("dist_prev_*")) | set(TRASH.glob("pyi_prev_*"))
    if stage_root.is_dir():
        found |= {p for p in stage_root.iterdir() if p.is_dir()}
    return sorted(found)


def prune_stash() -> None:
    """Best-effort delete of every stashed folder.

    Staging stays on THIS volume on purpose. An earlier version parked each
    tree in the system temp directory, which is another volume, so
    shutil.move degraded to copytree + rmtree; when the rmtree was refused,
    the copy survived -- 92 MB of orphan duplicate per attempt (277 MB in
    %TEMP% after three). A same-volume rename is free and, if the delete is
    refused, leaves the tree in one place this function can find again.

    A delete guard can still abort this process part-way. It fails closed, so
    nothing is lost; run it from an ordinary terminal instead.
    """
    victims = stash_victims()
    if not victims:
        say("SKIP", "no stashed leftovers to prune")
        return

    stage_root = TRASH / ".purge"
    stage_root.mkdir(parents=True, exist_ok=True)

    reclaimed = 0.0
    for v in victims:
        size = tree_mb(v)
        staged = stage_root / v.name
        try:
            if v.parent != stage_root:
                if staged.exists():
                    say("WARN", f"{v.name}: already staged at {staged.parent.name}/, skipped")
                    continue
                os.rename(str(v), str(staged))
            shutil.rmtree(staged)
            reclaimed += size
            say("PRUNE", f"{v.name}  ({size:.0f} MB)")
        except Exception as e:                  # noqa: BLE001
            say("WARN", f"{v.name}  ({size:.0f} MB) left at "
                        f"{staged.relative_to(REPO)}: {e}")

    left = sorted(p.name for p in stage_root.iterdir() if p.is_dir())
    if left:
        say("WARN", f"{reclaimed:.0f} MB reclaimed, still on disk: {', '.join(left)}")
        say("WARN", "the delete was refused - run this from an ordinary terminal, "
                    f"or remove {stage_root.relative_to(REPO)} by hand")
    else:
        say("INFO", f"{reclaimed:.0f} MB reclaimed")


# --------------------------------------------------------------------------
# 3. application icon
# --------------------------------------------------------------------------
def ensure_icon(skip: bool) -> bool:
    """Regenerate build/app-icon.ico from app-icon.svg when it is out of date.

    The .ico is a *generated* artifact and dossier.spec only reads it -- a
    missing .ico makes PyInstaller quietly drop the icon from the exe. Keeping
    the regeneration here means editing app-icon.svg is enough on its own.

    Returns False when an icon could not be produced, so the caller can say so
    rather than shipping a blank exe silently.
    """
    if skip:
        say("SKIP", "icon regeneration disabled by --skip-icon")
        return ICON_ICO.exists()

    if not ICON_SVG.exists():
        say("WARN", f"{ICON_SVG.name} is missing - cannot regenerate the icon")
        return ICON_ICO.exists()
    if not MAKE_ICON.exists():
        say("WARN", f"{MAKE_ICON.name} is missing - cannot regenerate the icon")
        return ICON_ICO.exists()

    if ICON_ICO.exists() and ICON_ICO.stat().st_mtime >= ICON_SVG.stat().st_mtime:
        say("OK", f"icon up to date ({ICON_ICO.name}, "
                  f"{ICON_ICO.stat().st_size / 1024:.0f} KB)")
        return True

    why = "missing" if not ICON_ICO.exists() else "older than app-icon.svg"
    say("ICON", f"{ICON_ICO.name} {why} - rendering from {ICON_SVG.name} ...")
    rc = subprocess.run([sys.executable, str(MAKE_ICON)]).returncode
    if rc != 0 or not ICON_ICO.exists():
        say("WARN", "icon render failed - the exe will ship WITHOUT an icon")
        return False
    say("OK", f"icon regenerated ({ICON_ICO.stat().st_size / 1024:.0f} KB)")
    return True


def verify_icon(path: Path) -> bool:
    """Prove the icon is inside the exe: PE resource section + byte-level match.

    Much stronger than reading the build log: PyInstaller copies the .ico into
    an RT_ICON resource, so the largest frame's bytes must appear verbatim in
    the exe. This is what catches a silent icon-less build.
    """
    import struct

    if not ICON_ICO.exists():
        say("WARN", "no app-icon.ico - skipped the embedded-icon check")
        return True

    exe_bytes = path.read_bytes()
    ico_bytes = ICON_ICO.read_bytes()

    pe_off = struct.unpack_from("<I", exe_bytes, 0x3C)[0]
    num_sections = struct.unpack_from("<H", exe_bytes, pe_off + 6)[0]
    opt_size = struct.unpack_from("<H", exe_bytes, pe_off + 20)[0]
    sec_base = pe_off + 24 + opt_size
    has_rsrc = False
    for i in range(num_sections):
        off = sec_base + i * 40
        if exe_bytes[off:off + 8].rstrip(bytes(1)).decode("ascii", "replace") == ".rsrc":
            has_rsrc = True
            break
    if not has_rsrc:
        say("ERROR", "exe has no .rsrc section - the icon was NOT embedded")
        return False

    count = struct.unpack_from("<H", ico_bytes, 4)[0]
    best = None
    for i in range(count):
        off = 6 + i * 16
        w = ico_bytes[off] or 256
        h = ico_bytes[off + 1] or 256
        size, data_off = struct.unpack_from("<II", ico_bytes, off + 8)
        if best is None or size > best[3]:
            best = (w, h, data_off, size)
    w, h, data_off, size = best
    frame = ico_bytes[data_off:data_off + size]
    # A slice from the middle avoids header rewriting at the edges.
    probe = frame[len(frame) // 2: len(frame) // 2 + 64]
    if probe not in exe_bytes:
        say("ERROR", f"the {w}x{h} icon frame is not inside the exe")
        return False

    say("OK", f"icon embedded in the exe ({w}x{h}, {len(frame) / 1024:.1f} KB)")
    return True


# --------------------------------------------------------------------------
# 3b. companion documents beside the exe
# --------------------------------------------------------------------------
def missing_shipped_docs() -> list[Path]:
    """Companion documents that are not on disk. Checked BEFORE building."""
    return [p for p in SHIPPED_DOCS if not p.exists()]


def stage_shipped_docs() -> bool:
    """Copy SHIPPED_DOCS into dist/dossier/, immediately beside dossier.exe.

    Two reasons this lives here rather than in dossier.spec:

      * PyInstaller 6 OneFolder sends every `datas` entry to _internal/, and
        the read-only bundle is exactly where these documents must NOT be.
      * Staging after the build also keeps the files clear of both of
        PyInstaller's delete points (the work dir and the COLLECT wipe of
        dist/dossier), so nothing has to be re-copied or re-verified.

    Returns False when a source is missing, so a silently incomplete release
    folder cannot be produced.
    """
    absent = missing_shipped_docs()
    if absent:
        say("ERROR", "missing companion document(s): " + ", ".join(p.name for p in absent))
        return False
    for src in SHIPPED_DOCS:
        dst = APP / src.name
        shutil.copy2(str(src), str(dst))
        say("STEP", f"staged {src.name} -> {dst.relative_to(REPO)} "
                    f"({dst.stat().st_size / 1024:.0f} KB)")
    return True


# --------------------------------------------------------------------------
# 4. build + verify
# --------------------------------------------------------------------------
def run_fixture() -> bool:
    if not VERIFY.exists():
        say("SKIP", f"frozen-root fixture not found ({VERIFY.name})")
        return True
    say("CHECK", "running the frozen-root fixture ...")
    rc = subprocess.run([sys.executable, str(VERIFY)]).returncode
    if rc != 0:
        say("ERROR", "frozen-root fixture failed - fix the path logic before building.")
        return False
    return True


def run_builder() -> int:
    say("BUILD", f"PyInstaller {SPEC.name} -> {APP}")
    cmd = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--workpath", str(WORK), "--distpath", str(DIST), "--noconfirm",
    ]
    return subprocess.run(cmd).returncode


def verify_bundle() -> bool:
    ok = True
    if not EXE.exists():
        say("ERROR", f"{EXE} was not produced")
        return False
    say("OK", f"{EXE.relative_to(REPO)}  ({EXE.stat().st_size / 1024 / 1024:.1f} MB)")

    missing = [label for label, rel in BUNDLED.items() if not (APP / rel).exists()]
    if missing:
        ok = False
        say("ERROR", "missing bundled assets: " + ", ".join(missing))
    else:
        say("OK", f"all {len(BUNDLED)} bundled assets present")

    for src in SHIPPED_DOCS:
        dst = APP / src.name
        if not dst.exists():
            ok = False
            say("ERROR", f"{src.name} is not beside the exe")
        elif dst.stat().st_size != src.stat().st_size:
            ok = False
            say("ERROR", f"{src.name} is truncated: {dst.stat().st_size} of "
                         f"{src.stat().st_size} bytes")
        else:
            say("OK", f"{src.name} beside the exe ({dst.stat().st_size / 1024:.0f} KB)")

    inner = APP / "_internal"
    leaked = sorted(p.name for p in inner.iterdir()
                    if p.name in RUNTIME_STATE) if inner.is_dir() else []
    if leaked:
        ok = False
        say("ERROR", f"_internal write leakage: {leaked}")
    else:
        say("OK", "_internal is clean (no runtime state inside the bundle)")

    buried = ([p.name for p in SHIPPED_DOCS if (inner / p.name).exists()]
              if inner.is_dir() else [])
    if buried:
        ok = False
        say("ERROR", f"companion document(s) land inside the bundle: {buried}")

    if not verify_icon(EXE):
        ok = False

    say("INFO", f"distribution folder total: {tree_mb(APP):.0f} MB")
    return ok


def report_stash() -> None:
    victims = sorted(list(TRASH.glob("dist_prev_*")) + list(TRASH.glob("pyi_prev_*")))
    if not victims:
        return
    total = sum(tree_mb(v) for v in victims)
    say("INFO", f"{len(victims)} stashed leftover(s) under {TRASH.name}/ ({total:.0f} MB)")
    say("INFO", "reclaim with: venv/Scripts/python.exe build/rebuild.py --prune")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild dist/dossier/dossier.exe")
    ap.add_argument("--skip-install", action="store_true",
                    help="never run pip, even when PyInstaller is missing")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the frozen-root fixture")
    ap.add_argument("--skip-icon", action="store_true",
                    help="do not regenerate build/app-icon.ico from app-icon.svg")
    ap.add_argument("--prune", action="store_true",
                    help="delete the stashed leftovers and exit")
    args = ap.parse_args()

    if args.prune:
        prune_stash()
        return 0

    if not SPEC.exists():
        say("ERROR", f"spec not found: {SPEC}")
        say("ERROR", "run from the repository root: venv/Scripts/python.exe build/rebuild.py")
        return 2

    # Same reasoning as preflight_dist: a missing input costs one stat to
    # detect now and a whole build to discover afterwards.
    absent = missing_shipped_docs()
    if absent:
        say("ERROR", "missing companion document(s): " + ", ".join(p.name for p in absent))
        return 2

    if not args.no_verify and not run_fixture():
        return 1

    # Cheapest, most actionable check first: two renames that prove we can
    # replace dist/dossier at all, before any build time is spent.
    if not preflight_dist():
        return 1

    if not ensure_pyinstaller(args.skip_install):
        return 1

    icon_ok = ensure_icon(args.skip_icon)

    try:
        # Cheap pass first: the work dir holds the incremental analysis cache,
        # so it is only given up when the build actually needs it.
        for m in stash_before_build(include_workdir=False):
            say("STEP", f"moved aside: {m}")

        rc = run_builder()
        if rc != 0 and not EXE.exists():
            say("WARN", f"PyInstaller exited {rc} - retrying with a clean work dir")
            for m in stash_before_build(include_workdir=True):
                say("STEP", f"moved aside: {m}")
            rc = run_builder()
    except StashFailed:
        say("ERROR", "build aborted - dist/dossier was left untouched")
        return 1

    if rc != 0 or not EXE.exists():
        say("ERROR", f"PyInstaller failed (exit {rc}) - see the output above")
        return rc or 1

    if not stage_shipped_docs():
        return 1

    if not verify_bundle():
        return 1

    if not icon_ok:
        say("WARN", "this build has NO application icon - see the notes above")

    report_stash()
    say("DONE", "dist/dossier/ is ready to ship - dossier.exe, _internal/, "
                + ", ".join(p.name for p in SHIPPED_DOCS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
