# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reload-safe custom preview lifecycle with built-in fallbacks."""
from __future__ import annotations
from pathlib import Path
import bpy

_collection = None
_NAMES = ("cloth_next", "cloth", "rod", "soft_body", "collider", "force", "solver", "quality", "physical",
          "damping", "collision", "pressure", "pinning", "cache", "advanced",
          "bake", "play", "pause", "cancel", "success", "warning", "error",
          "info", "folder", "timer")


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
