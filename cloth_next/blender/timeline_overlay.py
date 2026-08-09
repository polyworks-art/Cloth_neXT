# SPDX-License-Identifier: GPL-3.0-or-later
"""Thin Cloth NeXt baked-frame indicator in Blender's Timeline."""

from __future__ import annotations

import bpy


BAKED_COLOR = (0.02, 0.72, 0.64, 0.95)
TRACK_LABEL = "Cloth NeXt Bake"
TRACK_HEIGHT = 19.0
STRIP_HEIGHT = 17.0
TRACK_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)

_handle = None
_label_handle = None
_range: tuple[int, int, int] | None = None


def baked_range() -> tuple[int, int, int] | None:
    return _range


def set_baked_range(start: int, latest: int, end: int) -> None:
    """Show the inclusive completed range, clamped to the Bake bounds."""
    global _range
    first, final = int(start), int(end)
    newest = min(final, max(first, int(latest)))
    _range = (first, newest, final)
    _redraw()


def clear() -> None:
    global _range
    _range = None
    _redraw()


def _redraw() -> None:
    manager = getattr(getattr(bpy, "context", None), "window_manager", None)
    for window in getattr(manager, "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if getattr(area, "type", "") == "DOPESHEET_EDITOR":
                try:
                    area.tag_redraw()
                except (AttributeError, ReferenceError, RuntimeError):
                    pass


def _draw() -> None:
    if _range is None:
        return
    area = getattr(bpy.context, "area", None)
    space = getattr(bpy.context, "space_data", None)
    region = getattr(bpy.context, "region", None)
    if (getattr(area, "type", "") != "DOPESHEET_EDITOR"
            or getattr(space, "mode", "") != "TIMELINE" or region is None):
        return
    start, latest, _end = _range
    try:
        x0 = float(region.view2d.view_to_region(start, 0.0, clip=False)[0])
        # The half-open right edge fills the latest completed frame cell.
        x1 = float(region.view2d.view_to_region(latest + 1, 0.0, clip=False)[0])
        left = max(0.0, min(x0, float(region.width)))
        right = max(0.0, min(x1, float(region.width)))
        if right <= left:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "TRI_FAN", {"pos": (
            (left, 1.0), (right, 1.0),
            (right, 1.0 + STRIP_HEIGHT), (left, 1.0 + STRIP_HEIGHT),
        )})
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("color", BAKED_COLOR)
        batch.draw(shader)
        gpu.state.blend_set("NONE")
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return


def _draw_label() -> None:
    if _range is None:
        return
    area = getattr(bpy.context, "area", None)
    space = getattr(bpy.context, "space_data", None)
    if (getattr(area, "type", "") != "DOPESHEET_EDITOR"
            or getattr(space, "mode", "") != "TIMELINE"):
        return
    try:
        import gpu
        import blf
        from gpu_extras.batch import batch_for_shader
        region = getattr(bpy.context, "region", None)
        if region is None:
            return
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "TRI_FAN", {"pos": (
            (0.0, 0.0), (float(region.width), 0.0),
            (float(region.width), TRACK_HEIGHT), (0.0, TRACK_HEIGHT),
        )})
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("color", BAKED_COLOR)
        batch.draw(shader)
        gpu.state.blend_set("NONE")
        font_id = 0
        blf.size(font_id, 11.0)
        blf.color(font_id, *TRACK_TEXT_COLOR)
        blf.position(font_id, 8.0, 4.0, 0.0)
        blf.draw(font_id, TRACK_LABEL)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return


def register() -> None:
    global _handle, _label_handle
    space = getattr(getattr(bpy, "types", None), "SpaceDopeSheetEditor", None)
    if _handle is None and space is not None:
        _handle = space.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")
    if _label_handle is None and space is not None:
        try:
            _label_handle = space.draw_handler_add(
                _draw_label, (), "CHANNELS", "POST_PIXEL")
        except (TypeError, ValueError):
            # Some Blender-compatible API stubs expose only WINDOW handlers.
            _label_handle = None


def unregister() -> None:
    global _handle, _label_handle
    space = getattr(getattr(bpy, "types", None), "SpaceDopeSheetEditor", None)
    if _handle is not None and space is not None:
        try:
            space.draw_handler_remove(_handle, "WINDOW")
        except (ReferenceError, RuntimeError):
            pass
    _handle = None
    if _label_handle is not None and space is not None:
        try:
            space.draw_handler_remove(_label_handle, "CHANNELS")
        except (ReferenceError, RuntimeError):
            pass
    _label_handle = None
    clear()
