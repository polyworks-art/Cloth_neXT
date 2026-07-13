# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create deterministic companion icon derivatives from approved project icons."""
from __future__ import annotations
from pathlib import Path
import shutil
import sys
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloth_next" / "assets" / "icons"
TARGET = ROOT / "companion" / "assets"
ICO_SIZES = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256))
MIST_ASSETS={"mist_small.png":28,"mist_medium.png":38,"mist_large.png":48,
             "mist_core.png":34,"mist_glow.png":58,"mist_fallback.png":76}

def _mist(size: int, *, amber: bool, seed: int) -> Image.Image:
    import random
    rng=random.Random(seed); scale=4
    layer=Image.new("RGBA",(size*scale,size*scale),(0,0,0,0)); draw=ImageDraw.Draw(layer)
    color=(217,154,50) if amber else (244,239,225)
    for _ in range(9):
        radius=rng.uniform(.18,.38)*size*scale
        x=size*scale/2+rng.uniform(-.17,.17)*size*scale
        y=size*scale/2+rng.uniform(-.14,.14)*size*scale
        draw.ellipse((x-radius,y-radius*.72,x+radius,y+radius*.72),fill=(*color,rng.randint(25,58)))
    layer=layer.filter(ImageFilter.GaussianBlur(size*scale*.09))
    result=layer.resize((size,size),Image.Resampling.LANCZOS)
    for point in ((0,0),(size-1,0),(0,size-1),(size-1,size-1)): result.putpixel(point,(0,0,0,0))
    return result

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
    for index,(name,size) in enumerate(MIST_ASSETS.items()):
        image=_mist(size,amber=name in {"mist_core.png","mist_small.png","mist_fallback.png"},seed=9100+index)
        if name=="mist_fallback.png":
            neutral=_mist(size,amber=False,seed=9200); image=Image.alpha_composite(neutral,image)
        image.save(TARGET/name,format="PNG",optimize=False,compress_level=9)
    validate()

def validate() -> None:
    for name in ("cloth_next.png", "bake.png", "cloth_next.ico", *MIST_ASSETS):
        path = TARGET / name
        if not path.is_file():
            raise FileNotFoundError(f"missing companion icon asset: {path}")
        with Image.open(path) as image:
            if name == "cloth_next.ico" and set(image.info.get("sizes", ())) != set(ICO_SIZES):
                raise ValueError("companion ICO does not contain every required size")
            image.verify()
        if name in MIST_ASSETS:
            with Image.open(path) as image:
                if image.mode!="RGBA" or image.size!=(MIST_ASSETS[name],)*2: raise ValueError(f"invalid mist asset: {name}")
                alpha=image.getchannel("A")
                if alpha.getbbox() is None or any(alpha.getpixel(p) for p in ((0,0),(image.width-1,0),(0,image.height-1),(image.width-1,image.height-1))):
                    raise ValueError(f"invalid mist alpha: {name}")

if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError) as exc:
        print(f"Companion asset build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Companion identity and bake icon assets: valid")
