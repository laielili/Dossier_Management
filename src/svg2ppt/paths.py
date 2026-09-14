"""
Runtime root resolution for the svg2ppt package.

Kept separate from ``src.config`` on purpose: svg2ppt is also imported as a
standalone top-level package by the offline demo, so it must never depend on
the rest of the project. Both helpers mirror ``src.config`` exactly.

    source checkout   both roots are the repository root
    frozen            ASSET_ROOT  <bundle>/_internal, i.e. sys._MEIPASS
                                  (static/, the layout templates, badge PNGs)
                      APP_ROOT    the folder holding the .exe
                                  (output/, generated artifacts)
"""

from __future__ import annotations

import sys
from pathlib import Path


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def asset_root() -> Path:
    """Read-only asset root: the repo root, or sys._MEIPASS when frozen."""
    if _frozen():
        return Path(
            getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
        )
    return Path(__file__).resolve().parent.parent.parent


def writable_root() -> Path:
    """Writable root: the folder containing the executable when frozen."""
    if _frozen():
        return Path(sys.executable).resolve().parent
    return asset_root()
