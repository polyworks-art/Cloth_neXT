# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the development-only Tk companion executable."""
from __future__ import annotations
from pathlib import Path
import sys
import PyInstaller.__main__

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.build_assets import build as build_assets
def main():
    build_assets()
    assets=ROOT/"companion/assets"
    PyInstaller.__main__.run([str(ROOT/"companion/app.py"),"--name=Cloth NeXt Bake",
        "--onefile","--windowed","--noconfirm",
        f"--icon={assets/'cloth_next.ico'}",
        f"--add-data={assets/'cloth_next.png'};companion_assets",
        f"--add-data={assets/'bake.png'};companion_assets",
        *[f"--add-data={assets/name};companion_assets" for name in ("mist_small.png","mist_medium.png","mist_large.png","mist_core.png","mist_glow.png","mist_fallback.png")],
        f"--distpath={ROOT/'companion/dist'}",f"--workpath={ROOT/'companion/build/app_icon'}",
        f"--specpath={ROOT/'companion'}",f"--paths={ROOT}"])
    output=ROOT/"companion/dist/Cloth NeXt Bake.exe"
    if not output.is_file(): raise RuntimeError("companion EXE was not produced")
    print(f"Development companion: {output}")
if __name__=="__main__": main()
