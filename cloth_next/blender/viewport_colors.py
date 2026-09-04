# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-rendering Object Color presentation for Cloth NeXt roles."""

from __future__ import annotations

import bpy

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
_shading_states: list[tuple[object, str]] = []
_REFRESH_INTERVAL_SECONDS = 1.0


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
    refresh_viewports()


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


def refresh_viewports() -> bool:
    """Keep every live Solid viewport in Object Color mode and redraw it.

    Blender can create new VIEW_3D spaces after add-on registration (workspace
    changes and file loads are common examples), so doing this only once during
    ``register`` is not sufficient.
    """
    known = {id(shading) for shading, _color_type in _shading_states}
    changed_any = False
    for area, space in _view3d_spaces():
        shading = getattr(space, "shading", None)
        if shading is None or getattr(shading, "type", "") != "SOLID":
            continue
        if getattr(shading, "color_type", "") != "OBJECT":
            if id(shading) not in known:
                _shading_states.append((shading, shading.color_type))
                known.add(id(shading))
            try:
                shading.color_type = "OBJECT"
                changed_any = True
            except (AttributeError, ReferenceError, RuntimeError):
                continue
            try:
                area.tag_redraw()
            except (AttributeError, ReferenceError, RuntimeError):
                pass
    return changed_any


def _refresh_timer():
    refresh_viewports()
    return _REFRESH_INTERVAL_SECONDS


@persistent
def _on_load_pre_clear_shading_states(*_args) -> None:
    """Drop RNA pointers before Blender replaces the current file database.

    ``SpaceView3D.shading`` objects belong to the current screen data.  They
    cannot be dereferenced after ``open_mainfile``; even an exception guard is
    too late because the invalid RNA access can crash in Blender's native code.
    """
    _shading_states.clear()


_on_load_pre_clear_shading_states._clothnext_viewport_handler = True


def _purge_stale_load_handlers(container) -> None:
    for callback in list(container):
        if (getattr(callback, "_clothnext_viewport_handler", False)
                and callback is not _on_load_pre_clear_shading_states):
            container.remove(callback)


def register() -> None:
    _shading_states.clear()
    load_pre = getattr(getattr(getattr(bpy, "app", None), "handlers", None),
                       "load_pre", None)
    if load_pre is not None:
        _purge_stale_load_handlers(load_pre)
        if _on_load_pre_clear_shading_states not in load_pre:
            load_pre.append(_on_load_pre_clear_shading_states)
    refresh_viewports()
    for obj in getattr(getattr(bpy, "data", None), "objects", ()):
        apply_object(obj)
    timers = getattr(getattr(bpy, "app", None), "timers", None)
    if timers is not None and not timers.is_registered(_refresh_timer):
        try:
            timers.register(_refresh_timer, first_interval=0.1, persistent=True)
        except TypeError:
            # Minimal Blender API stubs used by tests and older compatible API
            # surfaces may not expose the optional persistent keyword.
            timers.register(_refresh_timer, first_interval=0.1)


def unregister() -> None:
    load_pre = getattr(getattr(getattr(bpy, "app", None), "handlers", None),
                       "load_pre", None)
    if load_pre is not None:
        while _on_load_pre_clear_shading_states in load_pre:
            load_pre.remove(_on_load_pre_clear_shading_states)
        _purge_stale_load_handlers(load_pre)
    timers = getattr(getattr(bpy, "app", None), "timers", None)
    if timers is not None and timers.is_registered(_refresh_timer):
        timers.unregister(_refresh_timer)
    for obj in getattr(getattr(bpy, "data", None), "objects", ()):
        restore_object(obj)
    for shading, color_type in reversed(_shading_states):
        try:
            shading.color_type = color_type
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    _shading_states.clear()
