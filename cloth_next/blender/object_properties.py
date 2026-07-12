# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persistent per-object Cloth NeXt state (Phase 2.8A).

Only plain, serializable Blender properties live here. Runtime solver
handles, threads, sockets, and process objects are never stored in Blender
properties; those belong to session-scoped Python state elsewhere.
"""

from __future__ import annotations

import bpy

ROLE_ITEMS = (
    ("CLOTH", "Cloth", "Simulate this object as cloth"),
    ("COLLIDER", "Collider", "Use this object as a collision obstacle"),
)

DEFAULT_ROLE = "CLOTH"


class CLOTHNEXT_PG_quality_settings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(name="Preset", items=(("PREVIEW", "Preview", ""), ("STANDARD", "Standard", ""), ("HIGH", "High", ""), ("CUSTOM", "Custom", "")), default="STANDARD")
    substeps: bpy.props.IntProperty(name="Substeps", default=4, min=1, max=100)
    solver_iterations: bpy.props.IntProperty(name="Solver Iterations", default=20, min=1, max=1000)
    contact_iterations: bpy.props.IntProperty(name="Contact Iterations", default=10, min=1, max=1000)


class CLOTHNEXT_PG_physical_settings(bpy.types.PropertyGroup):
    mass_mode: bpy.props.EnumProperty(name="Mass", items=(("DENSITY", "Surface Density", ""), ("TOTAL", "Total Mass", "")), default="DENSITY")
    surface_density: bpy.props.FloatProperty(name="Surface Density", default=0.3, min=0.001, soft_max=10.0, unit="MASS")
    thickness: bpy.props.FloatProperty(name="Thickness", default=0.001, min=0.0, soft_max=0.1, unit="LENGTH", precision=4)
    stretch_stiffness: bpy.props.FloatProperty(name="Stretch", default=1000.0, min=0.0)
    shear_stiffness: bpy.props.FloatProperty(name="Shear", default=500.0, min=0.0)
    bend_stiffness: bpy.props.FloatProperty(name="Bend", default=1.0, min=0.0)


class CLOTHNEXT_PG_damping_settings(bpy.types.PropertyGroup):
    stretch: bpy.props.FloatProperty(name="Stretch", default=0.1, min=0.0, max=1.0)
    shear: bpy.props.FloatProperty(name="Shear", default=0.1, min=0.0, max=1.0)
    bend: bpy.props.FloatProperty(name="Bend", default=0.1, min=0.0, max=1.0)
    velocity: bpy.props.FloatProperty(name="Velocity", default=0.01, min=0.0, max=1.0)


class CLOTHNEXT_PG_collision_settings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(name="Collision", default=True)
    self_collision: bpy.props.BoolProperty(name="Self Collision", default=False)
    distance: bpy.props.FloatProperty(name="Distance", default=0.005, min=0.0, unit="LENGTH")
    self_distance: bpy.props.FloatProperty(name="Self Distance", default=0.005, min=0.0, unit="LENGTH")
    friction: bpy.props.FloatProperty(name="Friction", default=0.3, min=0.0, max=1.0)


class CLOTHNEXT_PG_pressure_settings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(name="Pressure", default=False)
    target: bpy.props.FloatProperty(name="Target Pressure", default=1.0, min=0.0)
    stiffness: bpy.props.FloatProperty(name="Stiffness", default=1.0, min=0.0)
    volume_conservation: bpy.props.FloatProperty(name="Volume Conservation", default=1.0, min=0.0, max=1.0)


class CLOTHNEXT_PG_shape_settings(bpy.types.PropertyGroup):
    pin_group: bpy.props.StringProperty(name="Pin Group", default="")
    pin_stiffness: bpy.props.FloatProperty(name="Pin Stiffness", default=1.0, min=0.0, max=1.0)
    use_rest_shape: bpy.props.BoolProperty(name="Use Rest Shape", default=False)
    rest_shape_source: bpy.props.StringProperty(name="Rest Shape Source", default="")
    rest_scale: bpy.props.FloatProperty(name="Rest Scale", default=1.0, min=0.01, max=2.0)


class CLOTHNEXT_PG_cache_settings(bpy.types.PropertyGroup):
    frame_start: bpy.props.IntProperty(name="Start", default=1)
    frame_end: bpy.props.IntProperty(name="End", default=250)
    directory: bpy.props.StringProperty(name="Directory", default="//cloth_next_cache", subtype="DIR_PATH")


class CLOTHNEXT_PG_object_settings(bpy.types.PropertyGroup):
    """Phase 2.8 object-level Cloth NeXt settings."""

    enabled: bpy.props.BoolProperty(
        name="Enabled", default=False,
        description="Cloth NeXt is enabled on this object")
    role: bpy.props.EnumProperty(
        name="Object Role", items=ROLE_ITEMS, default=DEFAULT_ROLE,
        description="How Cloth NeXt treats this object in a simulation")
    quality: bpy.props.PointerProperty(type=CLOTHNEXT_PG_quality_settings)
    physical: bpy.props.PointerProperty(type=CLOTHNEXT_PG_physical_settings)
    damping: bpy.props.PointerProperty(type=CLOTHNEXT_PG_damping_settings)
    collision: bpy.props.PointerProperty(type=CLOTHNEXT_PG_collision_settings)
    pressure: bpy.props.PointerProperty(type=CLOTHNEXT_PG_pressure_settings)
    shape: bpy.props.PointerProperty(type=CLOTHNEXT_PG_shape_settings)
    cache: bpy.props.PointerProperty(type=CLOTHNEXT_PG_cache_settings)


def reset_settings(settings) -> None:
    """Reset all Phase 2.8 object-level settings to safe defaults.

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


CLASSES = (CLOTHNEXT_PG_quality_settings, CLOTHNEXT_PG_physical_settings,
           CLOTHNEXT_PG_damping_settings, CLOTHNEXT_PG_collision_settings,
           CLOTHNEXT_PG_pressure_settings, CLOTHNEXT_PG_shape_settings,
           CLOTHNEXT_PG_cache_settings, CLOTHNEXT_PG_object_settings)
