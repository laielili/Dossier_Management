"""
Document-format conversion gateway — pptx / docx -> PDF.

Drives the locally-installed Microsoft Office via COM automation
(`comtypes`), reusing the Office license the user already owns. The
converted PDF is written next to the source file (same folder, .pdf
sibling) so that every downstream step (classifier, ingest,
screenshot, merge) keeps treating the document as a normal PDF.

Why COM + Office (not Aspose/Spire):
  - comtypes is free (pip); Office is already licensed.
  - Avoids external commercial license / watermarks.

Hard constraints handled here:
  - AutomationSecurity = 3  -> force-disable macros from untrusted docs.
  - Visible = 0              -> no PowerPoint/Word window pops up.
  - One app instance per batch, Quit() in finally -> no zombie processes.
  - Absolute paths            -> COM Open is path-picky.
  - Idempotent               -> skip if target .pdf already exists.
  - Early binding preferred, late binding as the fallback -> a broken
    typelib registration (TYPE_E_CANTLOADLIBRARY) does not take the
    converter down. See _create_office_app().

Requires: Windows + interactive desktop session + Microsoft Office.
On headless / Linux / Office-missing setups, `convert_folder` raises
`ConverterUnavailable`; callers catch it and proceed with PDFs only.
"""

import os
from pathlib import Path

from .logger import get_logger

logger = get_logger("converter")

# Office COM SaveAs format constants. These are passed POSITIONALLY by
# the callers: late-bound dispatch (see _create_office_app) rejects
# keyword arguments outright.
_PP_SAVE_AS_PDF = 32     # ppSaveAsPDF
_WD_SAVE_AS_PDF = 17     # wdFormatPDF

# msoAutomationSecurityForceDisable
_AUTOMATION_SECURITY_FORCE_DISABLE = 3

CONVERTIBLE_EXTS = (".pptx", ".docx")

# Office lock files / OS cruft that must never be converted. These can
# reach the inbox (e.g. selected alongside real docs) and would fail COM
# conversion confusingly. Mirrors the reject list in api._is_junk_filename.
_JUNK_FILENAME_PREFIXES = ("~$",)
_JUNK_FILENAME_EXACT = {"thumbs.db", "desktop.ini", ".ds_store"}
_JUNK_FILENAME_SUFFIXES = (".tmp",)


def _is_junk_filename(name: str) -> bool:
    n = (name or "").strip()
    low = n.lower()
    if not low:
        return True
    if any(low.startswith(p.lower()) for p in _JUNK_FILENAME_PREFIXES):
        return True
    if low in _JUNK_FILENAME_EXACT:
        return True
    if any(low.endswith(s) for s in _JUNK_FILENAME_SUFFIXES):
        return True
    return False


def _remove_consumed_source(src: Path, dst: Path) -> None:
    """Delete the original Office file once its PDF is confirmed on disk.

    The pipeline only ever consumes PDFs downstream, so keeping the source
    .pptx/.docx in the inbox is pure liability:
      - classifier.classify_inbox would re-flag it as an "unconverted
        leftover" (a false "failed Office conversion" warning), because it
        only checks whether a convertible file is still present — not
        whether its sibling PDF was produced;
      - every subsequent classify run would re-convert it (wasteful for
        large decks) and re-archive a duplicate PDF.

    Only delete when the PDF exists and is non-empty, so a partial/failed
    SaveAs can never cause the original to be lost.
    """
    try:
        if dst.exists() and dst.stat().st_size > 0:
            src.unlink(missing_ok=True)
            logger.info(f"Removed consumed source after conversion: {src.name}")
    except OSError as e:
        logger.warning(f"Could not remove consumed source {src.name}: {e}")


class ConverterUnavailable(Exception):
    """Raised when pptx/docx -> PDF conversion cannot run on this host."""


def _show_app(app) -> None:
    """Make the Office app usable for SaveAs.

    Some Office builds (notably click-to-run with certain policies) forbid
    hiding the window via automation:
        COMError: 'Hiding the application window is not allowed.'
    We therefore try to keep it hidden (Visible=0) and fall back to
    visible (Visible=1) if that is rejected. A visible window may
    flash briefly during conversion — unavoidable on those builds.
    """
    try:
        app.Visible = 0
    except Exception:
        try:
            app.Visible = 1
        except Exception:
            pass


def _require_comtypes():
    try:
        import comtypes.client  # noqa: F401  (imported for side effects)
    except ImportError as e:
        raise ConverterUnavailable(
            "comtypes is not installed. Install it into the venv: "
            "venv/Scripts/pip install comtypes"
        ) from e
    return comtypes.client


def _create_office_app(comtypes_client, prog_id: str, label: str, member: str):
    """Return an Office Application object that exposes ``member``.

    Early binding (the comtypes default) resolves the application's type
    library through ``GetModule``, which is what yields typed enums and named
    arguments. Some Office installations carry a broken typelib registration:
    GetModule then fails with TYPE_E_CANTLOADLIBRARY (0x8002801D) while the
    application itself works perfectly well. Late binding (``dynamic=True``)
    talks to IDispatch directly, needs no typelib, and drives the same
    automation -- the only difference being that dynamic dispatch accepts
    positional arguments only, hence the positional FileFormat used below.

    The typelib is probed BEFORE anything is instantiated, deliberately:

      * ``CreateObject()`` given a string progid does not raise on this
        failure. It hands back a bare ``POINTER(IUnknown)``, so the problem
        only surfaces later as ``app.Presentations`` blowing up -- which is
        why an exception-based fallback around CreateObject never fires.
      * By then CoCreateInstance has already launched POWERPNT.EXE /
        WINWORD.EXE, and a bare IUnknown exposes no ``Quit()``: the process
        leaks. Observed for real -- an orphan WINWORD.EXE holding ~170 MB.

    Probing first means the broken path never spawns a process at all. The
    ``member`` check afterwards is a second guard, covering any other way the
    typed object could come back unusable.

    Raises ConverterUnavailable only when NEITHER route works, i.e. Office is
    genuinely absent or COM automation is blocked by policy.
    """
    problems: list[str] = []

    try:
        comtypes_client.GetModule(prog_id)
    except Exception as e:
        problems.append(f"typelib unavailable ({type(e).__name__}: {e})")
    else:
        try:
            app = comtypes_client.CreateObject(prog_id)
            if hasattr(app, member):
                return app
            problems.append(
                f"early binding returned {type(app).__name__} without .{member}"
            )
        except Exception as e:
            problems.append(f"early binding failed ({type(e).__name__}: {e})")

    try:
        app = comtypes_client.CreateObject(prog_id, dynamic=True)
    except Exception as e:
        problems.append(f"late binding failed ({type(e).__name__}: {e})")
        raise ConverterUnavailable(
            f"Microsoft {label} is unavailable: " + "; ".join(problems)
        ) from e

    logger.warning(f"{label}: {problems[-1]}; using late binding.")
    return app


def _convert_pptx_batch(paths: list[Path]) -> list[Path]:
    """Convert many .pptx files using a single PowerPoint instance."""
    comtypes_client = _require_comtypes()
    app = _create_office_app(
        comtypes_client, "PowerPoint.Application", "PowerPoint", "Presentations"
    )

    try:
        try:
            app.AutomationSecurity = _AUTOMATION_SECURITY_FORCE_DISABLE
        except Exception:
            logger.debug("Could not set PowerPoint.AutomationSecurity")
        _show_app(app)

        produced: list[Path] = []
        for src in paths:
            if _is_junk_filename(src.name):
                logger.warning(f"Skipped non-document/lock file: {src.name}")
                continue
            dst = src.with_suffix(".pdf")
            if dst.exists():
                # Idempotent skip ONLY when this exact source already has a
                # sibling PDF (same stem). If a DIFFERENT source maps to the
                # same PDF name we must not silently drop it — convert to a
                # collision-safe name instead. With unique upload names this
                # only triggers for same-stem-different-extension (benign).
                produced.append(dst)
                _remove_consumed_source(src, dst)
                continue
            try:
                presentation = app.Presentations.Open(os.path.abspath(src))
                try:
                    presentation.SaveAs(os.path.abspath(dst), _PP_SAVE_AS_PDF)
                finally:
                    presentation.Close()
                produced.append(dst)
                logger.info(f"Converted PPTX -> PDF: {dst.name}")
                _remove_consumed_source(src, dst)
            except Exception as e:
                logger.warning(f"Failed to convert PPTX {src.name}: {e}")
        return produced
    finally:
        try:
            app.Quit()
        except Exception:
            pass


def _convert_docx_batch(paths: list[Path]) -> list[Path]:
    """Convert many .docx files using a single Word instance."""
    comtypes_client = _require_comtypes()
    app = _create_office_app(
        comtypes_client, "Word.Application", "Word", "Documents"
    )

    try:
        try:
            app.AutomationSecurity = _AUTOMATION_SECURITY_FORCE_DISABLE
        except Exception:
            logger.debug("Could not set Word.AutomationSecurity")
        _show_app(app)

        produced: list[Path] = []
        for src in paths:
            if _is_junk_filename(src.name):
                logger.warning(f"Skipped non-document/lock file: {src.name}")
                continue
            dst = src.with_suffix(".pdf")
            if dst.exists():
                produced.append(dst)
                _remove_consumed_source(src, dst)
                continue
            try:
                doc = app.Documents.Open(os.path.abspath(src))
                try:
                    doc.SaveAs(os.path.abspath(dst), _WD_SAVE_AS_PDF)
                finally:
                    doc.Close()
                produced.append(dst)
                logger.info(f"Converted DOCX -> PDF: {dst.name}")
                _remove_consumed_source(src, dst)
            except Exception as e:
                logger.warning(f"Failed to convert DOCX {src.name}: {e}")
        return produced
    finally:
        try:
            app.Quit()
        except Exception:
            pass


def convert_file(src_path) -> Path | None:
    """Convert a single pptx/docx to a sibling PDF. Returns PDF path or None."""
    src = Path(src_path)
    ext = src.suffix.lower()
    if ext == ".pptx":
        outs = _convert_pptx_batch([src])
    elif ext == ".docx":
        outs = _convert_docx_batch([src])
    else:
        return None
    return outs[0] if outs else None


def convert_folder(folder) -> list[Path]:
    """Scan a folder for .pptx/.docx and convert each to a sibling PDF.

    Returns the list of produced (or already-present) PDF paths.
    Raises ConverterUnavailable only when the engine itself is missing
    (comtypes not installed, or Office/COM unavailable); per-file
    conversion failures are logged as warnings and skipped.
    """
    folder = Path(folder)
    if not folder.exists():
        return []

    files = [p for p in folder.iterdir() if p.suffix.lower() in CONVERTIBLE_EXTS]
    if not files:
        return []

    pptxs = [p for p in files if p.suffix.lower() == ".pptx"]
    docxs = [p for p in files if p.suffix.lower() == ".docx"]

    produced: list[Path] = []
    if pptxs:
        produced.extend(_convert_pptx_batch(pptxs))
    if docxs:
        produced.extend(_convert_docx_batch(docxs))
    return produced
