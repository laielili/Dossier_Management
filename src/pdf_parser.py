"""
PDF parsing module — PyMuPDF (fitz)

Handles text extraction, page screenshots at 2x resolution.
report_type is inferred from the parent directory name (CLINS / FE / CE).
"""

import hashlib
import re
from pathlib import Path

import fitz

from .config import SCREENSHOTS_DIR, SCREENSHOT_DPI, REPORT_TYPES
from .logger import get_logger

logger = get_logger(__name__)


def infer_report_type(file_path: Path) -> str:
    """Infer report type from parent directory name."""
    parent = file_path.parent.name.upper()
    if parent in REPORT_TYPES:
        return parent
    logger.warning(
        f"Cannot infer report type for {file_path.name}; parent dir '{parent}' "
        f"not in {REPORT_TYPES}. Defaulting to 'UNKNOWN'."
    )
    return "UNKNOWN"


# Bullet / numbered-list markers used to flag "list" pages (summary-style).
_BULLET_RE = re.compile(
    r"^\s*(?:[•‣◦▪▫\-*·]|[\d]{1,2}[.)]|[a-zA-Z][.)]|\([\d]{1,2}\))\s+"
)


def detect_page_signals(page) -> dict:
    """Detect structural signals on a fitz page (used by the page-selection
    stage to keep high-density summary pages).

    Returns a dict with:
        figures: number of visual elements (raster images + dense vector
                 drawings). charts/curves are usually raster images or dense
                 vector diagrams, so both count.
        bullets: True if the page contains a real bullet/numbered list.
        table:   True if the page contains a table.
    """
    # Visual elements. Raster images (photos, chart screenshots) are the most
    # reliable "figure" signal. Vector drawings also include text rendered as
    # paths, so only a dense set of drawings (a real diagram/chart) counts.
    images = len(page.get_images(full=True))
    drawings = len(page.get_drawings())
    has_diagram = drawings >= 20
    figures = images + (drawings if has_diagram else 0)
    has_figure = figures > 0  # images > 0 OR a dense vector diagram

    text = page.get_text()
    bullet_lines = sum(1 for line in text.splitlines() if _BULLET_RE.match(line))
    has_bullets = bullet_lines >= 2

    has_table = False
    try:
        finder = page.find_tables()
        table_list = finder.find_tables() if hasattr(finder, "find_tables") else []
        has_table = len(table_list) > 0
    except Exception:
        # Heuristic fallback: several lines with 3+ internal multi-space gaps.
        gaps = sum(1 for line in text.splitlines() if len(re.findall(r"  +", line)) >= 3)
        has_table = gaps >= 4

    # --- Extra signals used by noise detection (all fitz-native, no deps) ---
    # n_blocks: count of TEXT blocks (excludes image blocks). A content page
    #   has many; a cover / section divider has only 1-3.
    n_blocks = 0
    max_font = 0.0
    try:
        blocks = page.get_text("blocks")
        for b in blocks:
            if len(b) > 6 and b[6] == 0:   # block_type 0 == text
                n_blocks += 1
        for b in page.get_text("dict").get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    if sp.get("size", 0.0) > max_font:
                        max_font = sp["size"]
    except Exception:
        pass
    # img_area_ratio: total raster-image area / page area (0..1). Used to spot
    #   a near-full-page image with no caption (decorative-image noise).
    img_area_ratio = 0.0
    try:
        rect = page.rect
        page_area = float(rect.width * rect.height) or 1.0
        total = 0.0
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if bbox:
                total += float((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        img_area_ratio = min(1.0, total / page_area)
    except Exception:
        pass

    return {
        "figures": figures,
        "bullets": has_bullets,
        "table": has_table,
        "n_blocks": n_blocks,
        "max_font": round(max_font, 1),
        "img_area_ratio": round(img_area_ratio, 3),
    }


class PDFParser:
    """PDF document parser — single-file scope."""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"PDF not found: {self.file_path}")

        self.filename = self.file_path.name
        self.report_type = infer_report_type(self.file_path)
        self.doc = fitz.open(str(self.file_path))

        self._screenshot_dir = SCREENSHOTS_DIR / self.report_type / self.file_path.stem
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def page_count(self) -> int:
        return len(self.doc)

    def extract_text(self, page_number: int) -> str:
        """Extract text from a single page (0-based)."""
        if page_number < 0 or page_number >= self.page_count:
            raise ValueError(
                f"Page out of range: {page_number} (total {self.page_count})"
            )
        return self.doc[page_number].get_text()

    def extract_all_pages(self) -> list[dict]:
        """Extract text from every page.

        Returns:
            list of dicts with keys: page_index, page_label, text, filename,
            report_type, source_path, signals (structural signals used by the
            page-selection stage to keep high-density summary pages).
        """
        pages = []
        for i in range(self.page_count):
            text = self.doc[i].get_text()
            if not text.strip():
                continue
            pages.append({
                "page_index": i,
                "page_label": i + 1,   # human-readable (1-based)
                "text": text.strip(),
                "filename": self.filename,
                "report_type": self.report_type,
                "source_path": str(self.file_path),
                "signals": detect_page_signals(self.doc[i]),
            })
        logger.info(
            f"Extracted {len(pages)} non-empty pages from {self.filename} "
            f"[{self.report_type}]"
        )
        return pages

    def screenshot_page(self, page_number: int) -> Path:
        """Capture a single page at high resolution (300 DPI)."""
        page = self.doc[page_number]
        mat = fitz.Matrix(SCREENSHOT_DPI / 72, SCREENSHOT_DPI / 72)
        pix = page.get_pixmap(matrix=mat)

        safe_hash = hashlib.md5(
            f"{self.filename}_{page_number}".encode()
        ).hexdigest()[:12]
        img_path = self._screenshot_dir / f"p{page_number + 1}_{safe_hash}.png"
        pix.save(str(img_path))

        return img_path

    def close(self):
        self.doc.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def extract_first_page_text(
    file_path: Path,
    max_pages: int = 2,
    min_chars: int = 200,
) -> str:
    """Extract text from the first page(s) of a PDF for classification.

    Uses page 1; if it yields too little text (e.g. a cover image or
    title-only page), extends to the first ``max_pages`` pages until at
    least ``min_chars`` of text are collected.

    Lightweight: opens and closes the document without creating any
    screenshot directories.
    """
    file_path = Path(file_path)
    doc = fitz.open(str(file_path))
    try:
        chunks: list[str] = []
        for i in range(min(max_pages, len(doc))):
            text = doc[i].get_text().strip()
            if text:
                chunks.append(text)
            if sum(len(c) for c in chunks) >= min_chars:
                break
        return "\n".join(chunks).strip()
    finally:
        doc.close()
