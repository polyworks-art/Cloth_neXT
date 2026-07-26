# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Operators that enable and disable Cloth NeXt on an object (Phase 2.8A).

Enabling Cloth NeXt is pure property state: no native Cloth modifier is
created, no solver is started, and no network or filesystem work happens.
The solver does not need to be installed to set up an object.
"""

from __future__ import annotations

import bpy

from .. import export_identity
from . import object_properties
from ..bake.controller import shared_controller
from ..solver_quality import (
    PDRD_QUALITY_PRESETS,
    QUALITY_PRESETS,
    SolverQualitySettings,
    SolverQualityValidationError,
    apply_quality_preset,
    matching_quality_preset,
    remap_quality_for_pdrd,
)
from ..materials import presets as material_presets


def _active_mesh(context):
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type not in {"MESH", "CURVE", "EMPTY"}:
        return None
    return obj


def scene_has_pdrd(scene) -> bool:
    """Whether an enabled PDRD/Rigid Body participates in this scene."""
    return bool(scene is not None and any(
        getattr(getattr(obj, "cloth_next", None), "enabled", False)
        and obj.cloth_next.role == "RIGID_BODY"
        for obj in scene.objects))


_scene_has_pdrd = scene_has_pdrd


def _write_solver_quality(scene, values: SolverQualitySettings) -> None:
    quality = scene.cloth_next_quality
    quality.time_step = values.time_step
    quality.min_newton_steps = values.min_newton_steps
    quality.cg_max_iter = values.cg_max_iter
    quality.cg_tol = values.cg_tol


def _remap_quality_after_pdrd_change(
        scene, *, previous_has_pdrd: bool) -> None:
    """Keep a selected preset while switching its standard/PDRD values.

    Manually tuned (Custom) quality values are deliberately left untouched.
    """
    has_pdrd = _scene_has_pdrd(scene)
    if scene is None or has_pdrd == previous_has_pdrd:
        return
    try:
        current = object_properties.solver_quality_from(scene)
        remapped = remap_quality_for_pdrd(
            current,
            from_has_pdrd=previous_has_pdrd,
            to_has_pdrd=has_pdrd,
        )
    except SolverQualityValidationError:
        return
    if remapped != current:
        _write_solver_quality(scene, remapped)


def synchronize_scene_quality(scene) -> bool:
    """Align a recognized preset with the scene's current PDRD state.

    This also migrates already-authored PDRD scenes when Cloth NeXt 2.1.3 is
    registered. Custom numeric values stay untouched.
    """
    if scene is None or getattr(scene, "cloth_next_quality", None) is None:
        return False
    has_pdrd = _scene_has_pdrd(scene)
    try:
        current = object_properties.solver_quality_from(scene)
        active = matching_quality_preset(current, has_pdrd=has_pdrd)
        if active is not None:
            if has_pdrd and active.identifier == "LOW":
                _write_solver_quality(
                    scene, apply_quality_preset("MEDIUM", has_pdrd=True))
                return True
            return False
        remapped = remap_quality_for_pdrd(
            current,
            from_has_pdrd=not has_pdrd,
            to_has_pdrd=has_pdrd,
        )
    except SolverQualityValidationError:
        return False
    if remapped == current:
        return False
    _write_solver_quality(scene, remapped)
    return True


def synchronize_all_scene_quality() -> None:
    """Migrate every loaded scene after the Blender properties are attached."""
    for scene in getattr(bpy.data, "scenes", ()):
        synchronize_scene_quality(scene)


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
        export_identity.ensure_persistent_id(obj)
        if self.role == "FORCE" and obj.type != "EMPTY":
            self.report({"WARNING"}, "Force requires an Empty object.")
            return {"CANCELLED"}
        if self.role == "ROD" and obj.type != "CURVE":
            self.report({"WARNING"},
                        "Cable / Rope requires a Curve object.")
            return {"CANCELLED"}
        if self.role not in {"ROD", "FORCE"} and obj.type != "MESH":
            self.report({"WARNING"},
                        "Cloth, Soft Body and Collider require a Mesh object.")
            return {"CANCELLED"}
        scene = getattr(context, "scene", None)
        previous_has_pdrd = _scene_has_pdrd(scene)
        obj.cloth_next.role = self.role
        _remap_quality_after_pdrd_change(
            scene, previous_has_pdrd=previous_has_pdrd)
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
        export_identity.ensure_persistent_id(obj)
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
        scene = getattr(context, "scene", None)
        previous_has_pdrd = _scene_has_pdrd(scene)
        object_properties.reset_settings(obj.cloth_next)
        _remap_quality_after_pdrd_change(
            scene, previous_has_pdrd=previous_has_pdrd)
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
        start = int(context.scene.frame_start)
        end = int(context.scene.frame_end)
        dynamic_roles = {"CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY"}
        changed = 0
        for obj in context.scene.objects:
            settings = getattr(obj, "cloth_next", None)
            if (settings is None or not settings.enabled
                    or settings.role not in dynamic_roles):
                continue
            settings.bake_start = start
            settings.bake_end = end
            changed += 1
        self.report({"INFO"},
                    f"Scene range applied to {changed} simulated object(s).")
        return {"FINISHED"}


class _ApplySolverQualityPresetMixin:
    """Shared implementation for independently registered Quality buttons."""

    quality_preset = ""

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
        has_pdrd = _scene_has_pdrd(context.scene)
        identifier = self.quality_preset or self.preset
        if has_pdrd and identifier.upper() == "LOW":
            self.report(
                {"WARNING"},
                "Low is unavailable with Rigid Body; use XMedium or higher.")
            return {"CANCELLED"}
        try:
            values = apply_quality_preset(
                identifier, has_pdrd=has_pdrd)
        except SolverQualityValidationError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _write_solver_quality(context.scene, values)
        if has_pdrd:
            self.report(
                {"INFO"},
                f"{identifier.title()} uses PDRD-safe solver settings.")
        return {"FINISHED"}


class CLOTHNEXT_OT_apply_solver_quality_preset(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    """Apply one scene-wide solver quality preset."""

    bl_idname = "clothnext.apply_solver_quality_preset"
    bl_label = "Apply Solver Quality Preset"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    preset: bpy.props.StringProperty(options={"HIDDEN"})


class CLOTHNEXT_OT_apply_quality_low(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    bl_idname = "clothnext.apply_quality_low"
    bl_label = "Low Quality"
    bl_description = QUALITY_PRESETS[0].description
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    quality_preset = "LOW"


class CLOTHNEXT_OT_apply_quality_medium(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    bl_idname = "clothnext.apply_quality_medium"
    bl_label = "Medium Quality"
    bl_description = QUALITY_PRESETS[1].description
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    quality_preset = "MEDIUM"


class CLOTHNEXT_OT_apply_quality_high(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    bl_idname = "clothnext.apply_quality_high"
    bl_label = "High Quality"
    bl_description = QUALITY_PRESETS[2].description
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    quality_preset = "HIGH"


class CLOTHNEXT_OT_apply_quality_extreme(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    bl_idname = "clothnext.apply_quality_extreme"
    bl_label = "Extreme Quality"
    bl_description = (
        f"{QUALITY_PRESETS[3].description} {QUALITY_PRESETS[3].warning}")
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    quality_preset = "EXTREME"


class CLOTHNEXT_OT_apply_quality_xmedium(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    bl_idname = "clothnext.apply_quality_xmedium"
    bl_label = "XMedium Quality"
    bl_description = PDRD_QUALITY_PRESETS[1].description
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    quality_preset = "MEDIUM"


class CLOTHNEXT_OT_apply_quality_xhigh(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    bl_idname = "clothnext.apply_quality_xhigh"
    bl_label = "XHigh Quality"
    bl_description = PDRD_QUALITY_PRESETS[2].description
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    quality_preset = "HIGH"


class CLOTHNEXT_OT_apply_quality_xextreme(
        _ApplySolverQualityPresetMixin, bpy.types.Operator):
    bl_idname = "clothnext.apply_quality_xextreme"
    bl_label = "XExtreme Quality"
    bl_description = (
        f"{PDRD_QUALITY_PRESETS[3].description} "
        f"{PDRD_QUALITY_PRESETS[3].warning}")
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    quality_preset = "EXTREME"


QUALITY_PRESET_OPERATOR_IDS = {
    "LOW": CLOTHNEXT_OT_apply_quality_low.bl_idname,
    "MEDIUM": CLOTHNEXT_OT_apply_quality_medium.bl_idname,
    "HIGH": CLOTHNEXT_OT_apply_quality_high.bl_idname,
    "EXTREME": CLOTHNEXT_OT_apply_quality_extreme.bl_idname,
}

PDRD_QUALITY_PRESET_OPERATOR_IDS = {
    "MEDIUM": CLOTHNEXT_OT_apply_quality_xmedium.bl_idname,
    "HIGH": CLOTHNEXT_OT_apply_quality_xhigh.bl_idname,
    "EXTREME": CLOTHNEXT_OT_apply_quality_xextreme.bl_idname,
}


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
           CLOTHNEXT_OT_apply_quality_low,
           CLOTHNEXT_OT_apply_quality_medium,
           CLOTHNEXT_OT_apply_quality_high,
           CLOTHNEXT_OT_apply_quality_extreme,
           CLOTHNEXT_OT_apply_quality_xmedium,
           CLOTHNEXT_OT_apply_quality_xhigh,
           CLOTHNEXT_OT_apply_quality_xextreme,
           CLOTHNEXT_OT_apply_material_preset)
