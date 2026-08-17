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
from .schema import Component, DeckXML, SchemaError


class DeckBuilder:
    """High-level orchestrator: XML in, PPTX + preview HTML out."""

    def __init__(
        self,
        template_path: str | Path | None = None,
        dpi: int = 150,
        max_pages: int | None = None,
    ):
        self.layout_engine = LayoutEngine(
            template_path, max_pages=max_pages
        )
        self.renderer = DeckRenderer(self.layout_engine, dpi=dpi)

    def build_from_string(
        self,
        xml_text: str,
        output_dir: str | Path,
        filename: str = "synthesis_deck.pptx",
    ) -> RenderResult:
        deck = DeckXML.from_string(xml_text)
        pages = self.layout_engine.layout(deck.components)
        return self.renderer.render(pages, output_dir, filename=filename)

    def build_from_file(
        self,
        xml_path: str | Path,
        output_dir: str | Path,
        filename: str = "synthesis_deck.pptx",
    ) -> RenderResult:
        deck = DeckXML.from_file(xml_path)
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
