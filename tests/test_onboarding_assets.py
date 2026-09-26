# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from PIL import Image

from tools.build_onboarding_assets import (ICON_NAMES, build_hero,
                                           build_icon_sheet,
                                           build_monochrome_icons)


def test_icon_sheet_builds_sixteen_tiny_transparent_runtime_assets(tmp_path):
    source = tmp_path / "sheet.png"
    image = Image.new("RGBA", (42, 42), (0, 0, 0, 0))
    for index in range(16):
        x = round((index % 4) * image.width / 4)
        y = round((index // 4) * image.height / 4)
        image.putpixel((min(x + 1, 41), min(y + 1, 41)), (0, 100, 255, 255))
    image.save(source)
    outputs = build_icon_sheet(source, tmp_path / "icons")
    assert tuple(path.stem for path in outputs) == ICON_NAMES
    assert all(Image.open(path).size == (22, 22) for path in outputs)


def test_hero_build_is_exact_runtime_panel_size(tmp_path):
    source = tmp_path / "hero.png"
    Image.new("RGB", (400, 500), "white").save(source)
    output = build_hero(source, tmp_path / "hero-panel.png")
    assert Image.open(output).size == (175, 390)


def test_monochrome_icon_build_preserves_alpha_and_removes_color(tmp_path):
    icons = tmp_path / "icons"
    icons.mkdir()
    for name in ICON_NAMES:
        image = Image.new("RGBA", (22, 22), (20, 80, 240, 0))
        image.putpixel((11, 11), (20, 80, 240, 255))
        image.save(icons / f"{name}.png")
    outputs = build_monochrome_icons(icons)
    assert tuple(path.stem for path in outputs) == ICON_NAMES
    assert all(Image.open(path).getpixel((11, 11)) == (245, 245, 243, 255)
               for path in outputs)
