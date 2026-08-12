"""
PDF generation module — reportlab

Produces a single synthesis-input PDF for a downstream multimodal LLM.
Each selected page becomes ONE page containing a short provenance
annotation (source / type / page) plus a high-resolution screenshot.
The raw page text is deliberately NOT embedded — the screenshot is the
authoritative visual and the raw extraction only adds noise (diagram
fragments, numbering, leaked paths) that distracts the LLM.
"""

from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# CJK-capable font so the target-formula banner renders Chinese correctly
# (Helvetica cannot). Falls back to Helvetica-Bold if the CID font is
# unavailable (Chinese would then be dropped from the banner).
_CJK_FONT = "STSong-Light"
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
    _CJK_AVAILABLE = True
except Exception as e:  # pragma: no cover - environment without CJK CMaps
    logger.warning(f"CJK font '{_CJK_FONT}' unavailable, Chinese may not render: {e}")
    _CJK_AVAILABLE = False

from .config import (
    OUTPUT_DIR,
    REPORT_TYPES,
    REPORT_TYPE_LABELS,
    PROJECT_ROOT,
    TARGET_FORMULA_BANNER_TEMPLATE,
)
from .logger import get_logger

logger = get_logger(__name__)

# --- Page dimensions ---
PAGE_W, PAGE_H = landscape(A4)
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# reportlab's SimpleDocTemplate frame adds a 6pt padding on every side, so
# the *inner* drawable area is the margin box minus 2*FRAME_PAD on each axis.
# Sizing the full-page table against CONTENT_W/CONTENT_H (which ignores this
# padding) is what previously pushed the image onto the next page.
_FRAME_PAD = 6
USABLE_W = PAGE_W - 2 * MARGIN - 2 * _FRAME_PAD
USABLE_H = PAGE_H - 2 * MARGIN - 2 * _FRAME_PAD


class PDFGenerator:
    """Generates the final synthesis input PDF from summary-page data."""

    def __init__(self):
        self._styles = self._build_styles()

    def _build_styles(self) -> dict:
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "SynTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            spaceAfter=14,
        )
        heading_style = ParagraphStyle(
            "SynHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            spaceBefore=20,
            spaceAfter=8,
            textColor="#1a1a2e",
        )
        sub_heading_style = ParagraphStyle(
            "SynSubHeading",
            parent=styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=12,
            spaceBefore=14,
            spaceAfter=6,
            textColor="#16213e",
        )
        body_style = ParagraphStyle(
            "SynBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            spaceAfter=6,
        )
        meta_style = ParagraphStyle(
            "SynMeta",
            parent=styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8,
            textColor="#888888",
        )
        return {
            "title": title_style,
            "heading": heading_style,
            "sub_heading": sub_heading_style,
            "body": body_style,
            "meta": meta_style,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        project_id: str,
        summary_pages: list[dict],
        project_owner: str = "",
        target_formula: str = "",
    ) -> Path:
        """Build the synthesis PDF.

        Args:
            project_id: e.g. "PROJ-001"
            summary_pages: list of dicts:
                {
                    "text": str,
                    "screenshot": Path | str,
                    "filename": str,
                    "report_type": str ("CLINS" / "FE" / "CE"),
                    "page_label": int,
                    "source_path": str,
                }
            target_formula: user-supplied final target formula. When set, it
                is rendered as a highlighted banner on the cover (metadata
                injection) to anchor the downstream AI.

        Returns:
            Path to the generated PDF.
        """
        output_path = OUTPUT_DIR / f"synthesis_input_{project_id}.pdf"

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=landscape(A4),
            rightMargin=MARGIN,
            leftMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN,
        )
        story = []

        # --- Cover page ---
        self._add_cover(
            story, project_id, summary_pages, project_owner, target_formula
        )
        # cover already ends with a PageBreak

        # --- Group by report_type, then sort by filename ---
        groups = self._group_and_sort(summary_pages)

        for report_type in REPORT_TYPES:
            items = groups.get(report_type, [])
            if not items:
                continue

            for idx, item in enumerate(items, 1):
                self._add_report_page(story, item, idx)
                story.append(PageBreak())

        doc.build(story)
        logger.info(
            f"PDF generated: {output_path} "
            f"({len(summary_pages)} summary pages)"
        )
        return output_path

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------

    def _add_cover(
        self,
        story: list,
        project_id: str,
        summary_pages: list[dict],
        project_owner: str = "",
        target_formula: str = "",
    ):
        story.append(Paragraph("Synthesis Input", self._styles["title"]))
        story.append(Paragraph(
            f"Project: {project_id}",
            self._styles["sub_heading"],
        ))
        if project_owner:
            story.append(Paragraph(
                f"Project Owner: {project_owner}",
                self._styles["sub_heading"],
            ))
        story.append(Spacer(1, 6 * mm))

        # --- Metadata injection: target-formula banner ------------------
        # Anchors the downstream AI on the final target formula so it does
        # not drift across the multiple formulas present in the dossiers.
        if target_formula:
            banner_text = TARGET_FORMULA_BANNER_TEMPLATE.format(
                formula=target_formula
            )
            banner_style = ParagraphStyle(
                "SynBanner",
                parent=self._styles["body"],
                fontName=_CJK_FONT if _CJK_AVAILABLE else "Helvetica-Bold",
                fontSize=11,
                leading=15,
                textColor="#7a1f1f",
            )
            banner_tbl = Table(
                [[Paragraph(banner_text, banner_style)]],
                colWidths=[USABLE_W],
            )
            banner_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), "#fdeaea"),
                ("BOX", (0, 0), (-1, -1), 0.75, "#c0392b"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(banner_tbl)
            story.append(Spacer(1, 6 * mm))

        # Count by type
        type_counts = Counter(
            p.get("report_type", "?") for p in summary_pages
        )
        meta_parts = [f"Total summary pages: {len(summary_pages)}"]
        for rt in REPORT_TYPES:
            if type_counts.get(rt):
                meta_parts.append(f"{rt}: {type_counts[rt]}")
        meta_parts.append(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        story.append(Paragraph(" | ".join(meta_parts), self._styles["meta"]))
        story.append(Spacer(1, 6 * mm))

        story.append(PageBreak())

    def _add_report_page(
        self,
        story: list,
        item: dict,
        index: int,
    ) -> None:
        """Append one report page (screenshot + bottom annotation) to the story.

        The output PDF is landscape A4 landscape. Each page is a single
        fixed-height Table containing the screenshot on TOP and the
        provenance annotation pinned to the BOTTOM of the page. Keeping the
        annotation and the screenshot inside ONE flowable of exactly the
        content height guarantees two things:

          * The comment always stays on the SAME page as its screenshot.
            (Previously the comment was placed at the top and the image in a
            separate fixed-height table, so when the comment wrapped to two
            lines the image no longer fit beside it and was pushed onto the
            next page — orphaning the comment onto a different page.)
            * The comment is allowed to WRAP to as many lines as needed instead
            of being truncated with an ellipsis, so the downstream AI sees the
            full provenance (source / type / page).
        """
        filename = item.get("filename", "unknown.pdf")
        report_type = item.get("report_type", "?")
        page_label = item.get("page_label", "?")
        source_rel = self._relative_source(item.get("source_path", ""))

        ann_text = (
            f"#{index}  [{report_type}]  {filename}  —  Page {page_label}"
            f"  |  Source: {source_rel}"
        )

        # Annotation pinned to the BOTTOM of the page. It wraps freely to as
        # many lines as needed — no truncation, no forced single line.
        ann_style = ParagraphStyle(
            "SynFooter",
            parent=self._styles["meta"],
            fontName="Helvetica-Oblique",
            fontSize=7,
            leading=9,
            textColor="#888888",
            alignment=0,
        )
        ann_para = Paragraph(ann_text, ann_style)

        content_h = USABLE_H
        # Measure the wrapped height so we reserve only what the text needs.
        # This keeps the screenshot as large as possible while never letting
        # the annotation get clipped (the text box is grown, not folded).
        _aw, ann_h = ann_para.wrap(USABLE_W, content_h)
        ann_h = max(ann_h, 9)            # at least one line tall
        max_ann_h = content_h * 0.35     # hard guard so the image keeps 65%+
        ann_h = min(ann_h, max_ann_h)

        # Leave a 1pt sliver so the full-page table is guaranteed to fit and
        # never overflows into the next page (which would create a blank page
        # before it).
        img_row_h = (content_h - 1) - ann_h

        screenshot = item.get("screenshot")
        ss_path = Path(screenshot) if screenshot else None

        if ss_path and ss_path.exists():
            try:
                with Image.open(ss_path) as img:
                    w, h = img.size

                scale = min(USABLE_W / w, img_row_h / h) if (w and h) else 1.0
                img_w, img_h = w * scale, h * scale

                rl_img = RLImage(str(ss_path), width=img_w, height=img_h)
            except Exception as e:
                logger.error(f"Failed to embed screenshot {ss_path}: {e}")
                rl_img = Paragraph(
                    f"[Screenshot unavailable: {e}]",
                    self._styles["meta"],
                )
        else:
            rl_img = Paragraph(
                "[Screenshot unavailable]",
                self._styles["meta"],
            )

        # Two-row, full-height table: screenshot (top, centred/middle) +
        # annotation (bottom, left-aligned, wraps to multiple lines).
        page_tbl = Table(
            [[rl_img], [ann_para]],
            colWidths=[USABLE_W],
            rowHeights=[img_row_h, ann_h],
        )
        page_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
            ("VALIGN", (0, 1), (0, 1), "BOTTOM"),
            ("ALIGN", (0, 0), (0, 0), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(page_tbl)

    def _relative_source(self, source: str) -> str:
        """Return a clean project-relative path (e.g. data/CLINS/xxx.pdf).

        Avoids leaking the absolute local filesystem path into the output.
        """
        if not source:
            return ""
        try:
            rel = Path(source).resolve().relative_to(PROJECT_ROOT.resolve())
            return rel.as_posix()
        except ValueError:
            return Path(source).name

    def _group_and_sort(
        self,
        summary_pages: list[dict],
    ) -> dict[str, list[dict]]:
        """Group pages by report_type and sort alphabetically by filename."""
        groups: dict[str, list[dict]] = {rt: [] for rt in REPORT_TYPES}
        for page in summary_pages:
            rt = page.get("report_type", "UNKNOWN")
            if rt in groups:
                groups[rt].append(page)

        for rt in groups:
            groups[rt].sort(key=lambda p: p.get("filename", "").lower())

        return groups
