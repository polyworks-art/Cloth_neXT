# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create deterministic companion icon derivatives from approved project icons."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
import shutil
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloth_next" / "assets" / "icons"
STATUS_SOURCE = ROOT / "assets" / "solver_status_icons"
TARGET = ROOT / "companion" / "assets"
ICO_SIZES = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256))
PARTICLE_SOURCES={"bake":(12,-18),"cloth":(16,12),"collider":(12,-11),
                  "collision":(16,20),"pinning":(12,-24),"solver":(16,15),
                  "quality":(12,-14),"timer":(12,23)}
PARTICLE_ASSETS={f"particle_{name}_{size}.png":(size,size)
                 for name,(size,_angle) in PARTICLE_SOURCES.items()}
STATUS_ASSETS={
    "status_contacts_14.png":("contacts.svg",(74,190,255)),
    "status_newton_14.png":("newton.svg",(242,184,75)),
    "status_iterations_14.png":("linear_iterations.svg",(181,126,255)),
}
STATUS_SIZE=(14,14)

def _build_status_icon(source: Path, color: tuple[int,int,int]) -> Image.Image:
    try:
        import resvg_py
    except ImportError as exc:
        raise RuntimeError("resvg-py is required for companion icon builds") from exc
    rendered=resvg_py.svg_to_bytes(
        svg_path=str(source),width=256,height=256,skip_system_fonts=True)
    with Image.open(BytesIO(rendered)) as image:
        alpha=image.convert("RGBA").getchannel("A")
    bounds=alpha.getbbox()
    if bounds is None:
        raise ValueError(f"empty solver status icon: {source}")
    alpha=alpha.crop(bounds)
    available=(STATUS_SIZE[0]-2,STATUS_SIZE[1]-2)
    scale=min(available[0]/alpha.width,available[1]/alpha.height)
    fitted=alpha.resize(
        (max(1,round(alpha.width*scale)),max(1,round(alpha.height*scale))),
        Image.Resampling.LANCZOS)
    canvas=Image.new("L",STATUS_SIZE,0)
    canvas.paste(
        fitted,((STATUS_SIZE[0]-fitted.width)//2,
                (STATUS_SIZE[1]-fitted.height)//2))
    icon=Image.new("RGBA",STATUS_SIZE,(*color,0))
    icon.putalpha(canvas)
    return icon

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
    for name,(size,angle) in PARTICLE_SOURCES.items():
        with Image.open(SOURCE/f"{name}.png") as source:
            inset=max(2,size//6)
            content=source.convert("RGBA").resize(
                (size-inset*2,size-inset*2),Image.Resampling.LANCZOS)
            icon=Image.new("RGBA",(size,size),(255,255,255,0))
            icon.alpha_composite(content,(inset,inset))
            icon=icon.rotate(angle,resample=Image.Resampling.BICUBIC,
                             expand=False)
            alpha=icon.getchannel("A").point(lambda value:int(value*.72))
            icon.putalpha(alpha)
            icon.save(TARGET/f"particle_{name}_{size}.png",format="PNG",
                      optimize=False,compress_level=9)
    for target_name,(source_name,color) in STATUS_ASSETS.items():
        source=STATUS_SOURCE/source_name
        if not source.is_file():
            raise FileNotFoundError(f"missing solver status icon source: {source}")
        _build_status_icon(source,color).save(
            TARGET/target_name,format="PNG",optimize=False,compress_level=9)
    for stale in TARGET.glob("mist_*.png"):
        stale.unlink()
    validate()

def validate() -> None:
    for name in ("cloth_next.png", "bake.png", "cloth_next.ico",
                 *PARTICLE_ASSETS,*STATUS_ASSETS):
        path = TARGET / name
        if not path.is_file():
            raise FileNotFoundError(f"missing companion icon asset: {path}")
        with Image.open(path) as image:
            if name == "cloth_next.ico" and set(image.info.get("sizes", ())) != set(ICO_SIZES):
                raise ValueError("companion ICO does not contain every required size")
            image.verify()
        if name in PARTICLE_ASSETS:
            with Image.open(path) as image:
                if image.mode!="RGBA" or image.size!=PARTICLE_ASSETS[name]: raise ValueError(f"invalid particle asset: {name}")
                if path.stat().st_size>16*1024: raise ValueError(f"oversized particle asset: {name}")
        if name in STATUS_ASSETS:
            with Image.open(path) as image:
                rgba=image.convert("RGBA")
                visible=[pixel for pixel in rgba.get_flattened_data()
                         if pixel[3]]
                expected=STATUS_ASSETS[name][1]
                if image.mode!="RGBA" or image.size!=STATUS_SIZE:
                    raise ValueError(f"invalid solver status asset: {name}")
                if not visible or any(pixel[:3]!=expected for pixel in visible):
                    raise ValueError(f"invalid solver status color: {name}")
                if path.stat().st_size>8*1024:
                    raise ValueError(f"oversized solver status asset: {name}")

if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError) as exc:
        print(f"Companion asset build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Companion identity and bake icon assets: valid")
