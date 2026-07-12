# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create deterministic companion icon derivatives from approved project icons."""
from __future__ import annotations
from pathlib import Path
import shutil
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloth_next" / "assets" / "icons"
TARGET = ROOT / "companion" / "assets"
ICO_SIZES = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256))

def build() -> None:
    app_source, bake_source = SOURCE / "cloth_next.png", SOURCE / "bake.png"
    if not app_source.is_file() or not bake_source.is_file():
        raise FileNotFoundError("run tools/build_icons.py before companion asset build")
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(app_source, TARGET / "cloth_next.png")
    with Image.open(bake_source) as bake:
        alpha=bake.convert("RGBA").getchannel("A")
        tinted=Image.new("RGBA",bake.size,(217,154,50,0)); tinted.putalpha(alpha)
        tinted.save(TARGET/"bake.png",format="PNG",optimize=False,compress_level=9)
    with Image.open(app_source) as image:
        master = image.convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
        master.save(TARGET / "cloth_next.ico", format="ICO", sizes=ICO_SIZES,
                    bitmap_format="png")
    validate()

def validate() -> None:
    for name in ("cloth_next.png", "bake.png", "cloth_next.ico"):
        path = TARGET / name
        if not path.is_file():
            raise FileNotFoundError(f"missing companion icon asset: {path}")
        with Image.open(path) as image:
            if name == "cloth_next.ico" and set(image.info.get("sizes", ())) != set(ICO_SIZES):
                raise ValueError("companion ICO does not contain every required size")
            image.verify()

if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError) as exc:
        print(f"Companion asset build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Companion identity and bake icon assets: valid")
