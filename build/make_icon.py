"""Render app-icon.svg -> build/app-icon.ico for the PyInstaller bundle.

    venv/Scripts/python.exe build/make_icon.py

Why a browser and not PyMuPDF: the icon uses <linearGradient> fills
(url(#bg), url(#metal)). PyMuPDF's SVG importer does not resolve those
references -- it paints them solid black, which is exactly what the app
icon must not look like. A headless Chromium renders the real thing.

The .ico is a checked-in build artifact rather than being produced during
the PyInstaller run: that keeps `PyInstaller build/dossier.spec` hermetic
(no browser required on the build machine) and makes the icon easy to
inspect. Re-run this script after editing app-icon.svg.

Requires Microsoft Edge or Google Chrome plus Pillow -- both already
present in the project venv / on a stock Windows box.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SVG = REPO / "app-icon.svg"
OUT = REPO / "build" / "app-icon.ico"

CANVAS = 512
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

# Render through a wrapper page rather than opening the .svg directly: the
# page pins the canvas to CANVAS x CANVAS and forces a transparent backdrop,
# so the screenshot geometry does not depend on how the browser chooses to
# fit a bare SVG document into the viewport.
_FRAME = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  html,body{{margin:0;padding:0;width:{n}px;height:{n}px;background:transparent;overflow:hidden}}
  img{{display:block;width:{n}px;height:{n}px}}
</style></head><body><img src="{svg}" alt=""></body></html>
"""


def find_browser() -> str:
    for path in _BROWSER_CANDIDATES:
        if Path(path).exists():
            return path
    for exe in ("msedge", "chrome"):
        found = shutil.which(exe)
        if found:
            return found
    raise SystemExit(
        "No Edge/Chrome found. Install one, or rasterize app-icon.svg another "
        "way and save it as build/app-icon.ico."
    )


def render_svg(browser: str, svg: Path, canvas: int) -> bytes:
    """Screenshot the SVG at canvas x canvas with a transparent background."""
    with tempfile.TemporaryDirectory(prefix="dossier_icon_") as tmp:
        tmp_path = Path(tmp)
        # The wrapper and the SVG must share a directory to be same-origin.
        shutil.copy2(svg, tmp_path / "app-icon.svg")
        (tmp_path / "frame.html").write_text(
            _FRAME.format(n=canvas, svg="app-icon.svg"), encoding="utf-8"
        )
        png = tmp_path / "icon.png"
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--force-device-scale-factor=1",
            # A private profile keeps the run from touching, or colliding with,
            # the user's real browser session.
            f"--user-data-dir={tmp_path / 'profile'}",
            "--default-background-color=00000000",
            f"--window-size={canvas},{canvas}",
            f"--screenshot={png}",
            (tmp_path / "frame.html").as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if not png.exists():
            raise SystemExit(
                "Browser produced no screenshot.\n"
                f"  exit={proc.returncode}\n"
                f"  stderr={proc.stderr.decode('utf-8', 'replace')[-800:]}"
            )
        return png.read_bytes()


def main() -> int:
    if not SVG.exists():
        raise SystemExit(f"Source icon missing: {SVG}")

    from PIL import Image

    browser = find_browser()
    print(f"browser : {browser}")
    print(f"source  : {SVG}")

    png_bytes = render_svg(browser, SVG, CANVAS)
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    print(f"render  : {img.size[0]}x{img.size[1]} {img.mode}")

    # A fully opaque square means the artwork's transparent corners were lost,
    # which silently produces an ugly icon -- fail loudly instead.
    if img.getpixel((0, 0))[3] != 0:
        raise SystemExit(
            "Render is not transparent at the corner -- background calibration "
            "failed; the icon would ship with a filled square."
        )

    buf = io.BytesIO()
    img.save(buf, format="ICO", sizes=ICO_SIZES)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(buf.getvalue())

    size_kb = OUT.stat().st_size / 1024
    print(f"icon    : {OUT}  ({size_kb:.1f} KB, {len(ICO_SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
