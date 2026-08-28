# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create deterministic companion icon derivatives from approved project icons."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cloth_next" / "assets" / "icons"
IDENTITY_SOURCE = ROOT / "assets" / "Logo_CN.png"
STATUS_SOURCE = ROOT / "assets" / "solver_status_icons"
TARGET = ROOT / "companion" / "assets"
ICO_SIZES = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256))
PARTICLE_SOURCES={"bake":(12,-18),"cloth":(16,12),"collider":(12,-11),
                  "collision":(16,20),"pinning":(12,-24),"solver":(16,15),
                  "quality":(12,-14),"timer":(12,23)}
PARTICLE_ASSETS={f"particle_{name}_{size}.png":(size,size)
                 for name,(size,_angle) in PARTICLE_SOURCES.items()}
PARTICLE_SUBPIXEL_PHASES=4
PARTICLE_VISUAL_SCALE=1.05
STATUS_ASSETS={
    "status_contacts_16.png":"contacts.svg",
    "status_newton_16.png":"newton.svg",
    "status_iterations_16.png":"linear_iterations.svg",
}
STATUS_SIZE=(16,16)
APP_ICON_SIZE=(256,256)


def _subpixel_name(name: str, phase_x: int, phase_y: int) -> str:
    path=Path(name)
    return f"{path.stem}.subpixel-{phase_x}-{phase_y}{path.suffix}"


PARTICLE_SUBPIXEL_ASSETS={
    _subpixel_name(name,phase_x,phase_y):size
    for name,size in PARTICLE_ASSETS.items()
    for phase_y in range(PARTICLE_SUBPIXEL_PHASES)
    for phase_x in range(PARTICLE_SUBPIXEL_PHASES)}


def _subpixel_variant(icon: Image.Image, phase_x: int,
                      phase_y: int) -> Image.Image:
    fraction_x=phase_x/PARTICLE_SUBPIXEL_PHASES
    fraction_y=phase_y/PARTICLE_SUBPIXEL_PHASES
    variant=icon.transform(
        icon.size,Image.Transform.AFFINE,
        (1.0,0.0,-fraction_x,0.0,1.0,-fraction_y),
        resample=Image.Resampling.BICUBIC)
    # Bicubic interpolation can overshoot a channel by one value. Preserve the
    # established 72% particle-opacity ceiling for every fractional phase.
    alpha=variant.getchannel("A").point(lambda value:min(184,value))
    variant.putalpha(alpha)
    return variant


def _scale_about_center(icon: Image.Image, scale: float) -> Image.Image:
    inverse=1.0/max(0.01,float(scale))
    center_x=(icon.width-1)/2.0
    center_y=(icon.height-1)/2.0
    return icon.transform(
        icon.size,Image.Transform.AFFINE,
        (inverse,0.0,center_x*(1.0-inverse),
         0.0,inverse,center_y*(1.0-inverse)),
        resample=Image.Resampling.BICUBIC)

def _build_app_icon(source: Image.Image) -> Image.Image:
    rgba=source.convert("RGBA")
    scale=min(APP_ICON_SIZE[0]/rgba.width,APP_ICON_SIZE[1]/rgba.height)
    fitted=rgba.resize(
        (max(1,round(rgba.width*scale)),max(1,round(rgba.height*scale))),
        Image.Resampling.LANCZOS)
    canvas=Image.new("RGBA",APP_ICON_SIZE,(255,255,255,0))
    canvas.alpha_composite(
        fitted,((APP_ICON_SIZE[0]-fitted.width)//2,
                (APP_ICON_SIZE[1]-fitted.height)//2))
    return canvas

def _build_status_icon(source: Path) -> Image.Image:
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
    canvas.paste(fitted,((STATUS_SIZE[0]-fitted.width)//2,
                         (STATUS_SIZE[1]-fitted.height)//2))
    icon=Image.new("RGBA",STATUS_SIZE,(255,255,255,0))
    icon.putalpha(canvas)
    return icon

def build() -> None:
    app_source, bake_source = IDENTITY_SOURCE, SOURCE / "bake.png"
    if not app_source.is_file() or not bake_source.is_file():
        raise FileNotFoundError("run tools/build_icons.py before companion asset build")
    TARGET.mkdir(parents=True, exist_ok=True)
    with Image.open(app_source) as image:
        master = _build_app_icon(image)
        master.save(TARGET / "cloth_next.png", format="PNG", optimize=False,
                    compress_level=9)
    with Image.open(bake_source) as bake:
        alpha=bake.convert("RGBA").getchannel("A")
        tinted=Image.new("RGBA",bake.size,(217,154,50,0)); tinted.putalpha(alpha)
        tinted.save(TARGET/"bake.png",format="PNG",optimize=False,compress_level=9)
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
            icon=_scale_about_center(icon,PARTICLE_VISUAL_SCALE)
            alpha=icon.getchannel("A").point(lambda value:int(value*.72))
            icon.putalpha(alpha)
            particle_name=f"particle_{name}_{size}.png"
            icon.save(TARGET/particle_name,format="PNG",
                      optimize=False,compress_level=9)
            for phase_y in range(PARTICLE_SUBPIXEL_PHASES):
                for phase_x in range(PARTICLE_SUBPIXEL_PHASES):
                    _subpixel_variant(icon,phase_x,phase_y).save(
                        TARGET/_subpixel_name(
                            particle_name,phase_x,phase_y),format="PNG",
                        optimize=False,compress_level=9)
    for target_name,source_name in STATUS_ASSETS.items():
        source=STATUS_SOURCE/source_name
        if not source.is_file():
            raise FileNotFoundError(f"missing solver status icon source: {source}")
        _build_status_icon(source).save(
            TARGET/target_name,format="PNG",optimize=False,compress_level=9)
    for stale in TARGET.glob("mist_*.png"):
        stale.unlink()
    validate()

def validate() -> None:
    for name in ("cloth_next.png", "bake.png", "cloth_next.ico",
                 *PARTICLE_ASSETS,*PARTICLE_SUBPIXEL_ASSETS,*STATUS_ASSETS):
        path = TARGET / name
        if not path.is_file():
            raise FileNotFoundError(f"missing companion icon asset: {path}")
        with Image.open(path) as image:
            if name == "cloth_next.ico" and set(image.info.get("sizes", ())) != set(ICO_SIZES):
                raise ValueError("companion ICO does not contain every required size")
            image.verify()
        if name in PARTICLE_ASSETS or name in PARTICLE_SUBPIXEL_ASSETS:
            with Image.open(path) as image:
                expected=PARTICLE_ASSETS.get(
                    name,PARTICLE_SUBPIXEL_ASSETS.get(name))
                if image.mode!="RGBA" or image.size!=expected: raise ValueError(f"invalid particle asset: {name}")
                if path.stat().st_size>16*1024: raise ValueError(f"oversized particle asset: {name}")
        if name in STATUS_ASSETS:
            with Image.open(path) as image:
                rgba=image.convert("RGBA")
                visible=[pixel for pixel in rgba.get_flattened_data() if pixel[3]]
                if image.size!=STATUS_SIZE or not visible: raise ValueError(f"invalid status asset: {name}")
                if any(pixel[:3]!=(255,255,255) for pixel in visible): raise ValueError(f"status icon is not white: {name}")

if __name__ == "__main__":
    try:
        build()
    except (OSError, ValueError) as exc:
        print(f"Companion asset build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Companion identity and bake icon assets: valid")
