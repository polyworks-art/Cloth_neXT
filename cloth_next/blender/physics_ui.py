# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Physics Properties integration for Cloth NeXt (Phase 2.8A).

The "Cloth NeXt" entry is appended to Blender's own ``PHYSICS_PT_add`` panel
through the stable ``Panel.append``/``Panel.remove`` API. Blender does not
expose the internal two-column grid of the native Add-Physics buttons to
appended callbacks, so the entry renders as a full-width button directly
below the native buttons — the closest placement the public UI API supports
(see docs/LIMITATIONS.md). No Blender source class is monkey-patched and no
third-party add-on internals are touched. Cloth NeXt deliberately has no
N-panel; the Physics Properties tab is the primary workflow.
"""

from __future__ import annotations

import bpy

from . import physics_operators

_add_entry_appended = False


def _draw_add_physics_entry(panel, context) -> None:
    """Appended to PHYSICS_PT_add; draws the Cloth NeXt add/remove entry."""
    obj = getattr(context, "object", None)
    if obj is None or obj.type != "MESH":
        return
    settings = getattr(obj, "cloth_next", None)
    col = panel.layout.column()
    if settings is not None and settings.enabled:
        col.operator(physics_operators.CLOTHNEXT_OT_remove_physics.bl_idname,
                     text="Cloth NeXt", icon="X")
    else:
        col.operator(physics_operators.CLOTHNEXT_OT_add_physics.bl_idname,
                     text="Cloth NeXt", icon="MOD_CLOTH")


# Marks the callback so stale copies from a previous module instance (e.g.
# after a script reload that skipped unregister) can be identified and purged.
_draw_add_physics_entry._clothnext_add_entry = True


def _purge_stale_add_entries() -> None:
    """Best-effort removal of callbacks left behind by a reloaded module."""
    draw = getattr(bpy.types.PHYSICS_PT_add, "draw", None)
    for func in list(getattr(draw, "_draw_funcs", ())):
        if (getattr(func, "_clothnext_add_entry", False)
                and func is not _draw_add_physics_entry):
            bpy.types.PHYSICS_PT_add.remove(func)


def append_add_physics_entry() -> None:
    """Append the draw callback exactly once."""
    global _add_entry_appended
    if _add_entry_appended:
        return
    _purge_stale_add_entries()
    bpy.types.PHYSICS_PT_add.append(_draw_add_physics_entry)
    _add_entry_appended = True


def remove_add_physics_entry() -> None:
    global _add_entry_appended
    if not _add_entry_appended:
        return
    bpy.types.PHYSICS_PT_add.remove(_draw_add_physics_entry)
    _add_entry_appended = False


class CLOTHNEXT_PT_physics(bpy.types.Panel):
    """Main Cloth NeXt panel in the Physics Properties tab."""

    bl_label = "Cloth NeXt"
    bl_idname = "CLOTHNEXT_PT_physics"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "physics"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        if obj is None or obj.type != "MESH":
            return False
        settings = getattr(obj, "cloth_next", None)
        return settings is not None and settings.enabled

    def draw(self, context):
        layout = self.layout
        settings = context.object.cloth_next
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(settings, "role")
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Setup", icon="INFO")
        col.label(text="Cloth NeXt enabled")
        col.label(text="Solver not checked yet")
        layout.label(text="Simulation controls arrive in the next "
                          "Phase 2.8 step.")
        layout.operator(physics_operators.CLOTHNEXT_OT_remove_physics.bl_idname,
                        text="Remove Cloth NeXt", icon="X")


CLASSES = (CLOTHNEXT_PT_physics,)
