# =============================================================================
# Dossier_Management - PyInstaller spec  (OneFolder)
# =============================================================================
# Build (from the repository root, any interpreter that has the runtime deps
# plus pyinstaller - see requirements-build.txt):
#
#   venv/Scripts/python.exe -m pip install -r requirements-build.txt
#   venv/Scripts/python.exe -m PyInstaller build/dossier.spec \
#       --workpath build/_pyi --distpath dist
#
#   -> dist/dossier/dossier.exe
#      (build/rebuild.py also stages Installation_SOP.docx beside the exe; the
#       SOP is a companion document, so it is deliberately NOT a datas entry -
#       one would land inside the read-only _internal/ folder)
#
# ---------------------------------------------------------------------------
# Runtime layout this spec assumes
# ---------------------------------------------------------------------------
# The app resolves TWO roots at import time (src/config.py):
#
#   ASSET_ROOT  = sys._MEIPASS = dist/dossier/_internal   (read-only)
#                 static/, classify/*.txt, queries/query.txt,
#                 src/svg2ppt/templates/*.json
#   APP_ROOT    = the folder holding dossier.exe          (writable)
#                 data/, index_projects/, screenshots/, output/, logs/,
#                 retrieved/, Dossier_condensed/, <project>/, plus the
#                 user-editable config_overrides.json and search_paths.txt
#
# classify/*.txt and queries/query.txt are shipped AND written back by the UI,
# so config._seed_writable_defaults() copies them into APP_ROOT on first run
# and every later run edits that copy.
#
# CONSEQUENCE - deployment constraint: install dist/dossier/ into a folder the
# user can write to (%LOCALAPPDATA%, a user folder, a USB stick). Installing
# under Program Files fails at import with a clear RuntimeError naming APP_ROOT.
#
# ---------------------------------------------------------------------------
# Corrections to the previous draft of this spec (2026-09-08)
# ---------------------------------------------------------------------------
#  1. `("html-to-pptx", "html-to-pptx")` was listed as a data directory. That
#     directory does not exist in this repository and the entry broke the
#     build. Removed.
#  2. The old note claimed "PROJECT_ROOT resolves to the exe directory". It does
#     not: PyInstaller onedir puts data under `_internal/`, and modules in the
#     PYZ report __file__ as <sys._MEIPASS>/..., so PROJECT_ROOT resolved to
#     `_internal/` and every state file would have landed inside the bundle
#     (wiped on upgrade). Fixed in code, not by deployment advice - see the two
#     roots above.
#  3. The old note told the reader to set a `COMTYPES_GEN_DIR` environment
#     variable. comtypes 1.4.x does not read that variable; the name appears
#     nowhere in the installed package. comtypes already handles being frozen
#     itself (client/_code_cache.py::_find_gen_dir): with comtypes.gen absent
#     from the bundle it creates a memory-only package and writes generated
#     typelib code to %TEMP%/comtypes_cache/<exename>-<pyver>. No action needed.
#  4. The old note mentioned `listen_folder.txt` - there is no folder-listening
#     feature in src/ at all, so no such file is written.
#
# Kept from the draft: `prompt/` is NOT bundled. Verified again - it is LLM
# specification documentation, never opened at runtime.
# =============================================================================

from pathlib import Path

REPO = Path(SPECPATH).resolve().parent
block_cipher = None

a = Analysis(
    [str(REPO / "main.py")],
    pathex=[str(REPO)],
    binaries=[],
    # ---- External (non-.py) assets read at runtime ----
    # dest paths must match how the code resolves them under sys._MEIPASS:
    #   * top-level dirs   -> ASSET_ROOT/<name>
    #   * svg2ppt template -> ASSET_ROOT/src/svg2ppt/templates
    #     (layout.py resolves it via Path(__file__).parent, so the
    #      src/svg2ppt/ layer has to be preserved)
    # NOTE: output/, python/, venv/, screenshots/, logs/, data/ and _trash/ are
    # deliberately absent - runtime state and build leftovers, not assets.
    # NOTE: PyInstaller resolves a *relative* datas path against the folder
    # holding this spec (build/), not against the repository root, so every
    # source path is spelled out from REPO. The relative form silently looked
    # for build/static and aborted the build.
    datas=[
        (str(REPO / "static"), "static"),      # incl. study_region/_48, svg2ppt.html
        (str(REPO / "classify"), "classify"),  # classifier profiles (first-run defaults)
        (str(REPO / "queries"), "queries"),    # query.txt lexicon (first-run default)
        (str(REPO / "src" / "svg2ppt" / "templates"), "src/svg2ppt/templates"),
    ],
    hiddenimports=[
        # PDF / imaging
        "pymupdf",
        "PIL", "PIL.Image",
        # Office deck writer (python-pptx also needs its bundled default.pptx)
        "pptx", "lxml", "lxml.etree",
        # web stack - FastAPI/uvicorn import these lazily, so the parent
        # packages alone are not enough and the server dies at startup
        "fastapi", "starlette", "pydantic", "pydantic_core",
        "uvicorn", "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
        "uvicorn.protocols", "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan", "uvicorn.lifespan.on",
        # COM (pptx/docx -> pdf; lazily imported inside converter.py)
        "comtypes", "comtypes.client",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # keep the bundle lean - none of these are used by the pipeline
        "matplotlib", "numpy", "scipy", "pandas",
        "PyQt5", "PyQt6", "PySide2", "tkinter",
        # only needed by uvicorn's reloader, which is disabled when frozen
        "watchfiles",
    ],
    noarchive=False,
)

# ---- Application icon -------------------------------------------------
# build/app-icon.ico is rendered from app-icon.svg by build/make_icon.py
# and kept in the tree, so a build never depends on a browser being
# installed here. Re-run that script after editing the SVG:
#     venv/Scripts/python.exe build/make_icon.py
_ICON = REPO / "build" / "app-icon.ico"
_ICON_SVG = REPO / "app-icon.svg"
if not _ICON.exists():
    # Loud on purpose. PyInstaller happily produces an icon-less exe here, and
    # nobody notices until it is already shipped. build/rebuild.py regenerates
    # the .ico automatically; this branch is the bare-PyInstaller path.
    print("""
==========================================================================
  WARNING - building an exe with NO APPLICATION ICON
  Missing: """ + str(_ICON) + """
  It is generated from app-icon.svg. Create it with:
      venv/Scripts/python.exe build/make_icon.py
  ...or build through build/rebuild.py, which does this automatically.
==========================================================================
""")
elif _ICON_SVG.exists() and _ICON.stat().st_mtime < _ICON_SVG.stat().st_mtime:
    print(
        f"[dossier.spec] NOTE: app-icon.ico is older than app-icon.svg - the "
        f"icon may be stale. Refresh with: venv/Scripts/python.exe build/make_icon.py"
    )

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dossier",
    icon=str(_ICON) if _ICON.exists() else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # the server window doubles as the log view; keep it,
                           # and note that closing the window stops the server
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="dossier",
)
