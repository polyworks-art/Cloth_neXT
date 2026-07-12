# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build/check deterministic 64px PNG previews from approved SVG sources."""
from __future__ import annotations
import argparse
from io import BytesIO
from pathlib import Path
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "cloth_next_icons"
TARGET = ROOT / "cloth_next" / "assets" / "icons"
REQUIRED = ("cloth_next", "cloth", "collider", "solver", "quality", "physical",
            "damping", "collision", "pressure", "pinning", "cache", "advanced",
            "bake", "play", "pause", "cancel", "success", "warning", "error",
            "info", "folder", "timer")
SIZE = (64, 64)

def _render(source: Path) -> bytes:
    try:
        import resvg_py
    except ImportError as exc:
        raise RuntimeError("resvg-py is required only for the icon build") from exc
    rendered = resvg_py.svg_to_bytes(svg_path=str(source), width=SIZE[0],
                                     height=SIZE[1], skip_system_fonts=True)
    with Image.open(BytesIO(rendered)) as image:
        image = image.convert("RGBA")
        # Blender does not theme custom preview pixels. Render the single
        # approved icon family as white so it remains legible in the default
        # dark UI; antialiasing stays encoded in the original alpha channel.
        alpha = image.getchannel("A")
        image = Image.new("RGBA", image.size, (255, 255, 255, 0))
        image.putalpha(alpha)
        offset = ((SIZE[0] - image.width) // 2,
                  (SIZE[1] - image.height) // 2)
        alpha_canvas = Image.new("L", SIZE, 0)
        alpha_canvas.paste(alpha, offset)
        canvas = Image.new("RGBA", SIZE, (255, 255, 255, 0))
        canvas.putalpha(alpha_canvas)
        output = BytesIO()
        canvas.save(output, format="PNG", optimize=False, compress_level=9)
        return output.getvalue()

def validate() -> None:
    for name in REQUIRED:
        source, output = SOURCE / f"{name}.svg", TARGET / f"{name}.png"
        if not source.is_file(): raise ValueError(f"missing required SVG: {source}")
        if not output.is_file(): raise ValueError(f"missing runtime PNG: {output}")
        try:
            with Image.open(output) as image:
                image.load()
                if image.format != "PNG" or image.size != SIZE:
                    raise ValueError(f"invalid runtime icon: {output}")
                rgba = image.convert("RGBA")
                if any(pixel[:3] != (255, 255, 255)
                       for pixel in rgba.getdata() if pixel[3]):
                    raise ValueError(f"runtime icon is not white: {output}")
        except OSError as exc: raise ValueError(f"unreadable runtime icon: {output}") from exc

def build() -> None:
    missing = [name for name in REQUIRED if not (SOURCE / f"{name}.svg").is_file()]
    if missing: raise ValueError("missing required SVG concepts: " + ", ".join(missing))
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED:
        data = _render(SOURCE / f"{name}.svg")
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.size != SIZE:
                raise ValueError(f"renderer produced invalid {name}.png")
        (TARGET / f"{name}.png").write_bytes(data)
    validate()

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--check", action="store_true")
    args=parser.parse_args(argv)
    try: validate() if args.check else build()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Icon build failed: {exc}", file=sys.stderr); return 1
    print(f"Runtime icons: valid ({len(REQUIRED)} × {SIZE[0]}px)"); return 0

if __name__ == "__main__": raise SystemExit(main())
