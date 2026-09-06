# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-rendering Object Color presentation for Cloth NeXt roles."""

from __future__ import annotations

import bpy

from .addon_identity import addon_preferences

try:
    from bpy.app.handlers import persistent
except (ImportError, ModuleNotFoundError):  # pragma: no cover - lightweight stubs
    def persistent(function):
        function._bpy_persistent = None
        return function


ROLE_COLORS = {
    "CLOTH": (0.08, 0.32, 0.80, 1.0),
    "COLLIDER": (0.82, 0.07, 0.06, 1.0),
    "ROD": (0.95, 0.42, 0.05, 1.0),
    "SOFT_BODY": (0.12, 0.65, 0.28, 1.0),
    "RIGID_BODY": (0.52, 0.16, 0.78, 1.0),
    "FORCE": (0.95, 0.70, 0.06, 1.0),
}

_ORIGINAL_COLOR = "_cloth_next_original_viewport_color"
def role_colors_enabled(context=None) -> bool:
    try:
        preferences = addon_preferences(context or bpy.context, __package__)
        return bool(getattr(preferences, "show_role_colors", False))
    except (AttributeError, KeyError):
        return False


def role_color(role: str) -> tuple[float, float, float, float]:
    return ROLE_COLORS.get(str(role), (0.35, 0.35, 0.35, 1.0))


def _stored_color(obj):
    try:
        value = obj.get(_ORIGINAL_COLOR)
    except (AttributeError, TypeError, RuntimeError):
        value = None
    return tuple(value) if value is not None and len(value) == 4 else None


def apply_object(obj, *, use_role_colors=None) -> None:
    """Apply or restore one object's display-only color."""
    settings = getattr(obj, "cloth_next", None)
    enabled = bool(settings is not None and getattr(settings, "enabled", False))
    if use_role_colors is None:
        use_role_colors = role_colors_enabled()
    if not enabled or not use_role_colors:
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
                    yield area, space


def refresh_viewports() -> None:
    """Redraw role colors without changing the artist's shading selection."""
    for area, _space in _view3d_spaces():
        area.tag_redraw()


def synchronize_objects(*, use_role_colors=None) -> None:
    for obj in getattr(getattr(bpy, "data", None), "objects", ()):
        apply_object(obj, use_role_colors=use_role_colors)
    refresh_viewports()


def update_role_colors(self, _context) -> None:
    synchronize_objects(use_role_colors=self.show_role_colors)


@persistent
def _on_load_post(*_args) -> None:
    synchronize_objects()


_on_load_post._clothnext_viewport_handler = True


def _remove_load_handlers() -> None:
    for name in ("load_pre", "load_post"):
        handlers = getattr(bpy.app.handlers, name, ())
        for callback in list(handlers):
            if getattr(callback, "_clothnext_viewport_handler", False):
                handlers.remove(callback)


def register() -> None:
    _remove_load_handlers()
    bpy.app.handlers.load_post.append(_on_load_post)
    synchronize_objects()


def unregister() -> None:
    _remove_load_handlers()
    for obj in getattr(getattr(bpy, "data", None), "objects", ()):
        restore_object(obj)
    refresh_viewports()
