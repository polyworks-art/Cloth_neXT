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


class CLOTHNEXT_PG_object_settings(bpy.types.PropertyGroup):
    """Phase 2.8 object-level Cloth NeXt settings."""

    enabled: bpy.props.BoolProperty(
        name="Enabled", default=False,
        description="Cloth NeXt is enabled on this object")
    role: bpy.props.EnumProperty(
        name="Object Role", items=ROLE_ITEMS, default=DEFAULT_ROLE,
        description="How Cloth NeXt treats this object in a simulation")


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


CLASSES = (CLOTHNEXT_PG_object_settings,)
