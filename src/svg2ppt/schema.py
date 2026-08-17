"""
SVG deck input schema.

Downstream AI emits XML that wraps content-only SVG components:

    <deck project="P-TIOX" theme="loreal">
      <component type="title">         <svg ...>...</svg> </component>
      <component type="formula-ref">   <svg ...>...</svg> </component>
      <component type="meta">          <svg ...>...</svg> </component>
      <component type="info-card" label="Formulation"> <svg ...>...</svg> </component>
      <component type="efficacy-table"><svg ...>...</svg> </component>
      <component type="consumer-block"><svg ...>...</svg> </component>
      <component type="summary-block"> <svg ...>...</svg> </component>
      <component type="custom" region="middle_column"> <svg ...>...</svg> </component>
    </deck>

Rules:
- AI writes content SVG only (with viewBox / natural width+height).
- AI does not specify x, y, page numbers, or final layout.
- `type` is a routing hint; the layout engine decides region, size and pagination.
- `custom` may carry an explicit `region` attribute as an escape hatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    """Raised when the input XML violates the deck schema."""


@dataclass
class Component:
    """A single content SVG component produced by the downstream AI."""

    type: str
    svg: str  # full <svg ...>...</svg> string
    label: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        label = f" label={self.label!r}" if self.label else ""
        return f"<Component type={self.type!r}{label} svg={len(self.svg)}ch>"


@dataclass
class DeckXML:
    """Parsed deck wrapper."""

    project: str
    theme: str
    components: list[Component]
    raw_attrs: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Regexes for raw extraction (avoids ElementTree namespace rewriting).
    # ------------------------------------------------------------------
    _DECK_RE = re.compile(r"<deck\b([^>]*)>(.*?)</deck>", re.DOTALL | re.IGNORECASE)
    _COMPONENT_RE = re.compile(
        r"<component\b([^>]*)>(.*?)</component>", re.DOTALL | re.IGNORECASE
    )
    _SVG_RE = re.compile(
        r"<svg\b([^>]*)>(.*?)</svg>", re.DOTALL | re.IGNORECASE
    )
    _ATTR_RE = re.compile(r'\b(\w+)="([^"]*)"')

    @classmethod
    def from_string(cls, xml_text: str) -> "DeckXML":
        """Parse deck XML from a string."""
        deck_match = cls._DECK_RE.search(xml_text)
        if not deck_match:
            raise SchemaError("Root element <deck>...</deck> not found")

        raw_attrs = dict(cls._ATTR_RE.findall(deck_match.group(1)))
        project = raw_attrs.get("project", "").strip()
        theme = raw_attrs.get("theme", "loreal").strip()
        if not project:
            raise SchemaError("<deck> must have a non-empty 'project' attribute")

        components: list[Component] = []
        for idx, comp_match in enumerate(
            cls._COMPONENT_RE.finditer(deck_match.group(2)), start=1
        ):
            comp = cls._parse_component(comp_match.group(1), comp_match.group(2), idx)
            if comp is not None:
                components.append(comp)

        if not components:
            raise SchemaError("No <component> elements found in <deck>")

        return cls(
            project=project,
            theme=theme,
            components=components,
            raw_attrs=raw_attrs,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "DeckXML":
        """Parse deck XML from a file path."""
        path = Path(path)
        if not path.exists():
            raise SchemaError(f"Deck XML not found: {path}")
        return cls.from_string(path.read_text(encoding="utf-8"))

    @classmethod
    def _parse_component(
        cls, attr_text: str, body_text: str, index: int
    ) -> Component | None:
        attrs = dict(cls._ATTR_RE.findall(attr_text))
        comp_type = attrs.get("type", "").strip().lower()
        if not comp_type:
            raise SchemaError(f"<component> #{index} is missing the 'type' attribute")

        label = attrs.get("label", "").strip() or None
        svg_match = cls._SVG_RE.search(body_text)
        if not svg_match:
            raise SchemaError(
                f"<component type={comp_type!r}> must contain exactly one <svg>"
            )

        svg_str = f"<svg {svg_match.group(1).strip()}>{svg_match.group(2)}</svg>"
        svg_str = svg_str.strip()
        if not svg_str:
            raise SchemaError(f"<component type={comp_type!r}> contains an empty <svg>")

        return Component(
            type=comp_type,
            svg=svg_str,
            label=label,
            attrs={k: v for k, v in attrs.items() if k not in ("type", "label")},
        )

    def require_types(self, types: set[str]) -> None:
        """Validate that at least one component of each required type exists."""
        present = {c.type for c in self.components}
        missing = types - present
        if missing:
            raise SchemaError(f"Missing required component type(s): {sorted(missing)}")


# ---------------------------------------------------------------------------
# SVG geometry helpers
# ---------------------------------------------------------------------------


def svg_natural_size(svg_text: str) -> tuple[float, float]:
    """
    Return the natural (width, height) of an SVG in user units.

    Priority:
    1. viewBox="x y w h" -> (w, h)
    2. width/height attributes (numeric) -> (w, h)
    3. Fallback -> (100, 100)
    """
    vb_match = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text, re.IGNORECASE)
    if vb_match:
        parts = vb_match.group(1).strip().split()
        if len(parts) == 4:
            try:
                return float(parts[2]), float(parts[3])
            except ValueError:
                pass

    width = _parse_length(re.search(r'width\s*=\s*"([^"]+)"', svg_text, re.IGNORECASE))
    height = _parse_length(
        re.search(r'height\s*=\s*"([^"]+)"', svg_text, re.IGNORECASE)
    )

    if width is not None and height is not None:
        return width, height

    return 100.0, 100.0


def svg_view_box(svg_text: str) -> str | None:
    """Return the viewBox attribute if present, otherwise None."""
    match = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def svg_inner_content(svg_text: str) -> str:
    """
    Return the inner content of an <svg> element (everything between the outer
    opening and closing tags). This is used when inlining the component into a
    page-level SVG with a fresh viewport/clip.
    """
    svg_text = svg_text.strip()
    open_match = re.search(r"<svg\b[^>]*>", svg_text, re.IGNORECASE)
    if not open_match:
        raise SchemaError("Malformed SVG: cannot locate opening <svg> tag")

    close_match = re.search(r"</svg\s*>\s*$", svg_text, re.IGNORECASE)
    if not close_match:
        raise SchemaError("Malformed SVG: missing closing </svg>")

    return svg_text[open_match.end() : close_match.start()].strip()


def scale_svg_fonts(svg_text: str, f: float) -> str:
    """
    Return a copy of the SVG shrunk by density factor ``f`` along BOTH content
    axes:

    * Vertical (height driver): ``font-size`` + vertical geometry
      (``y/dy/y1/y2/cy/ry``) + ``height`` + viewBox height all scale by ``f``.
      The component becomes shorter, so more fit per page.
    * Horizontal (whitespace driver): content x-coordinates are affine-mapped
      to fill the viewBox width with a reduced side padding
      ``PX = max(2, original_left_pad * f)``. This removes intra-card
      horizontal whitespace (left/right insets, inter-column gaps). The card
      background rect is scaled by the same map so the frame stays consistent.
      The component still fills the full column width.

    ``f == 1.0`` is a no-op.
    """
    f = float(f)
    if f == 1.0:
        return svg_text

    # viewBox: keep width, scale height by f.
    def _vb(m: re.Match[str]) -> str:
        parts = m.group(1).split()
        if len(parts) == 4:
            try:
                w = float(parts[2])
                h = float(parts[3])
                return f'viewBox="0 0 {w:.3f} {h * f:.3f}"'
            except ValueError:
                return m.group(0)
        return m.group(0)

    s = re.sub(r'viewBox\s*=\s*"([^"]+)"', _vb, svg_text, flags=re.IGNORECASE)

    # Vertical: font-size + y-family + height.
    def _num(attr: str) -> None:
        nonlocal s
        pat = rf'(?<![\w-])({attr}\s*=\s*")([\d.]+)(")'
        s = re.sub(
            pat,
            lambda m: m.group(1) + f"{float(m.group(2)) * f:.3f}" + m.group(3),
            s,
            flags=re.IGNORECASE,
        )

    _num("height")
    s = re.sub(
        r'(?<![\w-])(font-size\s*=\s*")([\d.]+)(px|pt|)(")',
        lambda m: m.group(1) + f"{float(m.group(2)) * f:.3f}" + (m.group(3) or "") + m.group(4),
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r'(?<![\w-])(font-size\s*:\s*)([\d.]+)(px|)',
        lambda m: m.group(1) + f"{float(m.group(2)) * f:.3f}" + (m.group(3) or ""),
        s,
        flags=re.IGNORECASE,
    )
    for attr in ("y", "dy", "y1", "y2", "cy", "ry"):
        _num(attr)

    # Horizontal: affine x-map to kill intra-card whitespace.
    s = _shrink_horizontal(s, f)
    return s


def _viewbox_width(svg_text: str) -> float | None:
    """Return the viewBox width (W) of an SVG, or None if absent."""
    vb = svg_view_box(svg_text)
    if not vb:
        return None
    parts = vb.split()
    if len(parts) == 4:
        try:
            return float(parts[2])
        except ValueError:
            return None
    return None


def _shrink_horizontal(svg_text: str, f: float) -> str:
    """Affine-map content x so it fills the viewBox width with reduced padding.

    ``PX = max(2, original_left_pad * f)`` — as density ``f`` drops, the side
    inset shrinks, packing content toward the card edges. Background rects are
    scaled by the same affine map so the frame follows the content.
    """
    vb_w = _viewbox_width(svg_text)
    if vb_w is None or vb_w <= 0:
        return svg_text

    coords: list[float] = []
    for attr in ("x", "x1", "x2", "cx"):
        for m in re.finditer(rf'(?<![\w-]){attr}\s*=\s*"([\d.]+)"', svg_text, re.IGNORECASE):
            coords.append(float(m.group(1)))
    for m in re.finditer(r"<rect\b[^>]*>", svg_text, re.IGNORECASE):
        tag = m.group(0)
        xm = re.search(r'\bx\s*=\s*"([\d.]+)"', tag)
        wm = re.search(r'\bwidth\s*=\s*"([\d.]+)"', tag)
        if xm and wm:
            x = float(xm.group(1))
            w = float(wm.group(1))
            coords.extend([x, x + w])
    for m in re.finditer(r"<circle\b[^>]*>", svg_text, re.IGNORECASE):
        tag = m.group(0)
        xm = re.search(r'\bcx\s*=\s*"([\d.]+)"', tag)
        rm = re.search(r'\br\s*=\s*"([\d.]+)"', tag)
        if xm and rm:
            cx = float(xm.group(1))
            r = float(rm.group(1))
            coords.extend([cx - r, cx + r])

    if len(coords) < 2:
        return svg_text
    xmin, xmax = min(coords), max(coords)
    span = xmax - xmin
    if span <= 0:
        return svg_text

    original_pad = xmin
    PX = max(2.0, original_pad * f)
    a = (vb_w - 2 * PX) / span
    b = PX - a * xmin
    if a <= 0:
        return svg_text

    def _map(attr: str) -> str:
        pat = rf'(?<![\w-])({attr}\s*=\s*")([\d.]+)(")'
        return re.sub(
            pat,
            lambda m: m.group(1) + f"{a * float(m.group(2)) + b:.3f}" + m.group(3),
            svg_text,
            flags=re.IGNORECASE,
        )

    out = _map("x")
    out = _map("x1")
    out = _map("x2")
    out = _map("cx")
    # Horizontal radii / circle radius scale by a to keep shapes consistent.
    out = re.sub(
        r'(?<![\w-])(rx\s*=\s*")([\d.]+)(")',
        lambda m: m.group(1) + f"{float(m.group(2)) * a:.3f}" + m.group(3),
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r'(?<![\w-])(r\s*=\s*")([\d.]+)(")',
        lambda m: m.group(1) + f"{float(m.group(2)) * a:.3f}" + m.group(3),
        out,
        flags=re.IGNORECASE,
    )
    # rect width scales by a so the card frame shrinks with its content.
    out = re.sub(
        r'(<rect\b[^>]*?\bwidth\s*=\s*")([\d.]+)(")',
        lambda m: m.group(1) + f"{float(m.group(2)) * a:.3f}" + m.group(3),
        out,
        flags=re.IGNORECASE,
    )
    return out


def _parse_length(match: re.Match[str] | None) -> float | None:
    if not match:
        return None
    text = match.group(1).strip().lower()
    for unit in ("px", "pt", "em", "rem", "ex", "cm", "mm", "in", "%"):
        if text.endswith(unit):
            text = text[: -len(unit)].strip()
            break
    try:
        return float(text)
    except ValueError:
        return None
