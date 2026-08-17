"""Bug-hunting battery for svg2ppt engine + routes (read-only, writes only to output/bugs/)."""
import sys, json, io, traceback
sys.path.insert(0, "src")

from svg2ppt import DeckBuilder, DeckXML, SchemaError
from svg2ppt.schema import svg_natural_size
from svg2ppt.layout import LayoutError
from svg2ppt.render import RenderError
from pathlib import Path

OUT = Path("output/bugs"); OUT.mkdir(parents=True, exist_ok=True)

def comp(t, body, **attrs):
    a = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return f'<component type="{t}" {a}>{body}</component>'

def svg(w=200, h=60, txt="hi"):
    return f'<svg viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="#fff" stroke="#2e7d32"/><text x="10" y="30" font-size="14" fill="#333">{txt}</text></svg>'

def deck(comps, project="PROJ", theme="loreal"):
    return f'<?xml version="1.0"?>\n<deck project="{project}" theme="{theme}">\n' + "\n".join(comps) + "\n</deck>"

results = []
def check(name, fn):
    try:
        fn()
        results.append((name, "PASS", ""))
        print(f"[PASS] {name}")
    except Exception as e:
        results.append((name, "FAIL", repr(e)))
        print(f"[FAIL] {name}: {e!r}")

def build_ok(xml, **kw):
    b = DeckBuilder(dpi=120)
    r = b.build_from_string(xml, OUT, filename="t.pptx", **kw)
    assert r.pptx_path.exists() and r.pptx_path.read_bytes()[:2] == b"PK"
    return r

# 1. Happy single page
def t_happy():
    build_ok(deck([comp("title", svg(800,40,"TITLE")),
                   comp("meta", svg(900,24,"meta")),
                   comp("info-card", svg(200,120,"card"), label="Formulation"),
                   comp("efficacy-table", svg(540,200,"tbl")),
                   comp("summary-block", svg(200,120,"sum"))]))
check("1 happy single-page build", t_happy)

# 2. Multi-component continuation (middle overflow)
def t_continue():
    comps = [comp("title", svg(800,40,"T"))] + [comp("efficacy-table", svg(540,180,f"t{i}")) for i in range(6)]
    r = build_ok(deck(comps))
    assert r.page_count >= 2, f"expected continuation, got {r.page_count}"
check("2 multi-component continuation pages", t_continue)

# 3. Empty deck (no components)
def t_empty():
    try:
        DeckXML.from_string('<deck project="X"></deck>')
        raise AssertionError("should have raised SchemaError")
    except SchemaError:
        pass
check("3 empty deck rejected (SchemaError)", t_empty)

# 4. Missing project
def t_noproj():
    try:
        DeckXML.from_string('<deck><component type="title"><svg/></component></deck>')
        raise AssertionError("should raise")
    except SchemaError:
        pass
check("4 missing project rejected", t_noproj)

# 5. Component missing type
def t_notype():
    try:
        DeckXML.from_string('<deck project="X"><component><svg/></component></deck>')
        raise AssertionError("should raise")
    except SchemaError:
        pass
check("5 component missing type rejected", t_notype)

# 6. Component missing svg
def t_nosvg():
    try:
        DeckXML.from_string('<deck project="X"><component type="title">just text</component></deck>')
        raise AssertionError("should raise")
    except SchemaError:
        pass
check("6 component missing <svg> rejected", t_nosvg)

# 7. Unknown component type (no region) -> LayoutError
def t_unknown():
    try:
        b = DeckBuilder(dpi=120)
        b.build_from_string(deck([comp("frobnicate", svg(200,60,"x"))]), OUT)
        raise AssertionError("should raise LayoutError")
    except LayoutError:
        pass
check("7 unknown type -> LayoutError", t_unknown)

# 8. Repeat region overflow -> hard error (LayoutError)
def t_repeat_overflow():
    # many meta items into meta_row (h=40) -> overflow
    comps = [comp("meta", svg(900,30,f"m{i}")) for i in range(10)]
    try:
        b = DeckBuilder(dpi=120)
        b.build_from_string(deck(comps), OUT)
        raise AssertionError("should raise LayoutError (repeat overflow)")
    except LayoutError:
        pass
check("8 repeat-region overflow -> LayoutError", t_repeat_overflow)

# 9. custom region escape hatch
def t_custom():
    r = build_ok(deck([comp("title", svg(800,40,"T")),
                       comp("custom", svg(200,200,"C"), region="left_column")]))
    assert r.page_count >= 1
check("9 custom region escape hatch", t_custom)

# 10. scale_to_fit with max_pages
def t_scale():
    comps = [comp("title", svg(800,40,"T"))] + [comp("efficacy-table", svg(540,180,f"t{i}")) for i in range(8)]
    r = build_ok(deck(comps), max_pages=2, scale_to_fit=True)
    print(f"    scale_to_fit pages={r.page_count} warnings={r.warnings}")
check("10 scale_to_fit mode", t_scale)

# 11. soft cap warning (add-pages)
def t_softcap():
    comps = [comp("title", svg(800,40,"T"))] + [comp("efficacy-table", svg(540,180,f"t{i}")) for i in range(8)]
    r = build_ok(deck(comps), max_pages=2, scale_to_fit=False)
    assert any("Soft page cap" in w for w in r.warnings), "expected soft-cap warning"
check("11 soft page cap warning", t_softcap)

# 12. no viewBox + no width/height -> fallback 100x100 (no crash)
def t_fallback_size():
    c = '<component type="title"><svg><text x="0" y="20">no dims</text></svg></component>'
    w, h = svg_natural_size('<svg><text>x</text></svg>')
    assert (w, h) == (100.0, 100.0), (w, h)
    build_ok(deck([c]))
check("12 svg without dims -> fallback 100x100", t_fallback_size)

# 13. nested <svg> inside component svg -> truncation bug?
def t_nested_svg():
    inner = '<svg viewBox="0 0 50 50"><circle cx="25" cy="25" r="20"/></svg>'
    body = f'<svg viewBox="0 0 200 60"> <rect width="200" height="60" fill="#fff"/> {inner} </svg>'
    c = comp("efficacy-table", body)
    # Should NOT crash; flag if parsed length is shorter than expected
    d = DeckXML.from_string(deck([c]))
    got = d.components[0].svg
    print(f"    nested-svg parsed svg contains inner circle: {'circle' in got}")
    build_ok(deck([c]))
check("13 nested <svg> does not crash (note truncation)", t_nested_svg)

# 14. huge viewBox (10000x10000) -> clipped, no crash
def t_huge():
    build_ok(deck([comp("efficacy-table", svg(10000,10000,"BIG"))]))
check("14 huge viewBox no crash (clipped)", t_huge)

# 15. unicode / CJK text
def t_unicode():
    build_ok(deck([comp("title", svg(800,40,"标题测试 🧪")),
                   comp("info-card", svg(200,120,"配方信息"), label="配方")]))
check("15 unicode/CJK + emoji", t_unicode)

# 16. filename sanitization path: build then verify pptx name safe
def t_filename():
    r = build_ok(deck([comp("title", svg(800,40,"T"))]), )
    name = r.pptx_path.name
    assert name.endswith(".pptx") and "/" not in name and "\\" not in name
check("16 filename safe", t_filename)

# 17. multiple components same type
def t_dup():
    build_ok(deck([comp("info-card", svg(200,80,"a"), label="A"),
                   comp("info-card", svg(200,80,"b"), label="B"),
                   comp("summary-block", svg(200,80,"s"))]))
check("17 multiple same-type components", t_dup)

# ---- Route-level: path traversal via run_id ----
def t_traversal():
    # Replicate api.safe logic to demonstrate traversal with crafted run_id.
    import re
    OUTPUT_ROOT = Path("output/svg2ppt_builds")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    # create a sentinel file inside repo root to read
    sentinel = Path("REQ_TRAVERSAL_SENTINEL.txt")
    sentinel.write_text("LEAKED", encoding="utf-8")
    for rid in ("..", "../.."):
        base = (OUTPUT_ROOT / rid).resolve()
        if not base.exists() or not base.is_dir():
            continue
        target = (base / "REQ_TRAVERSAL_SENTINEL.txt").resolve()
        trapped = False
        if target != base and target.parent != base:
            if not str(target).startswith(str(base) + "\\") and not str(target).startswith(str(base) + "/"):
                trapped = True
        if not trapped:
            leaked = target.exists() and target.is_file()
            print(f"    run_id={rid!r} -> base={base} target={target} leaked={leaked}")
            if leaked:
                print(f"    [TRAVERSAL BUG] read content={target.read_text()!r}")
    sentinel.unlink()

check("17b path-traversal via run_id", t_traversal)

print("\n==== SUMMARY ====")
fails = [r for r in results if r[1] != "PASS"]
for n, s, e in results:
    print(f"  {s:4} {n}" + (f"  -> {e}" if s != "PASS" else ""))
print(f"TOTAL {len(results)}  PASS {len(results)-len(fails)}  FAIL {len(fails)}")
