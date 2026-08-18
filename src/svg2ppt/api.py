"""
svg2ppt backend — FastAPI bindings for the SVG-component -> PPTX deck builder.

This module is the ONLY place where the svg2ppt engine meets HTTP. It is wired
into src/api.py via:

    from .svg2ppt.api import router as svg2ppt_router
    app.include_router(svg2ppt_router)

Nothing here touches the existing html2pptx pipeline or the dossier pipeline.

Routes:
  GET  /svg2ppt                  — serve the SVG -> PPTX frontend page
  POST /svg2ppt/build            — accept a deck XML + options, build the deck
  GET  /svg2ppt/files/{run_id}/{filename} — safely serve a generated artifact
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import DeckBuilder, SchemaError
from .render import RenderError

# Repo root = src/svg2ppt/api.py -> parent(=svg2ppt) -> parent(=src) -> parent(=repo).
# Resolved without importing config so the module also loads cleanly when svg2ppt
# is imported as a standalone top-level package (e.g. by the demo).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_ROOT = REPO_ROOT / "output" / "svg2ppt_builds"
STATIC_PAGE = REPO_ROOT / "static" / "svg2ppt.html"

router = APIRouter()

_PPTX_MAGIC = b"PK\x03\x04"
_MEDIA_TYPES = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json",
}
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class Svg2PptxBuildRequest(BaseModel):
    """Build a deck from AI-provided SVG-component XML.

    ``xml`` is the full <deck> document (one <component type=...><svg>…</svg>
    per component). All other fields are optional UI-driven options, including
    the runtime debug/theme overrides applied by the svg2ppt engine.
    """

    xml: str
    filename: str = "synthesis_deck"
    max_pages: int = 0          # 0 => no cap (add-pages); >0 => hard cap, compress to fit
    dpi: int = 150              # render resolution for the raster deck
    theme: str = "loreal"       # reserved for future multi-template support

    # --- Debug / theme overrides (runtime only, never persisted) ---
    theme_overrides: dict = Field(default_factory=dict)   # structural/theme colors
    chrome_font_scale: float = 1.0   # banner/section-title font multiplier (region chrome)
    title_font_scale: float = 1.0    # top_banner content (title/formula-ref) multiplier
    meta_font_scale: float = 1.0     # meta_row content multiplier
    content_font_scale: float = 1.0  # left/middle/right column content multiplier (drives pages)
    margin_scale: float = 1.0        # region padding + inter-component gap multiplier


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@router.get("/svg2ppt", response_class=HTMLResponse)
async def svg2ppt_page():
    """Serve the SVG -> PPTX converter page."""
    if STATIC_PAGE.exists():
        return HTMLResponse(STATIC_PAGE.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h2>svg2ppt.html not found in static/</h2>", status_code=404
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _safe_filename(raw: str) -> str:
    """Reduce user input to a bare, safe ``*.pptx`` file name."""
    name = (raw or "").strip()
    name = name.replace("\\", "/").split("/")[-1]
    if name.lower().endswith(".pptx"):
        name = name[:-5]
    name = _UNSAFE.sub("_", name).strip().strip(".")
    if not name or set(name) <= {"."}:
        name = "synthesis_deck"
    return name[:120] + ".pptx"


@router.post("/svg2ppt/build")
async def svg2ppt_build(req: Svg2PptxBuildRequest):
    """Convert a deck XML into a 16:9 PPTX (+ inline preview assets).

    The XML is parsed and laid out entirely server-side (no browser). The
    result is written under output/svg2ppt_builds/<run_id>/; the response
    returns a download URL plus run_id/page_count so the client can render
    an inline preview from the per-page PNGs (no HTML file is generated).
    """
    xml = (req.xml or "").strip()
    if not xml:
        raise HTTPException(400, "xml is required (the <deck> document)")

    filename = _safe_filename(req.filename)
    dpi = max(72, min(int(req.dpi), 300))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    out_dir = OUTPUT_ROOT / run_id

    try:
        builder = DeckBuilder(
            template_path=None,
            dpi=dpi,
            max_pages=req.max_pages,
            overrides={
                "theme_overrides": req.theme_overrides or {},
                "chrome_font_scale": req.chrome_font_scale,
                "title_font_scale": req.title_font_scale,
                "meta_font_scale": req.meta_font_scale,
                "content_font_scale": req.content_font_scale,
                "margin_scale": req.margin_scale,
            },
        )
        result = builder.build_from_string(xml, out_dir, filename=filename)
    except SchemaError as exc:
        # The AI produced malformed XML — surface the message so the user can fix it.
        raise HTTPException(400, f"Invalid deck XML: {exc}")
    except RenderError as exc:
        raise HTTPException(500, f"Render failed: {exc}")
    except Exception as exc:  # noqa: BLE001 — surface any engine failure clearly
        raise HTTPException(500, f"Build failed: {exc}")

    pptx_bytes = result.pptx_path.read_bytes()
    size_kb = round(len(pptx_bytes) / 1024, 1)

    return {
        "ok": True,
        "run_id": run_id,
        "page_count": result.page_count,
        "size_kb": size_kb,
        "filename": result.pptx_path.name,
        "pptx_url": f"/svg2ppt/files/{run_id}/{result.pptx_path.name}",
        "warnings": result.warnings,
    }


# ---------------------------------------------------------------------------
# Artifact download (scoped file server)
# ---------------------------------------------------------------------------

@router.get("/svg2ppt/files/{run_id}/{filename}")
async def svg2ppt_file(run_id: str, filename: str):
    """Serve a generated artifact (pptx / preview.html / page PNG).

    Path traversal is blocked: the resolved target must stay inside
    output/svg2ppt_builds/<run_id>/.
    """
    base = (OUTPUT_ROOT / run_id).resolve()
    if not base.exists() or not base.is_dir():
        raise HTTPException(404, "Build not found")

    target = (base / filename).resolve()
    # Reject anything that escapes the run directory.
    if target != base and target.parent != base:
        # Allow only direct children of the run dir.
        if not str(target).startswith(str(base) + "\\") and not str(target).startswith(
            str(base) + "/"
        ):
            raise HTTPException(403, "Forbidden")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")

    suffix = target.suffix.lower()
    media = _MEDIA_TYPES.get(suffix, "application/octet-stream")
    return FileResponse(
        str(target),
        media_type=media,
        filename=target.name,
    )
