# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Create compact runtime onboarding assets from approved source artwork."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

ICON_NAMES = ("logo", "link", "cloth", "play", "rocket", "docs", "settings",
              "sliders", "shield", "search", "refresh", "rig", "check",
              "arrow", "changelog", "close")


def build_icon_sheet(source: Path, output_root: Path) -> tuple[Path, ...]:
    image = Image.open(source).convert("RGBA")
    output_root.mkdir(parents=True, exist_ok=True)
    outputs = []
    for index, name in enumerate(ICON_NAMES):
        column, row = index % 4, index // 4
        left = round(column * image.width / 4)
        right = round((column + 1) * image.width / 4)
        top = round(row * image.height / 4)
        bottom = round((row + 1) * image.height / 4)
        cell = image.crop((left, top, right, bottom))
        bounds = cell.getbbox()
        if bounds is None:
            raise ValueError(f"icon cell {name} is empty")
        cell = cell.crop(bounds)
        cell.thumbnail((20, 20), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
        canvas.alpha_composite(
            cell, ((canvas.width - cell.width) // 2,
                   (canvas.height - cell.height) // 2))
        output = output_root / f"{name}.png"
        canvas.save(output, optimize=True)
        outputs.append(output)
    return tuple(outputs)


def build_hero(source: Path, output: Path) -> Path:
    image = Image.open(source).convert("RGB")
    image = ImageOps.fit(image, (175, 390), method=Image.Resampling.LANCZOS,
                         centering=(0.58, 0.50))
    image = ImageEnhance.Brightness(image).enhance(0.78)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icon-sheet", type=Path)
    parser.add_argument("--hero", type=Path)
    parser.add_argument("--output-root", type=Path,
                        default=Path("cloth_next/resources/onboarding"))
    args = parser.parse_args()
    if not args.icon_sheet and not args.hero:
        parser.error("provide --icon-sheet and/or --hero")
    if args.icon_sheet:
        build_icon_sheet(args.icon_sheet, args.output_root / "icons")
    if args.hero:
        build_hero(args.hero, args.output_root / "assets" / "hero-panel.png")
    print(f"onboarding assets built in {args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
