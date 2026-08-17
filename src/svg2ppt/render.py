"""
Render laid-out deck pages into a PPTX deck (+ HTML preview).

Pipeline:
1. Build a page-level SVG for chrome (banner, borders, side tab, section titles).
2. Render each AI component SVG and the chrome SVG to PNG via PyMuPDF (fitz).
3. Composite chrome + components onto a white canvas with Pillow (this avoids
   relying on fitz for nested SVG positioning / clip-path support).
4. Assemble PNG pages into a python-pptx presentation (16:9, one picture per slide).
5. Emit a simple HTML preview page for human QA.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from PIL import Image
from pptx import Presentation
from pptx.util import Emu, Inches

from .layout import LayoutEngine, Page


class RenderError(RuntimeError):
    """Raised when page rendering or PPTX assembly fails."""


@dataclass
class RenderResult:
    """Paths and metadata for a rendered deck."""

    pptx_path: Path
    preview_path: Path | None
    page_count: int
    page_pngs: list[Path]
    warnings: list[str]


class DeckRenderer:
    """Render pages to PPTX and HTML preview."""

    def __init__(self, layout_engine: LayoutEngine | None = None, dpi: int = 150):
        self.layout = layout_engine or LayoutEngine()
        self.dpi = dpi
        self.theme = self.layout.template["theme"]
        self.chrome = self.layout.template.get("chrome", {})
        self.palette = self.theme["palette"]
        self.canvas_w = float(self.layout.template["canvas"]["width"])
        self.canvas_h = float(self.layout.template["canvas"]["height"])
        # 1 design unit = 0.01 inch, so pixels per design unit = dpi / 100.
        self.px_per_unit = self.dpi / 100.0
        self.page_size_px = (
            int(round(self.canvas_w * self.px_per_unit)),
            int(round(self.canvas_h * self.px_per_unit)),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(
        self,
        pages: list[Page],
        output_dir: str | Path,
        filename: str = "synthesis_deck.pptx",
    ) -> RenderResult:
        """Render pages to PPTX + HTML preview; return result metadata."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = list(self.layout.warnings)

        page_pngs: list[Path] = []
        for page in pages:
            png_path = output_dir / f"page_{page.index:03d}.png"
            self._render_page_png(page, png_path)
            page_pngs.append(png_path)

        pptx_path = output_dir / filename
        self._build_pptx(page_pngs, pptx_path)

        preview_path = None

        return RenderResult(
            pptx_path=pptx_path,
            preview_path=preview_path,
            page_count=len(pages),
            page_pngs=page_pngs,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Page rendering (Pillow composite)
    # ------------------------------------------------------------------

    def _render_page_png(self, page: Page, png_path: Path) -> None:
        canvas = Image.new("RGBA", self.page_size_px, (255, 255, 255, 255))

        # 1. Chrome (banners, side tab, borders, titles).
        chrome_svg = self._chrome_svg(page)
        chrome_img = self._svg_to_image(chrome_svg)
        if chrome_img.size != canvas.size:
            chrome_img = chrome_img.resize(canvas.size, Image.LANCZOS)
        canvas.paste(chrome_img, (0, 0), chrome_img)

        # 2. Components per region.
        for region_name in self.layout.regions:
            for placed in page.components.get(region_name, []):
                comp_img = self._svg_to_image(placed.component.svg)
                target_box = self._placed_rect_px(placed)
                comp_img = comp_img.resize(
                    (target_box[2], target_box[3]), Image.LANCZOS
                )
                canvas.paste(comp_img, (target_box[0], target_box[1]), comp_img)

        # Convert to RGB before saving (PPTX readers prefer RGB PNG).
        canvas.convert("RGB").save(str(png_path), "PNG")

    def _placed_rect_px(self, placed: Any) -> tuple[int, int, int, int]:
        x = int(round(placed.x * self.px_per_unit))
        y = int(round(placed.y * self.px_per_unit))
        w = int(round(placed.w * self.px_per_unit))
        h = int(round(placed.h * self.px_per_unit))
        return x, y, w, h

    def _svg_to_image(self, svg_text: str) -> Image.Image:
        """Render an SVG string to a Pillow RGBA image via fitz."""
        try:
            doc = fitz.open(stream=svg_text.encode("utf-8"), filetype="svg")
            pix = doc[0].get_pixmap(dpi=self.dpi, alpha=True)
            # fitz pixmap -> PIL Image without intermediate file.
            return Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        except Exception as exc:
            raise RenderError(f"Failed to render SVG to image: {exc}") from exc

    # ------------------------------------------------------------------
    # Chrome SVG
    # ------------------------------------------------------------------

    def _chrome_svg(self, page: Page) -> str:
        cw, ch = self.canvas_w, self.canvas_h
        parts: list[str] = []
        parts.append(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{cw}" height="{ch}" '
            f'viewBox="0 0 {cw} {ch}" font-family="{self.theme["typography"]["font_family"]}">'
        )

        regions = self.layout.regions

        # Background fills for banner / meta row / side tab.
        for name in ("top_banner", "meta_row"):
            r = regions[name]
            parts.append(
                f'<rect x="{r.x}" y="{r.y}" width="{r.w}" height="{r.h}" '
                f'fill="{r.background}"/>'
            )

        borders_cfg = self.chrome.get("borders", {})
        stroke = borders_cfg.get("color", self.palette["border_color"])
        stroke_w = borders_cfg.get("stroke_width", 1)
        draw_borders = borders_cfg.get("enabled", True)

        # Side tab (inside left_column).
        side = self.chrome.get("side_tab", {})
        if side.get("enabled"):
            left = regions["left_column"]
            tab_w = float(side.get("width", 24))
            label = side.get("label", "PROJECT DOSSIER")
            bg = side.get("background", self.palette["tab_background"])
            fg = side.get("text_color", "#ffffff")
            fs = float(side.get("font_size", 12))
            parts.append(
                f'<rect x="{left.x}" y="{left.y}" width="{tab_w}" height="{left.h}" '
                f'fill="{bg}"/>'
            )
            cx = left.x + tab_w / 2
            cy = left.y + left.h / 2
            parts.append(
                f'<text x="{cx}" y="{cy}" font-size="{fs}" fill="{fg}" '
                f'text-anchor="middle" dominant-baseline="middle" '
                f'transform="rotate(-90 {cx} {cy})">{html.escape(label)}</text>'
            )

        # Region borders + section titles.
        titles_cfg = self.chrome.get("section_titles", {})
        draw_titles = titles_cfg.get("enabled", True)
        title_fs = titles_cfg.get("font_size", 15)
        title_color = titles_cfg.get("font_color", self.palette["primary_accent"])
        title_weight = titles_cfg.get("font_weight", "bold")
        title_offset = titles_cfg.get("offset_y", 18)

        for name, r in regions.items():
            if draw_borders:
                parts.append(
                    f'<rect x="{r.x}" y="{r.y}" width="{r.w}" height="{r.h}" '
                    f'fill="none" stroke="{stroke}" stroke-width="{stroke_w}"/>'
                )
            if draw_titles and r.title:
                tx = r.inner_x
                ty = r.inner_y + title_offset
                parts.append(
                    f'<text x="{tx}" y="{ty}" font-size="{title_fs}" fill="{title_color}" '
                    f'font-weight="{title_weight}">{html.escape(r.title)}</text>'
                )

        parts.append("</svg>")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # PPTX assembly
    # ------------------------------------------------------------------

    def _build_pptx(self, page_pngs: list[Path], pptx_path: Path) -> None:
        prs = Presentation()
        prs.slide_width = Inches(self.canvas_w / 100.0)
        prs.slide_height = Inches(self.canvas_h / 100.0)

        blank_layout = prs.slide_layouts[6]  # blank
        for png in page_pngs:
            slide = prs.slides.add_slide(blank_layout)
            slide.shapes.add_picture(
                str(png),
                Emu(0),
                Emu(0),
                width=prs.slide_width,
                height=prs.slide_height,
            )

        pptx_path.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(pptx_path))

    # ------------------------------------------------------------------
    # Inline preview
    # ------------------------------------------------------------------
    # The deck preview is rendered client-side from the per-page PNGs
    # (see static/svg2ppt.html + svg2ppt-app.js). No HTML file is generated
    # here, so there is nothing to emit for the preview.
