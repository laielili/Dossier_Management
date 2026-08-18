"""
5-region layout engine.

The layout engine owns all spatial decisions. It reads the template, routes
components by type into regions, stacks them vertically, and paginates the
overflow region (middle_column by default) into continuation pages.

Page-cap semantics
------------------
``max_pages`` is a *hard* cap:

* ``max_pages == 0`` (or unset) -> add-pages mode: content overflows into as
  many continuation pages as needed, fidelity preserved.
* ``max_pages > 0`` -> compress mode (HARD cap): the overflow region is shrunk
  until the deck fits within ``max_pages``. The single **density factor
  ``f``** drives, in lockstep: (a) font-size + vertical geometry, (b) the
  inter-component ``gap``, and (c) intra-card horizontal whitespace (content
  affine-mapped to fill the card width with a reduced inset). ``f`` is
  binary-searched in ``[FONT_FLOOR, 1.0]`` to hit the cap exactly. The
  geometric (uniform-scale) path was removed — this is the only compression
  mode. If even ``FONT_FLOOR`` cannot meet the cap, the best effort is
  returned with a warning.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import Component, scale_svg_fonts, svg_natural_size

# Density factor floor. max_pages is a *hard* cap: the engine compresses the
# overflow region (font-size + vertical geometry + inter-component gap + intra
# -card horizontal whitespace) down to this factor to reach the cap. 0.15 is a
# near-unbounded floor — readability is intentionally sacrificed when required
# to honor max_pages. If even this floor cannot meet the cap, the best-effort
# pages are returned with a warning (not silently overflowing, not an error).
FONT_FLOOR = 0.15


class LayoutError(ValueError):
    """Raised when components cannot be laid out under the template rules."""


@dataclass
class Region:
    """Resolved region geometry (design units)."""

    name: str
    x: float
    y: float
    w: float
    h: float
    title: str | None
    title_height: float
    background: str
    show_border: bool
    padding: list[float]  # [top, right, bottom, left]

    @property
    def inner_x(self) -> float:
        return self.x + self.padding[3]

    @property
    def inner_y(self) -> float:
        return self.y + self.padding[0]

    @property
    def inner_w(self) -> float:
        return self.w - self.padding[1] - self.padding[3]

    @property
    def inner_h(self) -> float:
        return self.h - self.padding[0] - self.padding[2]

    @property
    def content_y(self) -> float:
        """Y coordinate where the first component should start."""
        return self.inner_y + self.title_height

    @property
    def content_h(self) -> float:
        """Available vertical space for components after reserving title."""
        return max(0.0, self.inner_h - self.title_height)


@dataclass
class PlacedComponent:
    """A component assigned to a concrete region rectangle on a page."""

    component: Component
    region: str
    x: float
    y: float
    w: float
    h: float
    scale: float = 1.0
    page_index: int = 0


@dataclass
class Page:
    """A single deck page containing placed components grouped by region."""

    index: int
    components: dict[str, list[PlacedComponent]] = field(default_factory=dict)
    is_continuation: bool = False


class LayoutEngine:
    """Lay out an AI-provided component list according to a 5-region template."""

    def __init__(
        self,
        template_path: str | Path | None = None,
        max_pages: int | None = None,
        overrides: dict | None = None,
    ):
        if template_path is None:
            here = Path(__file__).parent
            template_path = here / "templates" / "deck_5region.json"
        self.template_path = Path(template_path)
        self.template = self._load_template()
        # Apply debug/theme overrides to the in-memory template copy BEFORE
        # building regions, so region geometry, padding, gap and chrome all
        # reflect the requested look. This never touches the template file.
        self._apply_overrides(overrides)
        self.regions = self._build_regions()
        self.routing = self.template["routing"]
        self.gap = float(self.template["stacking"]["default_gap"])
        # Template provides the defaults; callers may override per build.
        self.max_pages = self.template["stacking"].get("max_pages")
        if max_pages is not None:
            # 0 / negative means "no cap" — add-pages mode.
            self.max_pages = max_pages if max_pages > 0 else None
        self.overflow_region = self.template["pagination"]["overflow_region"]
        self.repeat_regions = set(self.template["pagination"]["repeat_regions"])
        # Engine-side warnings (e.g. readability floor hit) surfaced to the client.
        self.warnings: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def layout(self, components: list[Component]) -> list[Page]:
        """Route, stack and paginate components into pages."""
        grouped = self._route_components(components)

        # Compute natural footprints (width = region inner width, height preserves aspect).
        sized: dict[str, list[tuple[Component, float, float]]] = {}
        for region_name, comps in grouped.items():
            region = self.regions[region_name]
            sized[region_name] = [
                (c, *self._component_size(c, region.inner_w)) for c in comps
            ]

        # First page allocation. Repeat-region content must fit on page 1.
        first_page, unconsumed = self._build_first_page(sized)
        pages: list[Page] = [first_page]

        # Stash repeat-region placements to clone onto continuation pages.
        repeat_placements = {
            r: first_page.components.get(r, []) for r in self.repeat_regions
        }

        # Continuation pages for non-repeat regions.
        while any(unconsumed.get(r) for r in unconsumed if r not in self.repeat_regions):
            page = Page(index=len(pages) + 1, is_continuation=True)
            for region_name in self.regions:
                if region_name in self.repeat_regions:
                    page.components[region_name] = [
                        PlacedComponent(
                            component=p.component,
                            region=p.region,
                            x=p.x,
                            y=p.y,
                            w=p.w,
                            h=p.h,
                            scale=p.scale,
                            page_index=len(pages) + 1,
                        )
                        for p in repeat_placements.get(region_name, [])
                    ]
                else:
                    page.components[region_name] = self._consume_region(
                        region_name, unconsumed, len(pages) + 1
                    )
            pages.append(page)

        # Hard page-cap handling.
        pages = self._apply_page_cap(pages, sized)

        # Finalize indices and continuation labels.
        for idx, page in enumerate(pages, start=1):
            page.index = idx
            page.is_continuation = idx > 1
            for region_comps in page.components.values():
                for p in region_comps:
                    p.page_index = idx

        return pages

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def _load_template(self) -> dict[str, Any]:
        if not self.template_path.exists():
            raise LayoutError(f"Template not found: {self.template_path}")
        return json.loads(self.template_path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Debug / theme overrides (in-memory only — template file is untouched)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_hex_color(value: str) -> bool:
        return isinstance(value, str) and bool(
            re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value.strip())
        )

    def _apply_overrides(self, overrides: dict | None) -> None:
        """Mutate ``self.template`` with debug/theme overrides.

        Only *structural / theme* colors are mutable; the data-status palette
        (green/orange/red/neutral) is intentionally left untouched so the
        signal semantics survive. ``content_font_scale`` is applied per
        component by DeckBuilder and is ignored here.
        """
        if not overrides:
            return

        theme_ov = overrides.get("theme_overrides") or {}
        if isinstance(theme_ov, dict):
            palette = self.template.setdefault("theme", {}).setdefault("palette", {})
            chrome = self.template.setdefault("chrome", {})
            regions = self.template.setdefault("regions", {})
            # Each UI swatch maps to one or more template keys.
            if self._is_hex_color(theme_ov.get("primary_accent", "")):
                palette["primary_accent"] = theme_ov["primary_accent"]
                chrome.setdefault("section_titles", {})["font_color"] = theme_ov[
                    "primary_accent"
                ]
            if self._is_hex_color(theme_ov.get("header_background", "")):
                palette["header_background"] = theme_ov["header_background"]
                if "top_banner" in regions:
                    regions["top_banner"]["background"] = theme_ov["header_background"]
                if "meta_row" in regions:
                    regions["meta_row"]["background"] = theme_ov["header_background"]
            if self._is_hex_color(theme_ov.get("tab_background", "")):
                palette["tab_background"] = theme_ov["tab_background"]
                chrome.setdefault("side_tab", {})["background"] = theme_ov["tab_background"]
            if self._is_hex_color(theme_ov.get("border_color", "")):
                palette["border_color"] = theme_ov["border_color"]
                chrome.setdefault("borders", {})["color"] = theme_ov["border_color"]
            if self._is_hex_color(theme_ov.get("text_color", "")):
                palette["text_color"] = theme_ov["text_color"]

        # Chrome font scale (cosmetic only — does not change pagination).
        cf = overrides.get("chrome_font_scale")
        if cf not in (None, 1.0):
            try:
                cf = max(0.5, min(2.0, float(cf)))
            except (TypeError, ValueError):
                cf = None
        if cf not in (None, 1.0):
            typ = self.template["theme"].setdefault("typography", {})
            for k in (
                "base_font_size",
                "table_font_size",
                "title_font_size",
                "section_title_font_size",
            ):
                if k in typ:
                    typ[k] = round(typ[k] * cf, 3)
            st = self.template["chrome"].setdefault("section_titles", {})
            if "font_size" in st:
                st["font_size"] = round(st["font_size"] * cf, 3)
            stb = self.template["chrome"].setdefault("side_tab", {})
            if "font_size" in stb:
                stb["font_size"] = round(stb["font_size"] * cf, 3)

        # Global margin scale: every region padding + the inter-component gap.
        ms = overrides.get("margin_scale")
        if ms not in (None, 1.0):
            try:
                ms = max(0.5, min(2.0, float(ms)))
            except (TypeError, ValueError):
                ms = None
        if ms not in (None, 1.0):
            for r in self.template["regions"].values():
                pad = r.get("padding")
                if pad:
                    r["padding"] = [round(p * ms, 3) for p in pad]
            self.template.setdefault("stacking", {})["default_gap"] = round(
                float(self.template.get("stacking", {}).get("default_gap", 8)) * ms, 3
            )

    def _build_regions(self) -> dict[str, Region]:
        regions: dict[str, Region] = {}
        titles_cfg = self.template.get("chrome", {}).get("section_titles", {})
        title_offset = float(titles_cfg.get("offset_y", 18))
        title_font_size = float(titles_cfg.get("font_size", 15))

        chrome = self.template.get("chrome", {})
        side_tab_w = 0.0
        if chrome.get("side_tab", {}).get("enabled"):
            side_tab_w = float(chrome["side_tab"].get("width", 24))

        for name, data in self.template["regions"].items():
            title = data.get("title")
            title_height = 0.0
            if title:
                # Reserve space for the chrome section title (baseline + descender buffer).
                title_height = title_offset + title_font_size + 4

            padding = [float(v) for v in data.get("padding", [0, 0, 0, 0])]
            if name == "left_column" and side_tab_w:
                # Push content past the vertical side tab.
                padding[3] += side_tab_w

            regions[name] = Region(
                name=name,
                x=float(data["x"]),
                y=float(data["y"]),
                w=float(data["w"]),
                h=float(data["h"]),
                title=title,
                title_height=title_height,
                background=data.get("background", "#ffffff"),
                show_border=bool(data.get("show_border", True)),
                padding=padding,
            )
        return regions

    # ------------------------------------------------------------------
    # Routing & sizing
    # ------------------------------------------------------------------

    def _route_components(self, components: list[Component]) -> dict[str, list[Component]]:
        grouped: dict[str, list[Component]] = {name: [] for name in self.regions}
        for comp in components:
            region = self._resolve_region(comp)
            grouped[region].append(comp)
        return grouped

    def _resolve_region(self, comp: Component) -> str:
        # Explicit escape hatch on <component region="...">.
        if "region" in comp.attrs and comp.attrs["region"] in self.regions:
            return comp.attrs["region"]
        if comp.type in self.routing:
            return self.routing[comp.type]
        raise LayoutError(
            f"Component type {comp.type!r} is not in routing table and has no explicit region"
        )

    def _component_size(self, comp: Component, target_width: float) -> tuple[float, float]:
        """Return (width, height) for a component filling ``target_width``."""
        w, h = svg_natural_size(comp.svg)
        if w <= 0 or h <= 0:
            return target_width, 10.0
        return target_width, target_width * (h / w)

    # ------------------------------------------------------------------
    # Page building
    # ------------------------------------------------------------------

    def _build_first_page(
        self,
        sized: dict[str, list[tuple[Component, float, float]]],
        gap_override: float | None = None,
    ) -> tuple[Page, dict[str, list[tuple[Component, float, float]]]]:
        page = Page(index=1, is_continuation=False)
        unconsumed: dict[str, list[tuple[Component, float, float]]] = {}

        for region_name in self.regions:
            region = self.regions[region_name]
            items = sized.get(region_name, [])
            # Only the overflow region is allowed to tighten its gap.
            gap = gap_override if region_name == self.overflow_region else None
            placed, leftover = self._fit_items(region, items, gap)
            page.components[region_name] = placed
            unconsumed[region_name] = leftover

            # Repeat-region overflow is a hard error: content must fit page 1 unchanged.
            if region_name in self.repeat_regions and leftover:
                total = sum(h for _, _, h in items)
                raise LayoutError(
                    f"Repeat region {region_name!r} overflows page 1 "
                    f"(needs {total:.1f}, has {region.inner_h:.1f}). "
                    "Reduce content or increase region height in the template."
                )

        return page, unconsumed

    def _fit_items(
        self,
        region: Region,
        items: list[tuple[Component, float, float]],
        gap: float | None = None,
    ) -> tuple[list[PlacedComponent], list[tuple[Component, float, float]]]:
        """Place items top-down until region content height is exhausted.

        Each item is ``(comp, width, height)``; the component is placed at its
        own ``width`` (not the region width) so scaled components keep aspect.
        ``gap`` overrides ``self.gap`` for this call (used by the compression
        path to tighten the overflow region).
        """
        if gap is None:
            gap = self.gap
        placed: list[PlacedComponent] = []
        remaining_height = region.content_h
        y = region.content_y

        for idx, (comp, width, height) in enumerate(items):
            consumed = height + (gap if placed else 0)
            if consumed > remaining_height and placed:
                # Can't fit this component; return rest as leftover.
                return placed, items[idx:]
            if consumed > remaining_height and not placed:
                # Even the first component doesn't fit. Place it anyway and warn later
                # (this path is blocked for repeat regions by _build_first_page).
                consumed = height
            y += gap if placed else 0
            placed.append(
                PlacedComponent(
                    component=comp,
                    region=region.name,
                    x=region.inner_x,
                    y=y,
                    w=width,
                    h=height,
                )
            )
            y += height
            remaining_height -= consumed

        return placed, []

    def _consume_region(
        self,
        region_name: str,
        unconsumed: dict[str, list[tuple[Component, float, float]]],
        page_index: int,
        gap_override: float | None = None,
    ) -> list[PlacedComponent]:
        region = self.regions[region_name]
        items = unconsumed.get(region_name, [])
        gap = gap_override if region_name == self.overflow_region else None
        placed, leftover = self._fit_items(region, items, gap)
        unconsumed[region_name] = leftover
        for p in placed:
            p.page_index = page_index
        return placed

    # ------------------------------------------------------------------
    # Page cap / compression
    # ------------------------------------------------------------------

    def _apply_page_cap(
        self,
        pages: list[Page],
        sized: dict[str, list[tuple[Component, float, float]]],
    ) -> list[Page]:
        if not self.max_pages or len(pages) <= self.max_pages:
            return pages

        # Over the hard cap -> compress the overflow region until it fits.
        compressed, met_cap = self._compress(sized, self.max_pages)

        if not met_cap:
            actual = len(compressed)
            self.warnings.append(
                f"Compressed to density floor (f={FONT_FLOOR:.2f}) but still "
                f"{actual} pages > max_pages={self.max_pages}. Returning best-effort "
                f"deck; reduce content or raise max_pages."
            )
        return compressed

    def _compress(
        self,
        sized: dict[str, list[tuple[Component, float, float]]],
        target_pages: int,
    ) -> tuple[list[Page], bool]:
        """Binary-search the density factor so the deck fits ``target_pages``.

        ``f`` drives font-size, vertical geometry, inter-component gap and
        intra-card horizontal whitespace in lockstep. Returns
        (best_effort_pages, met_cap). ``met_cap`` is True only if a factor
        inside [FONT_FLOOR, 1.0] actually reaches the target.
        """
        lo, hi = FONT_FLOOR, 1.0
        best_pages: list[Page] | None = None
        met_cap = False

        for _ in range(28):
            f = (lo + hi) / 2.0
            scaled = self._scaled_sized(sized, f)
            pages = self._build_pages_raw(scaled, gap_override=self.gap * f)
            n = len(pages)
            if n <= target_pages:
                best_pages, met_cap = pages, True
                lo = f  # try a larger (more legible) scale
            else:
                hi = f

        if not met_cap:
            # Nothing inside [FONT_FLOOR, 1.0] reaches the cap — return the
            # floor result (most compressed) so the warning matches reality.
            scaled = self._scaled_sized(sized, FONT_FLOOR)
            best_pages = self._build_pages_raw(scaled, gap_override=self.gap * FONT_FLOOR)

        return best_pages or [], met_cap

    def _scaled_sized(
        self,
        sized: dict[str, list[tuple[Component, float, float]]],
        f: float,
    ) -> dict[str, list[tuple[Component, float, float]]]:
        """Clone ``sized`` with the overflow region density-scaled by ``f``."""
        out: dict[str, list[tuple[Component, float, float]]] = {}
        for name, items in sized.items():
            if name != self.overflow_region:
                out[name] = list(items)
                continue
            scaled_items: list[tuple[Component, float, float]] = []
            for comp, _, _ in items:
                width, height, new_svg = self._fontfit_scale(comp, f)
                new_comp = Component(
                    type=comp.type,
                    svg=new_svg,
                    label=comp.label,
                    attrs=comp.attrs,
                )
                scaled_items.append((new_comp, width, height))
            out[name] = scaled_items
        return out

    # ---- scale functions -------------------------------------------------

    def _fontfit_scale(
        self, comp: Component, f: float
    ) -> tuple[float, float, str]:
        """Density-driven: shrink fonts + vertical spacing + horizontal
        whitespace, width unchanged.

        The rewritten SVG keeps its width but its viewBox height is scaled by
        ``f`` (vertical compression) and its content is affine-mapped to fill
        the card width (horizontal whitespace removal). The natural height (at
        full region width) shrinks by ``f`` and pagination tightens.
        """
        region = self.regions[self.overflow_region]
        new_svg = scale_svg_fonts(comp.svg, f)
        w0, h0 = svg_natural_size(new_svg)
        width = region.inner_w
        height = width * (h0 / w0) if w0 > 0 else 10.0
        return width, height, new_svg

    # ------------------------------------------------------------------
    # Raw pagination (reusable by compression)
    # ------------------------------------------------------------------

    def _build_pages_raw(
        self,
        sized: dict[str, list[tuple[Component, float, float]]],
        gap_override: float | None = None,
    ) -> list[Page]:
        first_page, unconsumed = self._build_first_page(sized, gap_override)
        pages: list[Page] = [first_page]

        repeat_placements = {
            r: first_page.components.get(r, []) for r in self.repeat_regions
        }

        while any(unconsumed.get(r) for r in unconsumed if r not in self.repeat_regions):
            page = Page(index=len(pages) + 1, is_continuation=True)
            for region_name in self.regions:
                if region_name in self.repeat_regions:
                    page.components[region_name] = [
                        PlacedComponent(
                            component=p.component,
                            region=p.region,
                            x=p.x,
                            y=p.y,
                            w=p.w,
                            h=p.h,
                            scale=p.scale,
                            page_index=len(pages) + 1,
                        )
                        for p in repeat_placements.get(region_name, [])
                    ]
                else:
                    page.components[region_name] = self._consume_region(
                        region_name, unconsumed, len(pages) + 1, gap_override
                    )
            pages.append(page)

        return pages
