# SPDX-License-Identifier: GPL-3.0-or-later
"""PATTERN NeXt design system: palette, metrics, icons and GPU primitives.

One accent (the Cloth NeXt cyan of the simulation toolbar), neutral
surfaces taken from the user's Blender theme (so light themes work), and
functional colours only where they carry data: seam identities, invalid
geometry, warnings. All sizes are in UI pixels and multiplied by Blender's
UI scale; icons are pre-rendered at the matching pixel size so they stay
crisp (``tools/build_pattern_icons.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import bpy

ACCENT = (0.06, 0.66, 0.85)            # Cloth NeXt cyan (floating_simulation._CYAN)
ERROR = (0.90, 0.33, 0.30)
WARNING = (0.93, 0.68, 0.25)
VALID = (0.40, 0.78, 0.52)
# Seam identities: distinguishable, equally muted (data colours, not UI).
SEAM_COLORS = ((0.36, 0.72, 0.88), (0.90, 0.62, 0.32), (0.58, 0.80, 0.42),
               (0.85, 0.50, 0.66), (0.62, 0.60, 0.92), (0.86, 0.80, 0.40),
               (0.40, 0.80, 0.72), (0.84, 0.46, 0.42))

BAR = 30          # toolbar strip height
BUTTON = 26       # toolbar button size
STATUS = 22       # status strip height
VERTEX = 6        # vertex handle size
FONT = 11

_ICON_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons" / "pattern_next"
_ICON_SIZES = (18, 27, 36, 64)
_textures = {}


def ui_scale(context=None):
    try:
        prefs = (context or bpy.context).preferences
        return max(0.5, float(prefs.system.ui_scale))
    except AttributeError:
        return 1.0


def _luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))


@dataclass(frozen=True)
class Palette:
    light: bool
    bar: tuple
    bar_line: tuple
    text: tuple
    text_dim: tuple
    hover: tuple
    active: tuple
    canvas: tuple
    grid_minor: tuple
    grid_major: tuple
    axis: tuple
    fill: tuple
    fill_selected: tuple
    outline: tuple
    outline_selected: tuple
    wire: tuple
    label_bg: tuple


_cache = {}


def palette(context=None):
    """Theme-derived palette (recomputed when the theme colours change)."""
    try:
        theme = (context or bpy.context).preferences.themes[0]
        header = tuple(theme.view_3d.space.header)
        text = tuple(theme.view_3d.space.header_text_hi)[:3]
        key = (header, text)
    except (AttributeError, IndexError):
        header, text, key = (0.19, 0.19, 0.19, 1.0), (1.0, 1.0, 1.0), None
    cached = _cache.get("palette")
    if cached is not None and cached[0] == key:
        return cached[1]
    light = _luma(header) > 0.5
    base = header[:3]
    if light:
        canvas = mix(base, (1.0, 1.0, 1.0), 0.35)
        ink = (0.16, 0.17, 0.18)
        result = Palette(
            light=True, bar=(*base, 1.0), bar_line=(*ink, 0.14), text=(*text, 1.0),
            text_dim=(*mix(text, base, 0.45), 1.0), hover=(*ink, 0.08),
            active=(*ACCENT, 0.20), canvas=(*canvas, 1.0),
            grid_minor=(*ink, 0.06), grid_major=(*ink, 0.13), axis=(*ink, 0.28),
            fill=(0.97, 0.97, 0.965, 1.0), fill_selected=(0.93, 0.965, 0.985, 1.0),
            outline=(*ink, 0.9), outline_selected=(*ACCENT, 1.0), wire=(*ink, 0.16),
            label_bg=(*canvas, 0.86))
    else:
        canvas = mix(base, (0.0, 0.0, 0.0), 0.18)
        result = Palette(
            light=False, bar=(*base, 1.0), bar_line=(1.0, 1.0, 1.0, 0.09), text=(*text, 1.0),
            text_dim=(*mix(text, base, 0.42), 1.0), hover=(1.0, 1.0, 1.0, 0.07),
            active=(*ACCENT, 0.22), canvas=(*canvas, 1.0),
            grid_minor=(1.0, 1.0, 1.0, 0.035), grid_major=(1.0, 1.0, 1.0, 0.085),
            axis=(1.0, 1.0, 1.0, 0.20),
            fill=(0.50, 0.515, 0.53, 1.0), fill_selected=(0.56, 0.595, 0.62, 1.0),
            outline=(0.88, 0.89, 0.90, 1.0), outline_selected=(*ACCENT, 1.0),
            wire=(0.0, 0.0, 0.0, 0.22), label_bg=(*canvas, 0.82))
    _cache["palette"] = (key, result)
    return result


def fabric_fill(pal, material, selected):
    """Neutral fill, gently tinted by an assigned material's viewport colour."""
    base = pal.fill_selected if selected else pal.fill
    if material is None:
        return base
    try:
        tint = tuple(material.diffuse_color)[:3]
    except AttributeError:
        return base
    return (*mix(base[:3], tint, 0.38), base[3])


def seam_color(index):
    return SEAM_COLORS[index % len(SEAM_COLORS)]


# -- icons -------------------------------------------------------------------------

def _png(path):
    from ..icon_registry import _png_rgba
    return _png_rgba(path)


def icon_texture(name, pixels):
    """GPU texture of icon ``name`` at the closest pre-rendered size >= pixels."""
    import gpu
    size = next((s for s in _ICON_SIZES if s >= pixels - 0.5), _ICON_SIZES[-1])
    key = (name, size)
    texture = _textures.get(key)
    if texture is None:
        path = _ICON_DIR / f"{name}_{size}.png"
        if not path.is_file():
            return None, size
        width, height, rgba = _png(path)
        buffer = gpu.types.Buffer("FLOAT", width * height * 4, [v / 255.0 for v in rgba])
        texture = gpu.types.GPUTexture((width, height), format="RGBA8", data=buffer)
        _textures[key] = texture
    return texture, size


def draw_icon(name, cx, cy, pixels, color):
    """Monochrome icon centred at (cx, cy), tinted with ``color``."""
    from gpu_extras.batch import batch_for_shader
    import gpu
    texture, size = icon_texture(name, pixels)
    if texture is None:
        return
    # Pixel-snap so 1:1 icons stay crisp.
    x = round(cx - size / 2)
    y = round(cy - size / 2)
    shader = gpu.shader.from_builtin("IMAGE_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN", {
        "pos": ((x, y), (x + size, y), (x + size, y + size), (x, y + size)),
        "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))})
    shader.bind()
    shader.uniform_sampler("image", texture)
    shader.uniform_float("color", color)
    batch.draw(shader)


def icon_pixels(context=None):
    scale = ui_scale(context)
    return 18 if scale < 1.25 else (27 if scale < 1.75 else 36)


def clear_textures():
    _textures.clear()


# -- primitives (region pixels) ------------------------------------------------------

def _uniform():
    import gpu
    return gpu.shader.from_builtin("UNIFORM_COLOR")


def rect(x, y, w, h, color):
    from gpu_extras.batch import batch_for_shader
    shader = _uniform()
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": ((x, y), (x + w, y), (x + w, y + h), (x, y + h))},
                     indices=((0, 1, 2), (0, 2, 3))).draw(shader)


def rounded(x, y, w, h, r, color):
    from gpu_extras.batch import batch_for_shader
    r = max(0.0, min(r, w / 2, h / 2))
    if r < 0.5:
        rect(x, y, w, h, color)
        return
    points = []
    for cx, cy, start in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0),
                          (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for i in range(5):
            a = math.radians(start + i * 22.5)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    verts = [(x + w / 2, y + h / 2)] + points
    indices = [(0, i + 1, (i + 1) % len(points) + 1) for i in range(len(points))]
    shader = _uniform()
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices).draw(shader)


def lines_px(pairs, color, width=1.0, region=None):
    if len(pairs) < 2:
        return
    from gpu_extras.batch import batch_for_shader
    import gpu
    region = region or bpy.context.region
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": pairs}).draw(shader)


def disc(cx, cy, radius, color, segments=16):
    from gpu_extras.batch import batch_for_shader
    verts = [(cx, cy)] + [(cx + radius * math.cos(2 * math.pi * k / segments),
                           cy + radius * math.sin(2 * math.pi * k / segments))
                          for k in range(segments)]
    indices = [(0, k + 1, (k + 1) % segments + 1) for k in range(segments)]
    shader = _uniform()
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices).draw(shader)


def ring(cx, cy, radius, color, width=1.0, segments=20, region=None):
    pts = [(cx + radius * math.cos(2 * math.pi * k / segments),
            cy + radius * math.sin(2 * math.pi * k / segments)) for k in range(segments + 1)]
    pairs = []
    for a, b in zip(pts, pts[1:]):
        pairs += [a, b]
    lines_px(pairs, color, width, region)


def square(cx, cy, size, fill, edge=None, region=None):
    half = size / 2
    x, y = round(cx - half) + 0.0, round(cy - half) + 0.0
    if edge is not None:
        rect(x - 1, y - 1, size + 2, size + 2, edge)
    rect(x, y, size, size, fill)


def diamond(cx, cy, size, color):
    from gpu_extras.batch import batch_for_shader
    h = size / 2
    shader = _uniform()
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": ((cx, cy - h), (cx + h, cy), (cx, cy + h),
                                              (cx - h, cy))},
                     indices=((0, 1, 2), (0, 2, 3))).draw(shader)


def text(value, x, y, color, size=FONT, align="LEFT", scale=None):
    """Draw text; returns its width. No shadows: labels sit on surfaces."""
    import blf
    import gpu
    size = round(size * (scale if scale is not None else ui_scale()))
    blf.size(0, size)
    width, _height = blf.dimensions(0, value)
    if align == "CENTER":
        x -= width / 2
    elif align == "RIGHT":
        x -= width
    blf.color(0, *color)
    blf.position(0, round(x), round(y), 0)
    blf.draw(0, value)
    gpu.state.blend_set("ALPHA")
    return width


def text_width(value, size=FONT, scale=None):
    import blf
    blf.size(0, round(size * (scale if scale is not None else ui_scale())))
    return blf.dimensions(0, value)[0]


def fit(value, width, size=FONT, scale=None):
    """Truncate with an ellipsis so the text fits ``width`` pixels."""
    if text_width(value, size, scale) <= width:
        return value
    while value and text_width(value + "…", size, scale) > width:
        value = value[:-1]
    return (value + "…") if value else ""


def label(value, x, y, pal, color=None, size=10, align="CENTER", scale=None):
    """Measurement label on a small canvas-coloured plate (never a shadow)."""
    s = scale if scale is not None else ui_scale()
    w = text_width(value, size, s)
    pad, h = 3 * s, (size + 5) * s
    left = x - w / 2 if align == "CENTER" else (x - w if align == "RIGHT" else x)
    rounded(left - pad, y - 3 * s, w + 2 * pad, h, 3 * s, pal.label_bg)
    text(value, left, y, color or pal.text_dim, size, "LEFT", s)
    return w + 2 * pad
