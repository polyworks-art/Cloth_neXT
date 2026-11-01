# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the PATTERN NeXt runtime icons from assets/pattern_next_icons/*.svg.

Same family as the Cloth NeXt icons (24 px grid, 1.8 px round strokes,
monochrome white, alpha antialiasing). Each icon is rendered at the pixel
sizes the 2D editor toolbar draws (crisp at UI scale 1, 1.5 and 2) plus the
64 px preview used by the sidebar.

    python tools/build_pattern_icons.py [--check] [--sheet out.png]
"""
from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import re
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "pattern_next_icons"
TARGET = ROOT / "cloth_next" / "assets" / "icons" / "pattern_next"
SIZES = (18, 27, 36, 64)


def names():
    return sorted(p.stem for p in SOURCE.glob("*.svg"))


def render(name, size):
    import resvg_py
    svg = (SOURCE / f"{name}.svg").read_text(encoding="utf-8")
    svg = svg.replace("currentColor", "#ffffff")
    svg = re.sub(r"<svg ", f'<svg width="{size}" height="{size}" ', svg, count=1)
    data = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size,
                                 skip_system_fonts=True)
    with Image.open(BytesIO(bytes(data))) as image:
        image = image.convert("RGBA")
    alpha = image.getchannel("A")
    white = Image.new("RGBA", image.size, (255, 255, 255, 0))
    white.putalpha(alpha)
    out = BytesIO()
    white.save(out, format="PNG", optimize=False, compress_level=9)
    return out.getvalue()


def build():
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in names():
        for size in SIZES:
            (TARGET / f"{name}_{size}.png").write_bytes(render(name, size))


def check():
    for name in names():
        for size in SIZES:
            path = TARGET / f"{name}_{size}.png"
            if not path.is_file():
                raise ValueError(f"missing {path}")
            with Image.open(path) as image:
                if image.size != (size, size) or image.mode != "RGBA":
                    raise ValueError(f"invalid {path}")


def sheet(path):
    """Contact sheet on dark and light backgrounds for design review."""
    items = names()
    cell = 48
    out = Image.new("RGBA", (cell * len(items), cell * 2 + 8), (0, 0, 0, 255))
    for row, (bg, tint) in enumerate((((40, 43, 46), (230, 234, 238)),
                                      ((236, 236, 236), (40, 44, 48)))):
        for i, name in enumerate(items):
            box = Image.new("RGBA", (cell, cell), (*bg, 255))
            with Image.open(TARGET / f"{name}_27.png") as icon:
                tinted = Image.new("RGBA", icon.size, (*tint, 0))
                tinted.putalpha(icon.getchannel("A"))
                box.alpha_composite(tinted, ((cell - 27) // 2, (cell - 27) // 2))
            out.paste(box, (i * cell, row * (cell + 8)))
    out.save(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--sheet")
    args = parser.parse_args(argv)
    try:
        check() if args.check else build()
        if args.sheet:
            sheet(args.sheet)
    except (OSError, ValueError, ImportError) as exc:
        print(f"PATTERN NeXt icon build failed: {exc}", file=sys.stderr)
        return 1
    print(f"PATTERN NeXt icons: {len(names())} x {len(SIZES)} sizes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
