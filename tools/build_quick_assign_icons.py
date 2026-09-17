# SPDX-License-Identifier: GPL-3.0-or-later
"""Render supplied Quick Assign SVGs for the dark viewport (build-time only)."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageOps
import resvg_py

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"Cloth": "cloth", "Cable": "rod", "RigidBody": "rigid_body",
         "Softbody": "soft_body", "Collider": "collider"}


def build():
    source = ROOT / "assets" / "quick_assign_icons"
    target = ROOT / "cloth_next" / "assets" / "icons"
    for name, role in NAMES.items():
        data = resvg_py.svg_to_bytes(svg_path=str(source / f"{name}.svg"),
                                     width=1024, height=1024)
        with Image.open(BytesIO(data)) as rendered:
            rgba = rendered.convert("RGBA")
        # Dark-theme icons use white RGB with detail/shading in their alpha.
        # Black source strokes become white; white fills become transparent,
        # preserving internal contours on both gray and blue bubbles.
        alpha = ImageChops.multiply(rgba.getchannel("A"),
                                   ImageOps.invert(rgba.convert("L")))
        icon = Image.new("RGBA", rgba.size, (255, 255, 255, 0))
        icon.putalpha(alpha)
        bounds = alpha.getbbox()
        if bounds is None:
            raise ValueError(f"Empty icon: {name}")
        icon = icon.crop(bounds)
        icon.thumbnail((112, 112), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (128, 128), (255, 255, 255, 0))
        canvas.paste(icon, ((128-icon.width)//2, (128-icon.height)//2))
        canvas.save(target / f"quick_{role}.png", compress_level=9)
        print(f"{name}.svg -> quick_{role}.png")


if __name__ == "__main__":
    build()
