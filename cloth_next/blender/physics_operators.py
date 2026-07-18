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
from ..solver_quality import (SolverQualityValidationError,
                              apply_quality_preset)
from ..materials import presets as material_presets


def _active_mesh(context):
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type not in {"MESH", "CURVE", "EMPTY"}:
        return None
    return obj


class CLOTHNEXT_OT_set_object_type(bpy.types.Operator):
    """Choose a supported Cloth NeXt object type"""

    bl_idname = "clothnext.set_object_type"
    bl_label = "Set Cloth NeXt Object Type"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    role: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        settings = getattr(obj, "cloth_next", None) if obj else None
        return bool(settings is not None and settings.enabled
                    and not shared_controller.snapshot().active)

    def execute(self, context):
        # Keep this allow-list here as well as in the menu.  Invoking the
        # operator directly must never put a future type into stored state.
        if self.role not in {item[0] for item in object_properties.ROLE_ITEMS}:
            self.report({"WARNING"}, "This Cloth NeXt object type is not supported yet.")
            return {"CANCELLED"}
        obj = context.active_object
        if self.role == "FORCE" and obj.type != "EMPTY":
            self.report({"WARNING"}, "Force requires an Empty object.")
            return {"CANCELLED"}
        if self.role == "ROD" and obj.type != "CURVE":
            self.report({"WARNING"}, "Rod / Cable requires a Curve object.")
            return {"CANCELLED"}
        if self.role not in {"ROD", "FORCE"} and obj.type != "MESH":
            self.report({"WARNING"},
                        "Cloth, Soft Body and Collider require a Mesh object.")
            return {"CANCELLED"}
        obj.cloth_next.role = self.role
        return {"FINISHED"}


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
        settings.role = ("FORCE" if obj.type == "EMPTY"
                         else object_properties.DEFAULT_ROLE)
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


class CLOTHNEXT_OT_apply_solver_quality_preset(bpy.types.Operator):
    """Apply one scene-wide solver quality preset."""

    bl_idname = "clothnext.apply_solver_quality_preset"
    bl_label = "Apply Solver Quality Preset"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    preset: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return (getattr(getattr(context, "scene", None),
                        "cloth_next_quality", None) is not None
                and not shared_controller.snapshot().active)

    def execute(self, context):
        if shared_controller.snapshot().active:
            self.report({"WARNING"},
                        "Solver Quality cannot change during an active Bake.")
            return {"CANCELLED"}
        try:
            values = apply_quality_preset(self.preset)
        except SolverQualityValidationError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        quality = context.scene.cloth_next_quality
        quality.time_step = values.time_step
        quality.min_newton_steps = values.min_newton_steps
        quality.cg_max_iter = values.cg_max_iter
        quality.cg_tol = values.cg_tol
        return {"FINISHED"}


class CLOTHNEXT_OT_apply_material_preset(bpy.types.Operator):
    """Apply one bundled Cloth NeXt fabric preset."""

    bl_idname = "clothnext.apply_material_preset"
    bl_label = "Apply Material Preset"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    preset: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, _context, properties):
        preset = material_presets.preset_by_identifier(
            getattr(properties, "preset", ""))
        if preset is None:
            return "Apply this bundled fabric preset"
        detail = preset.description
        if preset.source_reference:
            detail += " · MIT laboratory dataset"
        return detail

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        settings = getattr(obj, "cloth_next", None) if obj else None
        return bool(settings is not None and settings.enabled
                    and settings.role == "CLOTH"
                    and not shared_controller.snapshot().active)

    def execute(self, context):
        settings = context.active_object.cloth_next
        if not object_properties.select_preset(settings, self.preset):
            self.report({"ERROR"}, "Material preset is unavailable.")
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_set_object_type,
           CLOTHNEXT_OT_add_physics, CLOTHNEXT_OT_remove_physics,
           CLOTHNEXT_OT_use_scene_range,
           CLOTHNEXT_OT_apply_solver_quality_preset,
           CLOTHNEXT_OT_apply_material_preset)
