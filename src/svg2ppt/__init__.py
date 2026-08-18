"""
svg2ppt: SVG components -> 5-region layout -> PPTX synthesis deck.

Minimal usage:

    from svg2ppt import DeckBuilder

    builder = DeckBuilder(template_path=None, dpi=150)
    result = builder.build_from_file(
        xml_path="output/deck_input.xml",
        output_dir="output/my_deck",
    )
    print(result.pptx_path, result.page_count)

The module intentionally does not know about FastAPI or the legacy html2pptx
pipeline; those bindings live in src/api.py when added later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .layout import LayoutEngine
from .render import DeckRenderer, RenderResult
from .schema import Component, DeckXML, SchemaError, scale_svg_fonts


class DeckBuilder:
    """High-level orchestrator: XML in, PPTX + preview HTML out.

    ``overrides`` is a debug/theme dict applied at build time (never persisted):
      - theme_overrides, chrome_font_scale, margin_scale -> LayoutEngine (template)
      - content_font_scale -> uniform scale of every component SVG (drives pages)
    """

    def __init__(
        self,
        template_path: str | Path | None = None,
        dpi: int = 150,
        max_pages: int | None = None,
        overrides: dict | None = None,
    ):
        overrides = overrides or {}
        # Content font scale is component-level; separate it from the layout
        # overrides so LayoutEngine only sees template-level keys.
        self.content_font_scale = self._clamp_scale(
            overrides.get("content_font_scale", 1.0)
        )
        layout_overrides = {k: v for k, v in overrides.items() if k != "content_font_scale"}
        self.layout_engine = LayoutEngine(
            template_path, max_pages=max_pages, overrides=layout_overrides
        )
        self.renderer = DeckRenderer(self.layout_engine, dpi=dpi)

    @staticmethod
    def _clamp_scale(value: Any, lo: float = 0.5, hi: float = 2.0) -> float:
        try:
            return max(lo, min(hi, float(value)))
        except (TypeError, ValueError):
            return 1.0

    def _apply_content_scale(self, deck: DeckXML) -> None:
        """Scale every component's SVG uniformly (font-size + geometry)."""
        if self.content_font_scale == 1.0:
            return
        for comp in deck.components:
            comp.svg = scale_svg_fonts(comp.svg, self.content_font_scale)

    def build_from_string(
        self,
        xml_text: str,
        output_dir: str | Path,
        filename: str = "synthesis_deck.pptx",
    ) -> RenderResult:
        deck = DeckXML.from_string(xml_text)
        self._apply_content_scale(deck)
        pages = self.layout_engine.layout(deck.components)
        return self.renderer.render(pages, output_dir, filename=filename)

    def build_from_file(
        self,
        xml_path: str | Path,
        output_dir: str | Path,
        filename: str = "synthesis_deck.pptx",
    ) -> RenderResult:
        deck = DeckXML.from_file(xml_path)
        self._apply_content_scale(deck)
        pages = self.layout_engine.layout(deck.components)
        return self.renderer.render(pages, output_dir, filename=filename)


__all__ = [
    "DeckBuilder",
    "DeckRenderer",
    "LayoutEngine",
    "RenderResult",
    "Component",
    "DeckXML",
    "SchemaError",
]
