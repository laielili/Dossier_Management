"""
Synthetic end-to-end demo for svg2ppt.

Generates an XML deck with content-only SVG components, runs the layout engine,
and writes synthesis_deck.pptx + preview.html into output/synthetic_deck_demo/.

Usage from repo root:

    venv\\Scripts\\python.exe demo\\build_synthetic_deck.py

Or absolute:

    A:\\Projects\\Python Projects\\Dossier_Management\\venv\\Scripts\\python.exe \\
        A:\\Projects\\Python Projects\\Dossier_Management\\demo\\build_synthetic_deck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is importable when running the demo directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from svg2ppt import DeckBuilder


def svg_box(width: int, height: int, inner: str, view_box: str | None = None) -> str:
    """Wrap inner SVG content in a well-formed SVG element."""
    vb = f' viewBox="{view_box}"' if view_box else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"{vb}>'
        f"{inner}</svg>"
    )


def title_component() -> str:
    inner = (
        '<text x="0" y="30" font-size="28" font-weight="bold" fill="#333">P-TIOX</text>'
    )
    return svg_box(800, 40, inner, "0 0 800 40")


def formula_ref_component() -> str:
    inner = (
        '<text x="0" y="22" font-size="16" fill="#555">Target: 774715 21</text>'
    )
    return svg_box(300, 30, inner, "0 0 300 30")


def meta_component() -> str:
    inner = (
        '<text x="0" y="18" font-size="14" fill="#333">Launch - DEV  |  '
        'Female 25-55 y.o.  |  Inspired by BOTOX, treats areas Botox cannot reach</text>'
    )
    return svg_box(960, 28, inner, "0 0 960 28")


def info_card_component(label: str, text: str) -> str:
    inner = (
        f'<text x="0" y="16" font-size="12" font-weight="bold" fill="#c8860d">{label}</text>'
        f'<text x="0" y="34" font-size="11" fill="#333">{text}</text>'
    )
    return svg_box(200, 46, inner, "0 0 200 46")


def efficacy_table_component(rows: int = 60, title: str = "China T12W Clinical Test") -> str:
    """Tall table to force middle_column overflow and continuation pages."""
    row_h = 14
    h = 30 + rows * row_h
    lines: list[str] = [
        f'<text x="0" y="20" font-size="14" font-weight="bold" fill="#333">{title}</text>',
    ]
    y = 40
    for i in range(rows):
        color = "#2e7d32" if i % 3 == 0 else "#333"
        val = f"{-45 - i % 20}.00%"
        lines.append(
            f'<text x="0" y="{y}" font-size="11" fill="{color}">Metric {i+1}: {val}</text>'
        )
        y += row_h
    return svg_box(540, h, "".join(lines), f"0 0 540 {h}")


def consumer_block_component() -> str:
    inner = (
        '<text x="0" y="16" font-size="12" font-weight="bold" fill="#c8860d">Consumer Perception</text>'
        '<text x="0" y="36" font-size="11" fill="#2e7d32">Positive: fine lines reduced, good usage experience</text>'
        '<text x="0" y="54" font-size="11" fill="#c62828">Negative: slight stickiness on first application</text>'
    )
    return svg_box(540, 70, inner, "0 0 540 70")


def summary_component() -> str:
    inner = (
        '<text x="0" y="14" font-size="11" fill="#333">P-TIOX demonstrates significant</text>'
        '<text x="0" y="30" font-size="11" fill="#333">wrinkle reduction across multiple</text>'
        '<text x="0" y="46" font-size="11" fill="#333">instrumental metrics, with strong</text>'
        '<text x="0" y="62" font-size="11" fill="#333">consumer acceptance and a well-</text>'
        '<text x="0" y="78" font-size="11" fill="#333">tolerated formulation profile.</text>'
    )
    return svg_box(200, 100, inner, "0 0 200 100")


def custom_component() -> str:
    inner = '<text x="0" y="20" font-size="12" fill="#555">Custom note (routed to middle)</text>'
    return svg_box(540, 30, inner, "0 0 540 30")


def build_xml() -> str:
    components = [
        ('  <component type="title">\n    ', title_component(), '\n  </component>'),
        ('  <component type="meta">\n    ', meta_component(), '\n  </component>'),
        ('  <component type="info-card" label="Formulation">\n    ',
         info_card_component("Formulation", "2% SYN-AKE, milky lotion"),
         '\n  </component>'),
        ('  <component type="info-card" label="Packaging">\n    ',
         info_card_component("Packaging", "Glass dropper bottle 30ml"),
         '\n  </component>'),
        ('  <component type="info-card" label="Fragrance">\n    ',
         info_card_component("Fragrance", "Light floral, no alcohol"),
         '\n  </component>'),
        ('  <component type="info-card" label="Safety">\n    ',
         info_card_component("Safety", "Dermatologically tested"),
         '\n  </component>'),
        ('  <component type="info-card" label="Sustainability">\n    ',
         info_card_component("Sustainability", "Refillable glass dropper"),
         '\n  </component>'),
        ('  <component type="efficacy-table">\n    ', efficacy_table_component(rows=20, title="China T12W Clinical Test"), '\n  </component>'),
        ('  <component type="consumer-block">\n    ', consumer_block_component(), '\n  </component>'),
        ('  <component type="summary-block">\n    ', summary_component(), '\n  </component>'),
        ('  <component type="custom" region="middle_column">\n    ', custom_component(), '\n  </component>'),
        ('  <component type="efficacy-table">\n    ', efficacy_table_component(rows=20, title="US T8W Clinical Test"), '\n  </component>'),
        ('  <component type="efficacy-table">\n    ', efficacy_table_component(rows=20, title="FE Sensory Test"), '\n  </component>'),
    ]

    body = "\n".join(f"{a}{b}{c}" for a, b, c in components)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<deck project="P-TIOX" theme="loreal">\n{body}\n</deck>'


def main() -> int:
    output_dir = ROOT / "output" / "synthetic_deck_demo"
    output_dir.mkdir(parents=True, exist_ok=True)

    xml_path = output_dir / "demo_deck.xml"
    xml_path.write_text(build_xml(), encoding="utf-8")
    print(f"[demo] wrote XML: {xml_path}")

    builder = DeckBuilder(dpi=150)
    result = builder.build_from_file(xml_path, output_dir)

    print(f"[demo] rendered {result.page_count} pages")
    print(f"[demo] PPTX:  {result.pptx_path}")
    if result.preview_path:
        print(f"[demo] HTML preview: {result.preview_path}")
    else:
        print("[demo] inline preview (no HTML file generated)")
    for png in result.page_pngs:
        print(f"[demo] page PNG: {png}")
    if result.warnings:
        print("[demo] warnings:")
        for w in result.warnings:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
