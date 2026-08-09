# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-rendering Object Color presentation for Cloth NeXt roles."""

from __future__ import annotations

import bpy


ROLE_COLORS = {
    "CLOTH": (0.08, 0.32, 0.80, 1.0),
    "COLLIDER": (0.82, 0.07, 0.06, 1.0),
    "ROD": (0.95, 0.42, 0.05, 1.0),
    "SOFT_BODY": (0.12, 0.65, 0.28, 1.0),
    "RIGID_BODY": (0.52, 0.16, 0.78, 1.0),
    "FORCE": (0.95, 0.70, 0.06, 1.0),
}

_ORIGINAL_COLOR = "_cloth_next_original_viewport_color"
_shading_states: list[tuple[object, str]] = []


def role_color(role: str) -> tuple[float, float, float, float]:
    return ROLE_COLORS.get(str(role), (0.35, 0.35, 0.35, 1.0))


def _stored_color(obj):
    try:
        value = obj.get(_ORIGINAL_COLOR)
    except (AttributeError, TypeError, RuntimeError):
        value = None
    return tuple(value) if value is not None and len(value) == 4 else None


def apply_object(obj) -> None:
    """Apply or restore one object's display-only color."""
    settings = getattr(obj, "cloth_next", None)
    enabled = bool(settings is not None and getattr(settings, "enabled", False))
    if not enabled:
        restore_object(obj)
        return
    if _stored_color(obj) is None:
        try:
            obj[_ORIGINAL_COLOR] = tuple(float(value) for value in obj.color)
        except (AttributeError, TypeError, RuntimeError):
            pass
    try:
        obj.color = role_color(getattr(settings, "role", ""))
    except (AttributeError, TypeError, RuntimeError):
        pass


def restore_object(obj) -> None:
    original = _stored_color(obj)
    if original is None:
        return
    try:
        obj.color = original
        del obj[_ORIGINAL_COLOR]
    except (AttributeError, KeyError, TypeError, RuntimeError):
        pass


def _view3d_spaces():
    window_manager = getattr(getattr(bpy, "context", None),
                             "window_manager", None)
    for window in getattr(window_manager, "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if getattr(area, "type", "") != "VIEW_3D":
                continue
            for space in getattr(area, "spaces", ()):
                if getattr(space, "type", "") == "VIEW_3D":
                    yield space


def register() -> None:
    _shading_states.clear()
    for space in _view3d_spaces():
        shading = getattr(space, "shading", None)
        if (shading is not None and getattr(shading, "type", "") == "SOLID"
                and getattr(shading, "color_type", "") != "OBJECT"):
            _shading_states.append((shading, shading.color_type))
            shading.color_type = "OBJECT"
    for obj in getattr(getattr(bpy, "data", None), "objects", ()):
        apply_object(obj)


def unregister() -> None:
    for obj in getattr(getattr(bpy, "data", None), "objects", ()):
        restore_object(obj)
    for shading, color_type in reversed(_shading_states):
        try:
            shading.color_type = color_type
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    _shading_states.clear()
