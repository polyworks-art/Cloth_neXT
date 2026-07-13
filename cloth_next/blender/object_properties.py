# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persistent per-object Cloth NeXt state (Phase 3B).

Only plain, serializable Blender properties live here. Runtime solver
handles, threads, sockets, and process objects are never stored in Blender
properties; those belong to session-scoped Python state elsewhere.

Phase-3B material model: every displayed property is really sent to the
PPF solver. The old Phase-2.8 placeholder groups (Quality, the
Stretch/Shear/Thickness "Physical Properties", per-mode damping, self
collision, Pressure, Shape, and the editable Cache range) were never read
by any solver and are gone; their values remain as orphaned ID properties
in old .blend files and are deliberately never reinterpreted as physical
PPF values. Existing Cloth NeXt-enabled objects therefore start from the
DEFAULT CLOTH preset while keeping their enabled state and Cloth/Collider
role (see docs/PPF_PARAMETER_MAPPING.md, "Migration").
"""

from __future__ import annotations

import bpy

from ..materials import (MODEL_FABRIC, MODEL_SHAPE_PRESERVING,
                         ShellMaterialSettings, StaticMaterialSettings)
from ..materials import presets as material_presets

ROLE_ITEMS = (
    ("CLOTH", "Cloth", "Simulate this object as cloth"),
    ("COLLIDER", "Collider", "Use this object as a collision obstacle"),
)

DEFAULT_ROLE = "CLOTH"

# ---------------------------------------------------------------------------
# Preset plumbing.
#
# The bundled TOML is parsed exactly once at import (registration) time;
# Panel.draw never touches the file. If the bundle is unusable the enum
# degrades to Custom only, the Material panel shows the load error, and no
# preset can partially apply.

def _build_preset_items() -> tuple[tuple[str, str, str], ...]:
    items: list[tuple[str, str, str]] = []
    try:
        for preset in material_presets.builtin_presets():
            items.append((preset.identifier, preset.label,
                          preset.description))
    except material_presets.PresetError:
        pass
    items.append((material_presets.PRESET_CUSTOM,
                  material_presets.CUSTOM_LABEL,
                  material_presets.CUSTOM_DESCRIPTION))
    return tuple(items)


# Kept alive for the whole session: Blender requires enum item strings to
# stay referenced, and a static tuple guarantees no draw-time file access.
PRESET_ITEMS = _build_preset_items()
_PRESET_IDENTIFIERS = tuple(item[0] for item in PRESET_ITEMS)
DEFAULT_PRESET = (material_presets.DEFAULT_PRESET_ID
                  if material_presets.DEFAULT_PRESET_ID
                  in _PRESET_IDENTIFIERS
                  else material_presets.PRESET_CUSTOM)

# Reentrancy guard: while a preset is being applied, the per-property
# update callbacks must not flip the selection back to Custom.
_applying_preset = False


def _object_settings_of(property_group):
    """The owning CLOTHNEXT_PG_object_settings for a nested group."""
    owner = getattr(property_group, "id_data", None)
    return getattr(owner, "cloth_next", None)


def apply_preset(settings, identifier: str) -> bool:
    """Deterministically copy one bundled preset onto the property groups.

    Main-thread only (property writes). Returns False — and changes
    nothing — when the preset does not exist or the bundle failed to load.
    """
    preset = material_presets.preset_by_identifier(identifier)
    if preset is None:
        return False
    shell = preset.settings
    global _applying_preset
    _applying_preset = True
    try:
        material = settings.material
        material.model = shell.model
        material.surface_weight = shell.surface_weight
        material.stretch_resistance = shell.stretch_resistance
        material.sideways_response = shell.sideways_response
        material.bend_resistance = shell.bend_resistance
        material.stretch_limit_enabled = shell.stretch_limit_enabled
        material.maximum_stretch_percent = shell.maximum_stretch_percent
        settings.damping.shape_damping = shell.shape_damping
        settings.damping.fold_damping = shell.fold_damping
        settings.collision.surface_grip = shell.surface_grip
        settings.collision.collision_gap = shell.collision_gap
        settings.collision.surface_offset = shell.surface_offset
    finally:
        _applying_preset = False
    return True


def mark_custom(settings) -> None:
    """Switch the visible preset to Custom without touching any value."""
    if _applying_preset or settings is None:
        return
    material = settings.material
    if material.preset != material_presets.PRESET_CUSTOM:
        material.preset = material_presets.PRESET_CUSTOM


def _on_preset_update(self, _context) -> None:
    if _applying_preset:
        return
    if self.preset == material_presets.PRESET_CUSTOM:
        return  # selecting Custom never alters the current values
    settings = _object_settings_of(self)
    if settings is not None:
        apply_preset(settings, self.preset)


def _on_material_value_update(self, _context) -> None:
    """Any manual edit of a preset-controlled value selects Custom."""
    mark_custom(_object_settings_of(self))


class CLOTHNEXT_PG_material_settings(bpy.types.PropertyGroup):
    """Cloth material — every field maps to a real PPF shell parameter."""

    preset: bpy.props.EnumProperty(
        name="Material Preset", items=PRESET_ITEMS, default=DEFAULT_PRESET,
        update=_on_preset_update,
        description="Calibrated PPF fabric presets bundled with Cloth NeXt; "
                    "editing any mapped value switches to Custom without "
                    "resetting anything")
    model: bpy.props.EnumProperty(
        name="Solver Model",
        items=((MODEL_FABRIC, "Fabric (Baraff-Witkin)",
                "Calibrated model used by the bundled PPF fabric presets"),
               (MODEL_SHAPE_PRESERVING, "Shape Preserving (ARAP)",
                "Advanced shape-preserving alternative")),
        default=MODEL_FABRIC, update=_on_material_value_update,
        description="Selects the mathematical shell material model. Fabric "
                    "(Baraff-Witkin) is the calibrated model used by the "
                    "bundled PPF fabric presets. Shape Preserving (ARAP) is "
                    "an advanced alternative. Technical PPF parameter: "
                    "model")
    surface_weight: bpy.props.FloatProperty(
        name="Surface Weight", default=1.0, min=0.01, soft_max=10.0,
        max=10000.0, precision=3, update=_on_material_value_update,
        description="Mass of the fabric per square meter. Higher values "
                    "give the cloth more inertia and make it react more "
                    "heavily, but do not directly make it stiffer. "
                    "Unit: kg/m². Technical PPF parameter: density")
    stretch_resistance: bpy.props.FloatProperty(
        name="Stretch Resistance", default=1000.0, min=0.0,
        soft_max=100000.0, max=1e9, precision=1,
        update=_on_material_value_update,
        description="Controls how strongly the fabric resists being pulled "
                    "longer. Lower values create softer, more stretchable "
                    "cloth. Higher values preserve its original size more "
                    "strongly. Sent directly as PPF's density-normalized "
                    "young-mod value")
    sideways_response: bpy.props.FloatProperty(
        name="Sideways Response", default=0.35, min=0.0, max=0.4999,
        precision=4, update=_on_material_value_update,
        description="Controls how strongly stretching in one direction "
                    "affects the fabric sideways. Lower values allow the "
                    "directions to stretch more independently. Higher "
                    "values make the fabric contract sideways more "
                    "strongly. Technical PPF parameter: poiss-rat")
    bend_resistance: bpy.props.FloatProperty(
        name="Bend Resistance", default=10.0, min=0.0, soft_max=100.0,
        precision=2, update=_on_material_value_update,
        description="Controls how easily the fabric bends and forms folds. "
                    "Lower values create soft, flowing folds. Higher "
                    "values create broader, stiffer folds and stronger "
                    "shape retention. Technical PPF parameter: bend")
    stretch_limit_enabled: bpy.props.BoolProperty(
        name="Stretch Limit", default=False,
        update=_on_material_value_update,
        description="Prevents the fabric from stretching beyond the "
                    "specified percentage. When disabled, Cloth NeXt sends "
                    "a strain-limit value of zero to PPF")
    maximum_stretch_percent: bpy.props.FloatProperty(
        name="Maximum Stretch", default=5.0, min=0.01, soft_max=20.0,
        max=100.0, precision=2, subtype="PERCENTAGE",
        update=_on_material_value_update,
        description="Maximum permitted extension beyond the original size. "
                    "A value of 5% allows approximately five percent "
                    "stretch. Converted to PPF's fractional strain-limit "
                    "value")


class CLOTHNEXT_PG_damping_settings(bpy.types.PropertyGroup):
    """Both values are stiffness-proportional Rayleigh damping (seconds)."""

    shape_damping: bpy.props.FloatProperty(
        name="Shape Damping", default=0.0, min=0.0, soft_max=0.1,
        precision=4, update=_on_material_value_update,
        description="Reduces oscillation caused by stretching and in-plane "
                    "deformation. Small values can calm jitter without "
                    "making the fabric visibly sluggish. Unit: seconds. "
                    "Technical PPF parameter: deformation-damping")
    fold_damping: bpy.props.FloatProperty(
        name="Fold Damping", default=0.0, min=0.0, soft_max=0.1,
        precision=4, update=_on_material_value_update,
        description="Reduces oscillation and flutter in folds and bending "
                    "motion. Small values can calm unstable folds. Unit: "
                    "seconds. Technical PPF parameter: bending-damping")


class CLOTHNEXT_PG_collision_settings(bpy.types.PropertyGroup):
    """Contact values; on a Collider these are the STATIC group values."""

    enabled: bpy.props.BoolProperty(
        name="Enable Contact", default=True,
        description="Turns solver contact handling on or off for the whole "
                    "run. When disabled, PPF receives disable-contact and "
                    "the cloth falls through every obstacle. Technical PPF "
                    "parameter: scene disable-contact")
    surface_grip: bpy.props.FloatProperty(
        name="Surface Grip", default=0.5, min=0.0, max=1.0, precision=2,
        update=_on_material_value_update,
        description="Controls sliding friction at contacts. Lower values "
                    "slide more easily. Higher values grip more strongly. "
                    "Cloth NeXt currently uses PPF's Minimum friction "
                    "combination mode, so both touching surfaces need "
                    "sufficiently high values for a grippy result. "
                    "Technical PPF parameter: friction")
    collision_gap: bpy.props.FloatProperty(
        name="Collision Gap", default=0.001, min=0.0, soft_max=0.01,
        precision=4, update=_on_material_value_update,
        description="Distance before PPF's contact barrier begins "
                    "reacting. Larger values keep surfaces farther apart. "
                    "Excessive values can make the cloth appear to float. "
                    "Unit: Blender world units. Technical PPF parameter: "
                    "contact-gap")
    surface_offset: bpy.props.FloatProperty(
        name="Surface Offset", default=0.0, min=0.0, soft_max=0.03,
        precision=4, update=_on_material_value_update,
        description="Adds a collision skin around the surface. Use small "
                    "values to represent surface thickness without changing "
                    "the simulated mesh. Excessive values create visible "
                    "separation. Unit: Blender world units. Technical PPF "
                    "parameter: contact-offset")


class CLOTHNEXT_PG_object_settings(bpy.types.PropertyGroup):
    """Phase 3B object-level Cloth NeXt settings."""

    enabled: bpy.props.BoolProperty(
        name="Enabled", default=False,
        description="Cloth NeXt is enabled on this object")
    role: bpy.props.EnumProperty(
        name="Object Role", items=ROLE_ITEMS, default=DEFAULT_ROLE,
        description="How Cloth NeXt treats this object in a simulation")
    material: bpy.props.PointerProperty(type=CLOTHNEXT_PG_material_settings)
    damping: bpy.props.PointerProperty(type=CLOTHNEXT_PG_damping_settings)
    collision: bpy.props.PointerProperty(type=CLOTHNEXT_PG_collision_settings)
    baked_settings_fingerprint: bpy.props.StringProperty(
        name="Baked Settings Fingerprint", default="", options={"HIDDEN"},
        description="Material fingerprint of the last completed solver "
                    "result; a mismatch marks the cached result as stale")


# ---------------------------------------------------------------------------
# Blender-to-pure snapshot (main thread only; raises
# MaterialValidationError with property, value, range, and remedy).

def shell_settings_from(settings) -> ShellMaterialSettings:
    """Freeze the cloth object's properties into the pure material model."""
    material = settings.material
    damping = settings.damping
    collision = settings.collision
    return ShellMaterialSettings(
        model=str(material.model),
        surface_weight=float(material.surface_weight),
        stretch_resistance=float(material.stretch_resistance),
        sideways_response=float(material.sideways_response),
        bend_resistance=float(material.bend_resistance),
        shape_damping=float(damping.shape_damping),
        fold_damping=float(damping.fold_damping),
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset),
        stretch_limit_enabled=bool(material.stretch_limit_enabled),
        maximum_stretch_percent=float(material.maximum_stretch_percent))


def static_settings_from(settings) -> StaticMaterialSettings:
    """Freeze the collider object's contact properties."""
    collision = settings.collision
    return StaticMaterialSettings(
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset))


def reset_settings(settings) -> None:
    """Reset object-level settings to safe defaults.

    Touches only Cloth NeXt state; never modifiers, vertex groups,
    materials, caches, or files.
    """
    settings.enabled = False
    settings.role = DEFAULT_ROLE


def attach_to_object() -> None:
    """Attach the settings to every object; requires the class registered."""
    bpy.types.Object.cloth_next = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_object_settings)


def detach_from_object() -> None:
    if hasattr(bpy.types.Object, "cloth_next"):
        del bpy.types.Object.cloth_next


CLASSES = (CLOTHNEXT_PG_material_settings, CLOTHNEXT_PG_damping_settings,
           CLOTHNEXT_PG_collision_settings, CLOTHNEXT_PG_object_settings)
