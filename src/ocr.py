"""
OCR pre-pass — give scanned / image-only PDFs a real text layer.

WHY THIS EXISTS
--------------
The whole pipeline (classify, ingest, denoise) reads page text via
``pymupdf.get_text()``. A scanned PDF has no text layer, so ``get_text()`` returns
nothing and the document silently drops out of every stage. Rather than teach
each stage about OCR, this module synthesizes a *text layer* into a copy of the
PDF: downstream code never learns the source was scanned — it just sees text.

HOW
---
For each page we render it to a raster, run easyocr, then insert the recognized
text as **invisible** PDF text (``render_mode=3``) at an approximate position /
font size derived from the detected bounding box. The original image is kept, so
the visual page is unchanged; only a searchable/extractable text layer is added.

Files that already have a text layer are returned unchanged (no double-OCR).
OCR output is cached by file-content hash so repeat runs on identical files skip
the (heavy) recognition step.
"""

import hashlib
import threading
from pathlib import Path

import pymupdf as fitz

from .config import (
    OCR_ENABLED,
    OCR_LANGS,
    OCR_DPI,
    OCR_GPU,
    OCR_DETECTOR,
    OCR_CACHE_DIR,
)
from .logger import get_logger
from .pdf_parser import pdf_has_text

logger = get_logger("ocr")

# easyocr Reader is heavy (loads torch + models). Construct it at most once and
# lazily — only when an actual scanned PDF shows up.
_reader = None
_reader_lock = threading.Lock()


def _get_reader(langs: tuple, gpu: bool, detector: str | None = None):
    global _reader
    with _reader_lock:
        if _reader is not None:
            return _reader
        try:
            from easyocr import Reader
        except ImportError as e:  # pragma: no cover - depends on env
            raise RuntimeError(
                "OCR is enabled but 'easyocr' is not installed. "
                "Install it with: pip install easyocr"
            ) from e
        _reader = Reader(
            list(langs),
            gpu=gpu,
            detect_network=detector or OCR_DETECTOR,
            verbose=False,
        )
        return _reader


def _ocr_to_text_layer(src_pdf: Path, out_pdf: Path, langs: tuple, dpi: int, gpu: bool) -> None:
    """Run OCR over ``src_pdf`` and write a text-layer copy to ``out_pdf``."""
    import numpy as np

    reader = _get_reader(langs, gpu)
    doc = fitz.open(str(src_pdf))
    pt_per_px = 72.0 / dpi
    scale = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=scale, alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            # detail=1 -> (bbox, text, conf); paragraph=False -> one entry per line.
            results = reader.readtext(img, detail=1, paragraph=False)
            for bbox, text, conf in results:
                if not text or conf < 0.2:
                    continue
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                x0 = min(xs) * pt_per_px
                y_top = min(ys) * pt_per_px
                h = (max(ys) - min(ys)) * pt_per_px
                fs = max(6.0, h * 0.72)        # approximate cap-height in points
                base_y = y_top + h * 0.85      # rough baseline below the top
                try:
                    page.insert_text(
                        (x0, base_y),
                        text,
                        fontsize=fs,
                        fontname="helv",
                        render_mode=3,          # 3 = invisible: text layer only
                    )
                except Exception:
                    # Coordinate edge case — fall back to a top-left insert.
                    page.insert_text(
                        (x0, y_top + fs), text, fontsize=fs, fontname="helv",
                        render_mode=3,
                    )
        doc.save(str(out_pdf), garbage=3, deflate=True)
    finally:
        doc.close()


def ocr_ensure_text(
    src_pdf: Path,
    langs: tuple | None = None,
    dpi: int | None = None,
    gpu: bool | None = None,
) -> Path:
    """Return a PDF path that has a usable text layer.

    If ``src_pdf`` already contains extractable text, it is returned unchanged.
    Otherwise OCR is run and a text-layer copy is written to ``OCR_CACHE_DIR``
    (named ``<stem>__ocr_<hash8>.pdf``), reusing a content-hash cache so repeat
    runs on the same bytes skip recognition. Raises ``RuntimeError`` if OCR is
    enabled but unavailable, or if it produced no text (e.g. blank pages).
    """
    src_pdf = Path(src_pdf)
    if not OCR_ENABLED:
        return src_pdf
    if pdf_has_text(src_pdf):
        return src_pdf

    langs = langs or OCR_LANGS
    dpi = dpi or OCR_DPI
    gpu = gpu if gpu is not None else OCR_GPU

    OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(src_pdf.read_bytes()).hexdigest()[:12]
    out_pdf = OCR_CACHE_DIR / f"{src_pdf.stem}__ocr_{digest}.pdf"

    # A valid cache hit: the cached copy still carries a text layer.
    if out_pdf.exists() and pdf_has_text(out_pdf):
        logger.info(f"OCR cache hit: {src_pdf.name} -> {out_pdf.name}")
        return out_pdf

    logger.info(
        f"OCR: synthesizing text layer for {src_pdf.name} "
        f"(langs={langs}, dpi={dpi})"
    )
    _ocr_to_text_layer(src_pdf, out_pdf, langs, dpi, gpu)
    if not pdf_has_text(out_pdf):
        out_pdf.unlink(missing_ok=True)
        raise RuntimeError(
            f"OCR produced no text for {src_pdf.name} — the pages may be blank "
            f"or the scan quality too low to recognize."
        )
    return out_pdf
