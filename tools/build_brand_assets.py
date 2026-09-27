# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build every Cloth NeXt identity asset from the approved primary mark."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
import resvg_py

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "CN_new_Logo.svg"
BLACK = (17, 17, 17)
WHITE = (245, 245, 243)


def _render_mark(size: tuple[int, int], color: tuple[int, int, int],
                 *, clear_space: float = 0.125) -> Image.Image:
    """Render the mark with the guide's minimum surrounding clear space."""
    svg = SOURCE.read_text(encoding="utf-8")
    data = resvg_py.svg_to_bytes(svg_string=svg, width=497, height=476,
                                 skip_system_fonts=True)
    with Image.open(BytesIO(data)) as rendered:
        alpha = rendered.convert("RGBA").getchannel("A")
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError("brand mark rendered empty")
    alpha = alpha.crop(bounds)
    available = (max(1, round(size[0] * (1 - 2 * clear_space))),
                 max(1, round(size[1] * (1 - 2 * clear_space))))
    scale = min(available[0] / alpha.width, available[1] / alpha.height)
    fitted = alpha.resize((max(1, round(alpha.width * scale)),
                           max(1, round(alpha.height * scale))),
                          Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    mask.paste(fitted, ((size[0] - fitted.width) // 2,
                        (size[1] - fitted.height) // 2))
    output = Image.new("RGBA", size, (*color, 0))
    output.putalpha(mask)
    return output


def _save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False, compress_level=9)


def _derived_svg(color: str) -> str:
    # The guide reserves one-eighth of the destination on every side. Keep
    # that clear space in vector consumers too, rather than only in PNGs.
    return (SOURCE.read_text(encoding="utf-8")
            .replace('viewBox="0 0 497 476"',
                     'viewBox="-83 -79 663 634"')
            .replace("#111111", color))


def build() -> None:
    black = _render_mark((1024, 1024), BLACK)
    white = _render_mark((1024, 1024), WHITE)
    _save(black, ROOT / "assets" / "Logo_CN.png")
    _save(white, ROOT / "assets" / "Logo_CN_BW.png")
    _save(black, ROOT / "assets" / "LOGO_addon.png")
    _save(_render_mark((64, 64), WHITE),
          ROOT / "cloth_next" / "assets" / "icons" / "cloth_next.png")

    onboarding = ROOT / "cloth_next" / "resources" / "onboarding"
    _save(_render_mark((58, 56), WHITE),
          onboarding / "assets" / "cloth-next-logo.png")
    _save(_render_mark((76, 73), WHITE),
          onboarding / "assets" / "cloth-next-logo-splash.png")
    _save(_render_mark((22, 22), WHITE), onboarding / "icons" / "logo.png")

    (ROOT / "assets" / "Cloth_neXt_icon.svg").write_text(
        _derived_svg("#111111"), encoding="utf-8")
    (ROOT / "assets" / "cloth-next-white.svg").write_text(
        _derived_svg("#f5f5f3"), encoding="utf-8")
    (ROOT / "assets" / "cloth_next_icons" / "cloth_next.svg").write_text(
        _derived_svg("#f5f5f3"), encoding="utf-8")
    (onboarding / "assets" / "cloth-next-logo.svg").write_text(
        _derived_svg("#f5f5f3"), encoding="utf-8")


if __name__ == "__main__":
    build()
    print("Brand assets generated from assets/CN_new_Logo.svg")
