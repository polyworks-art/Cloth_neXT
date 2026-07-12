"""Reload-safe Blender registration for Cloth NeXt.

Registration performs no downloads, no network access, and no solver
discovery side effects; the solver installer only ever runs after an explicit
user action in the add-on preferences.
"""

from __future__ import annotations

import bpy

from . import preferences

_CLASSES = preferences.CLASSES
_registered = False


def register() -> None:
    global _registered
    if _registered:
        return
    registered: list[type] = []
    try:
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
    except Exception:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise
    _registered = True


def unregister() -> None:
    global _registered
    if not _registered:
        return
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _registered = False
