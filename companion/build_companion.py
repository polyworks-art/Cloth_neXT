# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the development-only Tk companion executable."""
from __future__ import annotations
from pathlib import Path
import PyInstaller.__main__

ROOT=Path(__file__).resolve().parents[1]
def main():
    PyInstaller.__main__.run([str(ROOT/"companion/app.py"),"--name=Cloth NeXt Bake",
        "--onefile","--windowed","--clean","--noconfirm",
        f"--distpath={ROOT/'companion/dist'}",f"--workpath={ROOT/'companion/build'}",
        f"--specpath={ROOT/'companion'}",f"--paths={ROOT}"])
    output=ROOT/"companion/dist/Cloth NeXt Bake.exe"
    if not output.is_file(): raise RuntimeError("companion EXE was not produced")
    print(f"Development companion: {output}")
if __name__=="__main__": main()
