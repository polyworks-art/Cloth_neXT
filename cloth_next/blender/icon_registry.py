# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reload-safe custom preview lifecycle with built-in fallbacks."""
from __future__ import annotations
from pathlib import Path
import bpy

_collection = None
_NAMES = ("cloth_next", "gaia_engine", "gaia_water", "cloth", "rod", "soft_body", "collider", "force", "solver", "quality", "physical",
          "damping", "collision", "pressure", "pinning", "cache", "advanced",
          "bake", "play", "pause", "cancel", "success", "warning", "error",
          "info", "folder", "timer", "setup", "shape", "rest_shape",
          "sewing", "friction_regions", "simulation_proxy",
          "solver_settings", "engine", "result", "diagnostics",
          "maintenance", "add", "download", "gpu", "health", "link",
          "monitor", "question", "remove", "settings", "update", "upload",
          "validate", "cloud", "tethys")


# Icons whose preview pixels are set directly from the PNG. Blender builds the
# small icon of an "IMAGE" preview lazily and, for newly added files, may show
# nothing; explicit pixels always draw.
_PIXEL_ICONS = {"tethys"}


def _png_rgba(path):
    """Decode an 8-bit RGBA, non-interlaced PNG (the icon build's output)."""
    import struct
    import zlib
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    offset, idat, width = 8, b"", 0
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour != 6 or interlace:
                raise ValueError("unsupported PNG layout")
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    raw, stride = zlib.decompress(idat), width * 4
    rows, previous = [], bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - 4] if i >= 4 else 0
            up, corner = previous[i], previous[i - 4] if i >= 4 else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        rows.append(bytes(line))
        previous = line
    # Blender previews store rows bottom-up.
    return width, height, b"".join(reversed(rows))


def _load_pixels(collection, name, path):
    width, height, rgba = _png_rgba(path)
    pixels = []
    for i in range(0, len(rgba), 4):
        alpha = rgba[i + 3] / 255.0
        # Preview pixels are premultiplied.
        pixels += [rgba[i] / 255.0 * alpha, rgba[i + 1] / 255.0 * alpha, rgba[i + 2] / 255.0 * alpha, alpha]
    item = collection.new(name)
    item.image_size = (width, height)
    item.image_pixels_float.foreach_set(pixels)
    item.icon_size = (width, height)
    item.icon_pixels_float.foreach_set(pixels)


def register():
    global _collection
    if _collection is not None:
        return
    previews = getattr(bpy.utils, "previews", None)
    if previews is None:
        try:
            import bpy.utils.previews as previews
        except ImportError:
            previews = None
    if previews is None:
        return
    collection = previews.new()
    directory = Path(__file__).resolve().parent.parent / "assets" / "icons"
    try:
        for name in _NAMES:
            path = directory / f"{name}.png"
            if path.is_file():
                try:
                    if name in _PIXEL_ICONS:
                        _load_pixels(collection, name, path)
                    else:
                        collection.load(name, str(path), "IMAGE")
                except Exception:
                    pass
        _collection = collection
    except Exception:
        previews.remove(collection)
        raise


def unregister():
    global _collection
    if _collection is not None:
        previews = getattr(bpy.utils, "previews", None)
        if previews is None:
            import bpy.utils.previews as previews
        previews.remove(_collection)
        _collection = None


def icon_id(name: str) -> int:
    if _collection is None:
        return 0
    item = _collection.get(name)
    return int(getattr(item, "icon_id", 0)) if item else 0


def icon_kwargs(name: str, fallback: str = "NONE") -> dict:
    value = icon_id(name)
    return {"icon_value": value} if value else {"icon": fallback}
