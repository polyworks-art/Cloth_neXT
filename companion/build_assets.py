# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create deterministic companion icon derivatives from approved project icons."""
from __future__ import annotations
from pathlib import Path
import shutil
import sys
import math
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloth_next" / "assets" / "icons"
TARGET = ROOT / "companion" / "assets"
ICO_SIZES = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256))
FOG_SIZE=(76,72); FOG_FRAME_COUNT=32
FOG_ASSETS=tuple(f"mist_frame_{index:02d}.png" for index in range(FOG_FRAME_COUNT))
MIST_ASSETS={name:FOG_SIZE for name in FOG_ASSETS} | {"mist_fallback.png":FOG_SIZE}

def _fog_frame(index: int) -> Image.Image:
    """Seamless, deterministic amber/charcoal turbulence with no alpha margin."""
    width,height=FOG_SIZE; scale=2; phase=math.tau*index/FOG_FRAME_COUNT
    image=Image.new("RGB",(width*scale,height*scale)); pixels=image.load()
    dark=(24,23,22); amber=(190,118,31); bright=(217,154,50)
    for py in range(height*scale):
        y=py/(height*scale-1)
        for px in range(width*scale):
            x=px/(width*scale-1)
            n=(.34*math.sin(math.tau*(1.15*x+.72*y)+phase)
               +.20*math.sin(math.tau*(2.05*x-1.28*y)-phase)
               +.12*math.sin(math.tau*(3.1*x+2.3*y)+phase*2))
            boundary=.55-x+.20*math.sin(math.tau*y+phase)+n*.30
            mix=max(0.,min(1.,.5+boundary*1.15))
            detail=.88+.12*math.sin(math.tau*(1.7*x+1.35*y)-phase)
            base=tuple(int(dark[c]*(1-mix)+amber[c]*mix) for c in range(3))
            glow=max(0.,mix-.55)*.55
            pixels[px,py]=tuple(max(0,min(255,int((base[c]*(1-glow)+bright[c]*glow)*detail))) for c in range(3))
    return image.resize(FOG_SIZE,Image.Resampling.LANCZOS)

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
    for index,name in enumerate(FOG_ASSETS):
        _fog_frame(index).save(TARGET/name,format="PNG",optimize=False,compress_level=9)
    _fog_frame(2).save(TARGET/"mist_fallback.png",format="PNG",optimize=False,compress_level=9)
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
                if image.mode!="RGB" or image.size!=MIST_ASSETS[name]: raise ValueError(f"invalid mist asset: {name}")
                if path.stat().st_size>64*1024: raise ValueError(f"oversized mist asset: {name}")

if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError) as exc:
        print(f"Companion asset build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Companion identity and bake icon assets: valid")
