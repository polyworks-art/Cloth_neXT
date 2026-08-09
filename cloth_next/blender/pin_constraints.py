# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender-facing soft and hard Pin constraint controls.

The core pin model deliberately stays free of ``bpy``. This module injects the
RNA properties before ``CLOTHNEXT_PG_object_settings`` is registered, resolves
them while immutable snapshots are captured, and exposes a compact child panel
beneath the existing Pinning panel.
"""

from __future__ import annotations

import hashlib
import math

import bpy

from ..bake.controller import shared_controller
from ..pinning import PinConstraintType, set_pin_constraint_resolver
from . import object_properties, validation_state

_DEFAULT_PULL_STRENGTH = 1.0
_MIN_PULL_STRENGTH = 1e-6


def _on_pin_constraint_update(self, _context) -> None:
    owner = getattr(self, "id_data", None)
    if owner is not None:
        validation_state.mark_settings_dirty(owner)


def _install_property_definitions() -> None:
    """Add the two fields before Blender registers the PropertyGroup class."""
    annotations = object_properties.CLOTHNEXT_PG_object_settings.__annotations__
    annotations.setdefault(
        "pin_constraint_type",
        bpy.props.EnumProperty(
            name="Constraint Type",
            items=(
                ("SOFT", "Soft Pin",
                 "Pull vertices toward the Pin target while allowing contact "
                 "and cloth forces to make them yield"),
                ("HARD", "Hard Pin",
                 "Hold vertices exactly at the Pin target. Use only for truly "
                 "fixed anchors whose path cannot intersect a Collider"),
            ),
            default="SOFT",
            update=_on_pin_constraint_update,
            description="Choose whether the Pin can yield to the simulation"))
    annotations.setdefault(
        "pin_pull_strength",
        bpy.props.FloatProperty(
            name="Pull Strength",
            default=_DEFAULT_PULL_STRENGTH,
            min=_MIN_PULL_STRENGTH,
            soft_max=1000.0,
            precision=3,
            update=_on_pin_constraint_update,
            description="Soft Pin force strength. Higher values follow the "
                        "target more tightly; 1 is the solver default"))


_install_property_definitions()


def _source_object(source_object_id: str):
    obj = bpy.data.objects.get(source_object_id)
    if obj is not None:
        return obj
    objects = bpy.data.objects
    values = getattr(objects, "values", None)
    candidates = values() if callable(values) else objects
    for candidate in candidates:
        name = str(getattr(candidate, "name_full", candidate.name))
        if name == source_object_id:
            return candidate
    return None


def _resolve_pin_constraint(source_object_id: str, _group_name: str):
    obj = _source_object(source_object_id)
    settings = getattr(obj, "cloth_next", None) if obj is not None else None
    if settings is None:
        return None
    if (bool(getattr(settings, "advanced_pin_targets", ()))
            or bool(getattr(settings, "advanced_pin_motion_enabled", False))
            or bool(getattr(settings, "soft_constraints", ()) )):
        constraint_type = PinConstraintType.SOFT
    else:
        try:
            constraint_type = PinConstraintType(
                str(getattr(settings, "pin_constraint_type", "SOFT")))
        except ValueError:
            constraint_type = PinConstraintType.SOFT
    strength = float(getattr(
        settings, "pin_pull_strength", _DEFAULT_PULL_STRENGTH))
    rows = tuple(getattr(settings, "soft_constraints", ()))
    if rows:
        strength = sum(max(0.0, float(getattr(row, "strength", 0.0)))
                       for row in rows)
        if strength <= 0.0:
            strength = _DEFAULT_PULL_STRENGTH
    if not math.isfinite(strength) or strength <= 0.0:
        strength = _DEFAULT_PULL_STRENGTH
    if constraint_type is PinConstraintType.HARD:
        strength = 0.0
    return constraint_type, strength


def _constraint_record(settings) -> str:
    kind = str(getattr(settings, "pin_constraint_type", "SOFT"))
    if kind == PinConstraintType.HARD.value:
        strength = 0.0
    else:
        strength = float(getattr(
            settings, "pin_pull_strength", _DEFAULT_PULL_STRENGTH))
    return f"{kind}\0{strength:.17g}"


_original_cheap_pinning_fingerprint = None
_original_parameter_inspection = None


def _patch_solver_test() -> None:
    """Extend cache diagnostics without duplicating the bake implementation."""
    global _original_cheap_pinning_fingerprint, _original_parameter_inspection
    from . import solver_test

    current = solver_test._cheap_pinning_fingerprint
    if not getattr(current, "_clothnext_pin_constraint_patch", False):
        _original_cheap_pinning_fingerprint = current

        def cheap_pinning_fingerprint(cloth_obj):
            base = current(cloth_obj)
            record = base + "\0" + _constraint_record(cloth_obj.cloth_next)
            return hashlib.sha256(record.encode("utf-8")).hexdigest()

        cheap_pinning_fingerprint._clothnext_pin_constraint_patch = True
        solver_test._cheap_pinning_fingerprint = cheap_pinning_fingerprint

    current_inspection = solver_test.build_parameter_inspection
    if not getattr(current_inspection, "_clothnext_pin_constraint_patch", False):
        _original_parameter_inspection = current_inspection

        def build_parameter_inspection(context):
            lines, payload = current_inspection(context)
            settings = context.object.cloth_next
            kind = str(getattr(settings, "pin_constraint_type", "SOFT"))
            if kind == PinConstraintType.HARD.value:
                replacement = "Constraint: Hard Pin (exact fix)"
            else:
                strength = float(getattr(
                    settings, "pin_pull_strength", _DEFAULT_PULL_STRENGTH))
                replacement = f"Constraint: Soft Pin (strength {strength:g})"
            lines = tuple(replacement if line == "Pull: Disabled" else line
                          for line in lines)
            return lines, payload

        build_parameter_inspection._clothnext_pin_constraint_patch = True
        solver_test.build_parameter_inspection = build_parameter_inspection


def _unpatch_solver_test() -> None:
    global _original_cheap_pinning_fingerprint, _original_parameter_inspection
    from . import solver_test

    if (getattr(solver_test._cheap_pinning_fingerprint,
                "_clothnext_pin_constraint_patch", False)
            and _original_cheap_pinning_fingerprint is not None):
        solver_test._cheap_pinning_fingerprint = \
            _original_cheap_pinning_fingerprint
    if (getattr(solver_test.build_parameter_inspection,
                "_clothnext_pin_constraint_patch", False)
            and _original_parameter_inspection is not None):
        solver_test.build_parameter_inspection = _original_parameter_inspection
    _original_cheap_pinning_fingerprint = None
    _original_parameter_inspection = None


def install_runtime_hooks() -> None:
    set_pin_constraint_resolver(_resolve_pin_constraint)
    _patch_solver_test()


def uninstall_runtime_hooks() -> None:
    set_pin_constraint_resolver(None)
    _unpatch_solver_test()


class CLOTHNEXT_PT_pin_constraint(bpy.types.Panel):
    """Choose the physical behavior of the selected Pin Group."""

    bl_label = "Constraint"
    bl_idname = "CLOTHNEXT_PT_pin_constraint"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "physics"
    bl_parent_id = "CLOTHNEXT_PT_pinning"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        settings = getattr(obj, "cloth_next", None)
        return bool(settings is not None and settings.enabled
                    and settings.role == "CLOTH"
                    and settings.pinning_enabled)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = context.object.cloth_next

        controls = layout.column(align=True)
        controls.enabled = not shared_controller.snapshot().active
        target_mode = (bool(settings.advanced_pin_targets)
                       or bool(settings.advanced_pin_motion_enabled)
                       or bool(settings.soft_constraints))
        kind = controls.row()
        kind.enabled = not target_mode
        kind.prop(settings, "pin_constraint_type", text="Type")
        strength = controls.row()
        strength.enabled = target_mode or settings.pin_constraint_type == "SOFT"
        strength.prop(settings, "pin_pull_strength", text="Strength")

        if target_mode:
            layout.label(text="Target Object always uses collision-safe Soft Pins",
                         icon="CHECKMARK")
        elif settings.pin_constraint_type == "SOFT":
            layout.label(text="Recommended for clothing and animated rigs",
                         icon="CHECKMARK")
            layout.label(text="Can yield when the target meets a Collider")
        else:
            warning = layout.box()
            warning.alert = True
            warning.label(text="Exact, non-yielding constraint", icon="ERROR")
            warning.label(text="A target crossing a Collider can make")
            warning.label(text="the solve infeasible")


CLASSES = (CLOTHNEXT_PT_pin_constraint,)
