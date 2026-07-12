# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Display-only, shared-status 3D Viewport HUD."""
from __future__ import annotations
import bpy
from ..bake.controller import shared_controller

_handle = None
_draw_failed = False


def _draw():
    global _draw_failed
    if _draw_failed:
        return
    try:
        import blf
        snap = shared_controller.snapshot()
        if snap.state.value == "IDLE":
            return
        text = f"Cloth NeXt UI Preview · {snap.status_title}"
        if snap.progress_total:
            text += f" · {snap.progress_current} / {snap.progress_total} · {snap.progress_fraction:.0%}"
        blf.position(0, 24, 32, 0)
        blf.size(0, 13)
        blf.draw(0, text)
    except Exception:
        _draw_failed = True


def register():
    global _handle, _draw_failed
    if _handle is not None or not hasattr(bpy.types, "SpaceView3D"):
        return
    _draw_failed = False
    _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
