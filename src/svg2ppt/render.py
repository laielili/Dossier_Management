"""
Render laid-out deck pages into a PPTX deck (+ HTML preview).

Pipeline:
1. Build a page-level SVG for chrome (banner, borders, side tab, section titles).
2. Render each AI component SVG and the chrome SVG to PNG via PyMuPDF (fitz).
3. Composite chrome + components onto a white canvas with Pillow (page PNGs
   for the inline preview only; avoids fitz nested-SVG/clip-path issues).
4. Assemble the editable PPTX: one chrome picture per slide (background) plus
   one GROUP per component (raster picture + transparent editable text boxes
   overlaid at the SVG text coordinates), 16:9.
5. Emit a simple HTML preview page for human QA.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf as fitz  # PyMuPDF
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from .layout import LayoutEngine, Page
from .schema import svg_natural_size, svg_view_box

# 1 design unit = 0.01 inch -> EMU / point conversions.
EMU_PER_UNIT = 9144  # 914400 EMU per inch / 100
PT_PER_UNIT = 0.72  # 72 pt per inch / 100

# A <text> whose transform is anything but a plain translate is skipped by the
# editable-text overlay (its raster is still correct inside the component PNG).
_TRANSFORM_UNSUPPORTED = re.compile(r"rotate|matrix|scale|skew", re.IGNORECASE)


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


@dataclass
class TextSpan:
    """A single editable text line extracted from a component SVG (viewBox units)."""

    x: float
    y: float
    font_size: float
    fill: str
    weight: str
    anchor: str
    text: str
    font_family: str = ""


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
        editable: bool = True,
    ) -> RenderResult:
        """Render pages to PPTX + HTML preview; return result metadata.

        ``editable=True`` produces the editable deck (chrome background picture
        + one group per component with transparent editable text overlays).
        ``editable=False`` reproduces the legacy output: each slide is a single
        full-page raster picture.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = list(self.layout.warnings)

        page_pngs: list[Path] = []
        for page in pages:
            png_path = output_dir / f"page_{page.index:03d}.png"
            self._render_page_png(page, png_path)
            page_pngs.append(png_path)

        pptx_path = output_dir / filename
        if editable:
            self._build_pptx_editable(pages, output_dir, pptx_path)
        else:
            self._build_pptx_legacy(page_pngs, pptx_path)

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
        """Full-page composite used for the inline preview."""
        canvas = Image.new("RGBA", self.page_size_px, (255, 255, 255, 255))
        self._paint_chrome(canvas, page)
        for region_name in self.layout.regions:
            for placed in page.components.get(region_name, []):
                self._paint_component(canvas, placed)
        canvas.convert("RGB").save(str(png_path), "PNG")

    def _render_chrome_png(self, page: Page, png_path: Path) -> None:
        """White canvas + chrome only (banners, borders, side tab, titles).

        This is the PPTX background layer; components are placed on top as
        individually selectable picture groups.
        """
        canvas = Image.new("RGBA", self.page_size_px, (255, 255, 255, 255))
        self._paint_chrome(canvas, page)
        canvas.convert("RGB").save(str(png_path), "PNG")

    def _render_component_png(self, placed: Any, png_path: Path) -> None:
        """Transparent PNG of one placed component's NON-TEXT raster.

        Every ``<text>`` element is stripped so the PPTX shows each string only
        once: the editable text boxes layered on top of this picture carry all
        text. The label (if any) keeps its reserved slot at the top so the
        graphic sits exactly where the preview paints it; the label text itself
        comes from the overlay text box, never baked in here.

        The SVG raster is saved VERBATIM (un-premultiplied RGBA): PIL's
        ``paste`` into a transparent canvas would premultiply the RGB of
        semi-transparent pixels, which PowerPoint interprets differently and
        renders lighter text edges than the preview.
        """
        w_px = int(round(placed.w * self.px_per_unit))
        h_px = int(round(placed.h * self.px_per_unit))
        svg_no_text = self._strip_text_elements(placed.component.svg)
        comp_img = self._svg_to_image(svg_no_text)
        if placed.component.label and self.layout.labels_enabled:
            lh_px = self._label_to_image(placed.component.label, w_px).height
            comp_img = comp_img.resize((w_px, h_px - lh_px), Image.LANCZOS)
            canvas = Image.new("RGBA", (w_px, h_px), (0, 0, 0, 0))
            layer = Image.new("RGBA", (w_px, h_px), (0, 0, 0, 0))
            # Plain paste (no mask): copies straight alpha pixels verbatim.
            layer.paste(comp_img, (0, lh_px))
            canvas = Image.alpha_composite(canvas, layer)
            canvas.save(str(png_path), "PNG")
        else:
            comp_img = comp_img.resize((w_px, h_px), Image.LANCZOS)
            comp_img.save(str(png_path), "PNG")

    def _strip_text_elements(self, svg_text: str) -> str:
        """Remove every ``<text>`` block from a component SVG.

        The raster keeps shapes/images only; text is provided by the editable
        text boxes in the PPTX group. Mirrors ``_TEXT_SPAN_RE`` so exactly the
        spans that become editable text boxes are removed.
        """
        return self._TEXT_SPAN_RE.sub("", svg_text)

    def _paint_chrome(self, canvas: Image.Image, page: Page) -> None:
        chrome_svg = self._chrome_svg(page)
        chrome_img = self._svg_to_image(chrome_svg)
        if chrome_img.size != canvas.size:
            chrome_img = chrome_img.resize(canvas.size, Image.LANCZOS)
        canvas.paste(chrome_img, (0, 0), chrome_img)

    def _paint_component(self, canvas: Image.Image, placed: Any) -> None:
        x, y, w, h = self._placed_rect_px(placed)
        if placed.component.label and self.layout.labels_enabled:
            label_img = self._label_to_image(placed.component.label, w)
            lh_px = label_img.height
            canvas.paste(label_img, (x, y), label_img)
            y += lh_px
            h -= lh_px
        comp_img = self._svg_to_image(placed.component.svg)
        comp_img = comp_img.resize((w, h), Image.LANCZOS)
        canvas.paste(comp_img, (x, y), comp_img)

    def _placed_rect_px(self, placed: Any) -> tuple[int, int, int, int]:
        x = int(round(placed.x * self.px_per_unit))
        y = int(round(placed.y * self.px_per_unit))
        w = int(round(placed.w * self.px_per_unit))
        h = int(round(placed.h * self.px_per_unit))
        return x, y, w, h

    def _svg_to_image(self, svg_text: str) -> Image.Image:
        """Render an SVG string to a Pillow RGBA image via PyMuPDF."""
        try:
            doc = fitz.open(stream=svg_text.encode("utf-8"), filetype="svg")
            pix = doc[0].get_pixmap(dpi=self.dpi, alpha=True)
            # PyMuPDF pixmap -> PIL Image without intermediate file.
            return Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        except Exception as exc:
            raise RenderError(f"Failed to render SVG to image: {exc}") from exc

    def _label_to_image(self, label: str, width_px: int) -> Image.Image:
        """Render a component's label title line (transparent background)."""
        cfg = self.layout.label_cfg
        fs = float(cfg.get("font_size", 11))
        gap = float(cfg.get("gap", 3))
        color = cfg.get("font_color", self.palette["primary_accent"])
        weight = cfg.get("font_weight", "bold")
        family = self.theme["typography"]["font_family"]
        lh = fs + gap
        height_px = int(round(lh * self.px_per_unit))
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" '
            f'height="{height_px}" viewBox="0 0 {width_px} {lh}" '
            f'font-family="{family}">'
            f'<text x="2" y="{fs - 1:.1f}" font-size="{fs}" fill="{color}" '
            f'font-weight="{weight}">{html.escape(label)}</text></svg>'
        )
        img = self._svg_to_image(svg)
        if img.size != (width_px, height_px):
            img = img.resize((width_px, height_px), Image.LANCZOS)
        return img

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

        # Top-banner divider: separates the stacked title/formula-ref (left)
        # from the right-aligned `description` line.
        div = self.chrome.get("top_banner_divider", {})
        if div.get("enabled") and "top_banner" in regions:
            tb = regions["top_banner"]
            dx = tb.inner_x + round(float(div.get("x_ratio", 0.56)) * tb.inner_w, 2)
            parts.append(
                f'<line x1="{dx}" y1="{tb.y + float(div.get("y_top", 8))}" '
                f'x2="{dx}" y2="{tb.y + float(div.get("y_bottom", 62))}" '
                f'stroke="{div.get("color", self.palette["border_color"])}" '
                f'stroke-width="{float(div.get("stroke_width", 1))}"/>'
            )

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
    # Editable text extraction (component SVG -> TextSpan list)
    # ------------------------------------------------------------------
    # Coordinates are in the component's viewBox units; the PPTX assembly
    # step maps them onto the placed rectangle.

    _TEXT_SPAN_RE = re.compile(r"<text\b([^>]*)>([\s\S]*?)</text>", re.IGNORECASE)
    _SVG_TAG_RE = re.compile(r"<svg\b([^>]*)>", re.IGNORECASE)
    _ATTR_RE = re.compile(r'\b([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"')
    _STYLE_KEY_RE = re.compile(r"(?:^|;)\s*([a-z-]+)\s*:\s*([^;]+)", re.IGNORECASE)
    _TRANSLATE_RE = re.compile(
        r"translate\(([-+\d.eE]+)(?:[\s,]+([-+\d.eE]+))?\)", re.IGNORECASE
    )

    @classmethod
    def _style_value(cls, style: str, key: str) -> str | None:
        key = key.lower()
        for k, v in cls._STYLE_KEY_RE.findall(style):
            if k.lower() == key:
                return v.strip()
        return None

    @classmethod
    def _parse_len(cls, text: str | None) -> float | None:
        if not text:
            return None
        text = text.strip().lower()
        for unit in ("px", "pt", "em", "rem", "ex", "cm", "mm", "in", "%"):
            if text.endswith(unit):
                text = text[: -len(unit)].strip()
                break
        try:
            return float(text)
        except ValueError:
            return None

    def _extract_text_spans(self, svg_text: str) -> list[TextSpan]:
        """Extract editable single-line text spans from a component SVG."""
        root_family = ""
        root = self._SVG_TAG_RE.search(svg_text)
        if root:
            root_family = (
                dict(self._ATTR_RE.findall(root.group(1))).get("font-family", "")
                or ""
            )
        spans: list[TextSpan] = []
        for match in self._TEXT_SPAN_RE.finditer(svg_text):
            raw_attrs = match.group(1)
            inner = match.group(2)
            attrs = dict(self._ATTR_RE.findall(raw_attrs))
            style = attrs.get("style", "")
            text = html.unescape(re.sub(r"<[^>]+>", "", inner))
            # Collapse internal whitespace (including the newlines that pretty-
            # printed inline <tspan>s leave between them) to single spaces.
            # Left intact, an embedded "\n" becomes a hard line break in
            # PowerPoint and a one-line SVG text turns into two stacked lines.
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue

            def _val(key: str) -> str | None:
                return attrs.get(key) or self._style_value(style, key)

            transform = attrs.get("transform")
            dx = dy = 0.0
            if transform:
                t = self._TRANSLATE_RE.search(transform)
                if t:
                    dx = float(t.group(1) or 0)
                    dy = float(t.group(2) or 0)
                elif _TRANSFORM_UNSUPPORTED.search(transform):
                    # Unsupported transform: the raster stays correct, the
                    # overlay is skipped so nothing lands in the wrong place.
                    continue

            fill = (_val("fill") or "#000000").strip()
            if fill in ("none", "transparent") or fill.startswith("url("):
                fill = "#000000"
            weight = (_val("font-weight") or "normal").strip().lower()
            anchor = (_val("text-anchor") or "start").strip().lower()
            family = (_val("font-family") or root_family or "").strip()

            spans.append(
                TextSpan(
                    x=float(attrs.get("x", "0") or 0) + dx,
                    y=float(attrs.get("y", "0") or 0) + dy,
                    font_size=self._parse_len(_val("font-size")) or 14.0,
                    fill=fill,
                    weight=weight,
                    anchor=anchor,
                    text=text,
                    font_family=family,
                )
            )
        return spans

    def _view_box_parts(self, svg_text: str) -> tuple[float, float, float, float]:
        vb = svg_view_box(svg_text)
        if vb:
            parts = vb.replace(",", " ").split()
            if len(parts) == 4:
                try:
                    return tuple(float(p) for p in parts)  # type: ignore[return-value]
                except ValueError:
                    pass
        w, h = svg_natural_size(svg_text)
        return 0.0, 0.0, w, h

    # ------------------------------------------------------------------
    # PPTX assembly (editable deck: chrome background + per-component groups)
    # ------------------------------------------------------------------

    def _build_pptx_editable(
        self, pages: list[Page], output_dir: Path, pptx_path: Path
    ) -> None:
        prs = Presentation()
        prs.slide_width = Inches(self.canvas_w / 100.0)
        prs.slide_height = Inches(self.canvas_h / 100.0)

        blank_layout = prs.slide_layouts[6]  # blank
        for page in pages:
            slide = prs.slides.add_slide(blank_layout)

            # 1. Chrome background layer (banners, borders, side tab, titles).
            chrome_png = output_dir / f"chrome_{page.index:03d}.png"
            self._render_chrome_png(page, chrome_png)
            slide.shapes.add_picture(
                str(chrome_png),
                Emu(0),
                Emu(0),
                width=prs.slide_width,
                height=prs.slide_height,
            )

            # 2. One group per component: raster picture + editable text overlay.
            seq = 0
            for region_name in self.layout.regions:
                for placed in page.components.get(region_name, []):
                    comp_png = output_dir / (
                        f"comp_{page.index:03d}_{region_name}_{seq:02d}.png"
                    )
                    seq += 1
                    self._render_component_png(placed, comp_png)
                    self._add_component_group(slide, placed, comp_png)

        pptx_path.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(pptx_path))

    def _build_pptx_legacy(
        self, page_pngs: list[Path], pptx_path: Path
    ) -> None:
        """Legacy assembly: one full-page raster picture per slide."""
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

    def _add_component_group(self, slide: Any, placed: Any, comp_png: Path) -> None:
        """Insert one component as a group: the raster PNG plus editable text
        boxes on top. The user can drag the whole group or edit its text.

        The group is anchored at the placed component's absolute position:
        ``a:off`` carries the slide-space origin while child shapes (picture +
        text boxes) use coordinates relative to ``a:chOff`` (0, 0). Without
        this, every group sits at the slide top-left corner.
        """
        w_emu = int(placed.w * EMU_PER_UNIT)
        h_emu = int(placed.h * EMU_PER_UNIT)
        x_emu = int(placed.x * EMU_PER_UNIT)
        y_emu = int(placed.y * EMU_PER_UNIT)

        group = slide.shapes.add_group_shape()
        group.shapes.add_picture(
            str(comp_png), Emu(0), Emu(0), width=Emu(w_emu), height=Emu(h_emu)
        )

        # Editable label title line (the PNG reserves its slot, text here).
        if placed.component.label and self.layout.labels_enabled:
            cfg = self.layout.label_cfg
            fs = float(cfg.get("font_size", 11))
            gap = float(cfg.get("gap", 3))
            color = cfg.get("font_color", self.palette["primary_accent"])
            self._add_textbox(
                group,
                left_emu=0,
                top_emu=0,
                width_emu=w_emu,
                height_emu=Emu(int((fs + gap) * EMU_PER_UNIT)),
                font_pt=fs * PT_PER_UNIT,
                fill=color,
                weight=cfg.get("font_weight", "bold"),
                anchor="start",
                text=placed.component.label,
                font_family=self.theme["typography"]["font_family"],
            )

        spans = self._extract_text_spans(placed.component.svg)
        # The label box already carries the title; drop a span that is the
        # very same string, otherwise it would render a duplicate.
        if placed.component.label and self.layout.labels_enabled:
            spans = [
                s for s in spans
                if s.text.strip().lower() != placed.component.label.strip().lower()
            ]
        # Per-span vertical line height = gap to the next baseline, so tightly
        # packed bullets never overlap. Sorting by y makes "next baseline"
        # well-defined even for the multi-column rows that share one y.
        ordered = sorted(spans, key=lambda s: s.y)
        # Summary cards get uniform-width text boxes: every left-aligned body
        # line shares the measured width of the card's longest line, so the
        # editable boxes in PowerPoint look like one aligned block instead of
        # ragged, content-sized strips.
        uniform_w = None
        if placed.component.type == "summary-block":
            uniform_w = self._summary_uniform_width_emu(placed, ordered)
        for span in ordered:
            nxt = None
            for s2 in ordered:
                if s2.y > span.y + 0.01:
                    nxt = s2.y
                    break
            gap_vb = (nxt - span.y) if nxt is not None else None
            self._add_span_textbox(
                group, placed, span, line_gap_vb=gap_vb, force_width=uniform_w
            )

        # Each shape added above recalculates the group extents to the child
        # bounding box and resets a:off to (0, 0), so the group's slide-space
        # anchor must be applied last. a:off carries the placed absolute
        # position; children stay relative to a:chOff (0, 0), a 1:1 child
        # coordinate space (chExt == ext), so nothing is scaled.
        grp_elm = group._element
        xfrm = grp_elm.get_or_add_xfrm()
        xfrm.off.x = x_emu
        xfrm.off.y = y_emu
        xfrm.ext.cx = w_emu
        xfrm.ext.cy = h_emu
        ch_off = grp_elm.chOff
        ch_off.x = 0
        ch_off.y = 0
        ch_ext = grp_elm.chExt
        ch_ext.cx = w_emu
        ch_ext.cy = h_emu

    def _summary_uniform_width_emu(
        self, placed: Any, spans: list[TextSpan]
    ) -> int | None:
        """Uniform text-box width (EMU) for a summary card's left-aligned lines.

        The width is the measured width of the longest line in the card,
        clamped so no box spills past the component's right edge. Returns
        None when the card has no measurable body lines.
        """
        vbx, _, vw, _ = self._view_box_parts(placed.component.svg)
        if vw <= 0:
            return None
        sx = placed.w / vw

        # Measure each line at its own font size (viewBox units -> EMU via sx).
        best_px = 0.0
        for span in spans:
            if span.anchor != "start":
                return None  # mixed anchors: leave layout untouched
            w_pt = self._estimate_text_width_pt(
                span.text, span.font_size, span.weight == "bold", span.font_family
            )
            best_px = max(best_px, (w_pt / PT_PER_UNIT) * sx)
        if best_px <= 0:
            return None
        right_edge = placed.w - min(max((spans[0].x - vbx) * sx, 0), placed.w)
        width_units = min(best_px, max(right_edge, placed.w / 10))
        return int(width_units * EMU_PER_UNIT)

    def _add_span_textbox(
        self,
        group: Any,
        placed: Any,
        span: TextSpan,
        line_gap_vb: float | None = None,
        force_width: int | None = None,
    ) -> None:
        """Map a viewBox-space TextSpan onto the placed component rect and add
        a fixed-size, non-wrapping text box inside the group.

        The font size is pre-shrunk when the text would be wider than the box
        (measured with real font metrics, conservative estimate), so the line
        never spills out of the component: the numbers in the file are final,
        PowerPoint does not need to repair anything.
        """
        vbx, vby, vw, vh = self._view_box_parts(placed.component.svg)
        if vw <= 0 or vh <= 0:
            return
        sx = placed.w / vw
        # The page preview and the component PNG both compress the SVG into the
        # area BELOW the engine-rendered label title: the label slot is reserved
        # at the top and the SVG is scaled to (component height - label slot).
        # The editable text overlay must use that same vertical scale, else its
        # text boxes are mapped taller than the raster and the bottom lines
        # overflow and get clamped on top of the previous line.
        label_slot = 0.0
        if placed.component.label and self.layout.labels_enabled:
            w_px = int(round(placed.w * self.px_per_unit))
            lh_px = self._label_to_image(placed.component.label, w_px).height
            label_slot = lh_px / self.px_per_unit
        avail_h = placed.h - label_slot
        sy = (avail_h / vh) if avail_h > 0 else (placed.h / vh)

        w_total = int(placed.w * EMU_PER_UNIT)
        h_total = int(placed.h * EMU_PER_UNIT)
        x_emu = (span.x - vbx) * sx * EMU_PER_UNIT
        font_pt = span.font_size * sy * PT_PER_UNIT
        family = (span.font_family
                  or self.theme["typography"]["font_family"]).split(",")[0].strip()
        bold = span.weight in ("bold", "bolder", "700", "800", "900")

        if span.anchor == "end":
            left, width, align = 0, max(int(x_emu), 1), PP_ALIGN.RIGHT
        elif span.anchor == "middle":
            left, width, align = 0, w_total, PP_ALIGN.CENTER
        else:
            left, width, align = (
                min(max(int(x_emu), 0), w_total - 1),
                max(w_total - min(max(int(x_emu), 0), w_total - 1), 1),
                PP_ALIGN.LEFT,
            )
        if force_width is not None and align == PP_ALIGN.LEFT:
            # Uniform-width override: keep the line's left edge, replace the
            # ragged content width with the card's shared block width.
            width = min(max(force_width, 1), w_total - left)

        font_pt = self._fit_font_pt(span.text, font_pt, width, bold, family)
        # Line height: prefer the SVG's actual gap to the next baseline so
        # tightly-packed bullets never overlap; cap at 1.3x font and keep at
        # least 1.0x so the glyph is not clipped.
        base_h = int(font_pt * 1.3 * 12700)
        if line_gap_vb is not None and line_gap_vb > 0:
            gap_h = int(line_gap_vb * sy * EMU_PER_UNIT)
            line_h_emu = max(min(base_h, gap_h), int(font_pt * 12700))
        else:
            line_h_emu = base_h

        # SVG text y is the baseline; approximate the box top at ~0.8em above.
        top_emu = (
            (span.y - vby) * sy - span.font_size * sy * 0.8
        ) * EMU_PER_UNIT
        # The component PNG reserves the label slot at its top, so the text
        # overlay shifts down by the same amount (canvas units -> EMU) to stay
        # glued to the graphic. With the corrected vertical scale above, the
        # boxes now fit inside the component and no longer collide.
        if label_slot:
            top_emu += label_slot * EMU_PER_UNIT
        top_emu = min(max(int(top_emu), 0), max(h_total - line_h_emu, 0))

        self._add_textbox(
            group,
            left_emu=Emu(left),
            top_emu=Emu(top_emu),
            width_emu=Emu(width),
            height_emu=Emu(line_h_emu),
            font_pt=font_pt,
            fill=span.fill,
            weight=span.weight,
            anchor=span.anchor,
            text=span.text,
            alignment=align,
            font_family=family,
        )

    def _fit_font_pt(
        self, text: str, font_pt: float, box_width_emu: int, bold: bool, family: str
    ) -> float:
        """Shrink the font size so the measured text width fits the box width.

        Measured with real font metrics (PIL, conservative fallback when no
        matching font file is available). Only long/wide lines are affected;
        normal text keeps its exact preview size. Never grows the font.
        """
        if box_width_emu <= 0 or not text:
            return font_pt
        est_w_pt = self._estimate_text_width_pt(text, font_pt, bold, family)
        avail_w_pt = box_width_emu / 12700.0
        if est_w_pt <= avail_w_pt:
            return font_pt
        scale = (avail_w_pt / est_w_pt) * 0.95
        return max(font_pt * scale, font_pt * 0.6)

    # Font metric helpers (measurement only; no font files are embedded or
    # named in the deck beyond the plain latin typeface set by python-pptx).
    _FONT_PATH_CACHE: dict[tuple[str, bool], str | None] = {}
    _FONT_FACE_CACHE: dict[tuple[str, int], Any] = {}

    @classmethod
    def _font_file_for(cls, family: str, text: str) -> str | None:
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
        key = (family.lower(), has_cjk)
        if key in cls._FONT_PATH_CACHE:
            return cls._FONT_PATH_CACHE[key]
        lower = key[0]
        candidates: list[str] = []
        if "yahei" in lower or "hei" in lower or "\u96c5\u9ed1" in lower:
            candidates.append(r"C:\Windows\Fonts\msyh.ttc")
        elif "song" in lower or "\u5b8b" in lower or "simsun" in lower:
            candidates.append(r"C:\Windows\Fonts\simsun.ttc")
        elif "consol" in lower or "mono" in lower or "courier" in lower:
            candidates.append(r"C:\Windows\Fonts\consola.ttf")
        elif "segoe" in lower:
            candidates.append(r"C:\Windows\Fonts\segoeui.ttf")
        elif "arial" in lower or "helvetica" in lower or "sans" in lower:
            candidates.append(r"C:\Windows\Fonts\arial.ttf")
        elif "times" in lower or "serif" in lower:
            candidates.append(r"C:\Windows\Fonts\times.ttf")
        if has_cjk:
            # Latin fonts have no CJK glyphs; PowerPoint falls back to a CJK
            # font (~1em per glyph). Measure with 雅黑 (msyh.ttc) as proxy,
            # not the latin .notdef box (~0.55em), which under-measures.
            is_cjk_family = any(
                t in lower for t in ("yahei", "hei", "\u96c5\u9ed1", "song", "simsun", "\u5b8b")
            )
            if not is_cjk_family:
                candidates.insert(0, r"C:\Windows\Fonts\msyh.ttc")
        while not candidates:
            candidates.append(
                r"C:\Windows\Fonts\msyh.ttc" if has_cjk else r"C:\Windows\Fonts\arial.ttf"
            )
        from pathlib import Path as _Path
        for path in candidates:
            if _Path(path).exists():
                cls._FONT_PATH_CACHE[key] = path
                return path
        cls._FONT_PATH_CACHE[key] = None
        return None

    @classmethod
    def _estimate_text_width_pt(
        cls, text: str, size_pt: float, bold: bool, family: str
    ) -> float:
        """Estimated rendered width in points of `text` at `size_pt`."""
        try:
            path = cls._font_file_for(family, text)
            if not path:
                raise LookupError
            from PIL import ImageFont
            key = (path, int(size_pt * 4))
            font = cls._FONT_FACE_CACHE.get(key)
            if font is None:
                font = ImageFont.truetype(path, size_pt * 4)
                cls._FONT_FACE_CACHE[key] = font
            width = font.getlength(text) / 4.0
        except Exception:
            # No metrics available: conservative factor-based estimate
            # (CJK glyphs are near-square, latin ~0.5em per char).
            has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
            per_char = 1.0 if has_cjk else 0.55
            width = len(text) * size_pt * max(per_char, 0.5)
        if bold:
            width *= 1.06
        return width

    def _add_textbox(
        self,
        group: Any,
        left_emu: Emu,
        top_emu: Emu,
        width_emu: Emu,
        height_emu: Emu,
        font_pt: float,
        fill: str,
        weight: str,
        anchor: str,
        text: str,
        alignment: PP_ALIGN | None = None,
        font_family: str = "",
    ) -> None:
        """Add a transparent, non-wrapping editable text box to the group.

        Only python-pptx's public API is used (no hand-written XML); the font
        family maps to the plain latin typeface so the rendered width matches
        the metrics used for pre-shrinking.
        """
        box = group.shapes.add_textbox(left_emu, top_emu, width_emu, height_emu)
        tf = box.text_frame
        tf.word_wrap = False
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.vertical_anchor = MSO_ANCHOR.TOP
        tf.margin_left = Emu(0)
        tf.margin_right = Emu(0)
        tf.margin_top = Emu(0)
        tf.margin_bottom = Emu(0)

        paragraph = tf.paragraphs[0]
        if alignment is not None:
            paragraph.alignment = alignment

        run = paragraph.add_run()
        run.text = text
        font = run.font
        font.size = Pt(max(font_pt, 1.0))
        font.bold = weight in ("bold", "bolder", "700", "800", "900")
        try:
            font.color.rgb = RGBColor.from_string(fill.lstrip("#"))
        except ValueError:
            pass
        family = font_family.split(",")[0].strip()
        if family:
            font.name = family

    # ------------------------------------------------------------------
    # Inline preview
    # ------------------------------------------------------------------
    # The deck preview is rendered client-side from the per-page PNGs
    # (see static/svg2ppt.html + svg2ppt-app.js). No HTML file is generated
    # here, so there is nothing to emit for the preview.
