# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Operators that enable and disable Cloth NeXt on an object (Phase 2.8A).

Enabling Cloth NeXt is pure property state: no native Cloth modifier is
created, no solver is started, and no network or filesystem work happens.
The solver does not need to be installed to set up an object.
"""

from __future__ import annotations

import bpy

from . import object_properties
from ..bake.controller import shared_controller


def _active_mesh(context):
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type != "MESH":
        return None
    return obj


class CLOTHNEXT_OT_add_physics(bpy.types.Operator):
    """Enable Cloth NeXt physics on the active mesh object"""

    bl_idname = "clothnext.add_physics"
    bl_label = "Cloth NeXt"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if shared_controller.snapshot().active:
            return False
        obj = _active_mesh(context)
        if obj is None:
            return False
        settings = getattr(obj, "cloth_next", None)
        return settings is not None and not settings.enabled

    def execute(self, context):
        obj = context.active_object
        settings = obj.cloth_next
        settings.enabled = True
        settings.role = object_properties.DEFAULT_ROLE
        scene = getattr(context, "scene", getattr(bpy.context, "scene", None))
        if scene is not None:
            settings.bake_start = int(scene.frame_start)
            settings.bake_end = int(scene.frame_end)
        self.report({"INFO"}, f"Cloth NeXt enabled on '{obj.name}'.")
        return {"FINISHED"}


class CLOTHNEXT_OT_remove_physics(bpy.types.Operator):
    """Remove Cloth NeXt from the active object (Cloth NeXt state only)"""

    bl_idname = "clothnext.remove_physics"
    bl_label = "Remove Cloth NeXt"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if shared_controller.snapshot().active:
            return False
        obj = _active_mesh(context)
        if obj is None:
            return False
        settings = getattr(obj, "cloth_next", None)
        return settings is not None and settings.enabled

    def execute(self, context):
        obj = context.active_object
        object_properties.reset_settings(obj.cloth_next)
        self.report({"INFO"}, f"Cloth NeXt removed from '{obj.name}'.")
        return {"FINISHED"}


class CLOTHNEXT_OT_use_scene_range(bpy.types.Operator):
    bl_idname = "clothnext.use_scene_range"
    bl_label = "Use Scene Range"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        return (obj is not None and getattr(obj, "cloth_next", None) is not None
                and obj.cloth_next.enabled
                and not shared_controller.snapshot().active)

    def execute(self, context):
        settings = context.active_object.cloth_next
        settings.bake_start = int(context.scene.frame_start)
        settings.bake_end = int(context.scene.frame_end)
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_add_physics, CLOTHNEXT_OT_remove_physics,
           CLOTHNEXT_OT_use_scene_range)
