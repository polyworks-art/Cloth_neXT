# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reload-safe Blender registration for Cloth NeXt.

Registration performs no downloads, no network access, and no solver
discovery side effects; the solver installer only ever runs after an explicit
user action in the add-on preferences.

Order is deterministic: classes first (preferences, then the object
PropertyGroup, then operators, then panels), then the ``Object.cloth_next``
PointerProperty, then the single Physics-Add draw callback. A partial
registration failure rolls back every step already applied; unregister
reverts everything in strict reverse order.
"""

from __future__ import annotations

import bpy

from . import (addon_update_operators, bake_operators, bake_preview, companion_manager, hud,
               icon_registry, object_properties, physics_operators, physics_ui,
               preferences, solver_test, test_scene)

_CLASSES = (
    preferences.CLASSES
    + addon_update_operators.CLASSES
    + object_properties.CLASSES
    + physics_operators.CLASSES
    + bake_operators.CLASSES
    + test_scene.CLASSES
    + solver_test.CLASSES
    + physics_ui.CLASSES
)

_registered = False


def _steps() -> list[tuple]:
    """Ordered (apply, revert) pairs covering all registration side effects."""
    steps: list[tuple] = [
        (lambda cls=cls: bpy.utils.register_class(cls),
         lambda cls=cls: bpy.utils.unregister_class(cls))
        for cls in _CLASSES
    ]
    steps.append((object_properties.attach_to_object,
                  object_properties.detach_from_object))
    steps.append((physics_ui.append_add_physics_entry,
                  physics_ui.remove_add_physics_entry))
    steps.append((icon_registry.register, icon_registry.unregister))
    steps.append((hud.register, hud.unregister))
    return steps


def register() -> None:
    global _registered
    if _registered:
        return
    applied: list = []
    try:
        for apply_step, revert_step in _steps():
            apply_step()
            applied.append(revert_step)
    except Exception:
        for revert_step in reversed(applied):
            revert_step()
        raise
    _registered = True


def unregister() -> None:
    global _registered
    if not _registered:
        return
    # Stop installer/update workers, timers, and handles first. The solver
    # test shutdown cancels the run, stops the exact owned solver process
    # (never an external server), and joins the worker thread.
    solver_test.shutdown()
    preferences.shutdown()
    addon_update_operators.shutdown()
    bake_preview.stop()
    companion_manager.shutdown()
    for _apply_step, revert_step in reversed(_steps()):
        revert_step()
    _registered = False
