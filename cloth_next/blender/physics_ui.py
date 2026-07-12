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

from . import icon_registry, physics_operators
from ..bake.controller import shared_controller

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
        snapshot = shared_controller.snapshot()
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Configured", icon="CHECKMARK")
        col.label(text="Solver status is available in Add-on Preferences")
        col.label(text=f"Bake UI: {snapshot.status_title}")
        if snapshot.preview:
            col.label(text="UI PREVIEW — no PPF simulation", icon="INFO")
        if snapshot.error_summary:
            col.label(text=snapshot.error_summary, icon="ERROR")
        layout.operator(physics_operators.CLOTHNEXT_OT_remove_physics.bl_idname,
                        text="Remove Cloth NeXt", icon="X")


class _ClothNextSubpanel:
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "physics"
    bl_parent_id = "CLOTHNEXT_PT_physics"

    @classmethod
    def poll(cls, context):
        if not CLOTHNEXT_PT_physics.poll(context):
            return False
        return not getattr(cls, "cloth_only", False) or context.object.cloth_next.role == "CLOTH"


def _mapped_note(layout):
    layout.label(text="Solver mapping will be enabled with the simulation pipeline.", icon="INFO")


class CLOTHNEXT_PT_overview(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Overview"; bl_idname = "CLOTHNEXT_PT_overview"
    def draw(self, context):
        s = context.object.cloth_next
        self.layout.label(text=f"{s.role.title()} · {context.object.name}")
        self.layout.label(text="Mesh setup ready for UI configuration")


class CLOTHNEXT_PT_solver(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Solver"; bl_idname = "CLOTHNEXT_PT_solver"
    def draw(self, _context):
        self.layout.label(text="Installation and compatibility are managed in Add-on Preferences.")


class CLOTHNEXT_PT_quality(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Quality"; bl_idname = "CLOTHNEXT_PT_quality"; cloth_only = True
    def draw(self, context):
        s=context.object.cloth_next.quality
        for name in ("preset", "substeps", "solver_iterations", "contact_iterations"): self.layout.prop(s, name)
        _mapped_note(self.layout)


class CLOTHNEXT_PT_physical(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Physical Properties"; bl_idname = "CLOTHNEXT_PT_physical"; cloth_only = True
    def draw(self, context):
        s=context.object.cloth_next.physical
        for name in ("mass_mode", "surface_density", "thickness", "stretch_stiffness", "shear_stiffness", "bend_stiffness"): self.layout.prop(s, name)
        _mapped_note(self.layout)


class CLOTHNEXT_PT_damping(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Damping"; bl_idname = "CLOTHNEXT_PT_damping"; cloth_only = True
    def draw(self, context):
        s=context.object.cloth_next.damping
        for name in ("stretch", "shear", "bend", "velocity"): self.layout.prop(s, name)


class CLOTHNEXT_PT_collisions(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Collisions"; bl_idname = "CLOTHNEXT_PT_collisions"
    def draw(self, context):
        s=context.object.cloth_next.collision
        names=("enabled", "distance", "friction")
        if context.object.cloth_next.role == "CLOTH": names=("enabled", "self_collision", "distance", "self_distance", "friction")
        for name in names: self.layout.prop(s, name)


class CLOTHNEXT_PT_pressure(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Pressure"; bl_idname = "CLOTHNEXT_PT_pressure"; cloth_only = True
    def draw(self, context):
        s=context.object.cloth_next.pressure
        for name in ("enabled", "target", "stiffness", "volume_conservation"): self.layout.prop(s, name)
        self.layout.label(text="Mesh closure validation is refreshed explicitly.", icon="INFO")


class CLOTHNEXT_PT_shape(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Shape"; bl_idname = "CLOTHNEXT_PT_shape"; cloth_only = True
    def draw(self, context):
        s=context.object.cloth_next.shape
        for name in ("pin_group", "pin_stiffness", "use_rest_shape", "rest_shape_source", "rest_scale"): self.layout.prop(s, name)


def _developer_tools_enabled(context) -> bool:
    addon_id = __package__.partition(".blender")[0]
    try:
        return bool(context.preferences.addons[addon_id]
                    .preferences.developer_tools)
    except (KeyError, AttributeError):
        return False


def _draw_solver_test_section(layout, context) -> None:
    """Phase-3A developer actions, clearly separated from the production
    Bake workflow (which does not exist yet)."""
    from . import solver_test
    box = layout.box()
    box.label(text="Developer: Real Solver Test (Phase 3A)", icon="EXPERIMENTAL")
    snapshot = shared_controller.snapshot()
    running = solver_test.run_active()
    box.operator("clothnext.create_test_scene", icon="MESH_GRID")
    run_row = box.row()
    run_row.enabled = not running and not snapshot.active
    run_row.operator("clothnext.solver_test_run",
                     **icon_registry.icon_kwargs("bake", "RENDER_ANIMATION"))
    if running or snapshot.active:
        cancel_row = box.row()
        cancel_row.enabled = snapshot.can_cancel
        cancel_row.operator("clothnext.solver_test_cancel", icon="CANCEL")
    column = box.column(align=True)
    column.label(text=f"State: {snapshot.status_title}")
    if snapshot.status_message:
        column.label(text=snapshot.status_message)
    if running or snapshot.active:
        from ..bake.status import format_duration
        plan = solver_test._active_plan
        if plan is not None:
            column.label(text=f"Solver: {plan.resolved.mode.name}")
            diag_host = "127.0.0.1 (owned process)"
            column.label(text=f"Server: {diag_host}")
        if snapshot.current_frame is not None and snapshot.frame_end:
            column.label(text=f"Frame {snapshot.current_frame} "
                              f"of {snapshot.frame_end}")
        column.label(text=f"Elapsed: {format_duration(snapshot.elapsed_seconds)}")
    if snapshot.error_summary:
        column.label(text=snapshot.error_summary, icon="ERROR")
    box.operator("clothnext.solver_test_clear", icon="TRASH")
    box.operator("clothnext.solver_test_open_logs", icon="FILE_FOLDER")


class CLOTHNEXT_PT_cache(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Cache"; bl_idname = "CLOTHNEXT_PT_cache"
    def draw(self, context):
        s=context.object.cloth_next.cache
        for name in ("frame_start", "frame_end", "directory"): self.layout.prop(s, name)
        self.layout.label(text="No simulation cache yet")
        self.layout.operator("clothnext.preview_start", text="Start UI Preview",
                             **icon_registry.icon_kwargs("bake", "RENDER_ANIMATION"))
        self.layout.operator("clothnext.companion_launch", text="Launch Bake Window", icon="WINDOW")
        if shared_controller.snapshot().active:
            self.layout.operator("clothnext.preview_cancel", text="Cancel UI Preview", icon="CANCEL")
        if _developer_tools_enabled(context):
            _draw_solver_test_section(self.layout, context)


class CLOTHNEXT_PT_advanced(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Advanced PPF"; bl_idname = "CLOTHNEXT_PT_advanced"; bl_options = {"DEFAULT_CLOSED"}
    def draw(self, _context):
        self.layout.label(text="Direct advanced solver mapping is not active yet.", icon="INFO")


CLASSES = (CLOTHNEXT_PT_physics, CLOTHNEXT_PT_overview, CLOTHNEXT_PT_solver,
           CLOTHNEXT_PT_quality, CLOTHNEXT_PT_physical, CLOTHNEXT_PT_damping,
           CLOTHNEXT_PT_collisions, CLOTHNEXT_PT_pressure, CLOTHNEXT_PT_shape,
           CLOTHNEXT_PT_cache, CLOTHNEXT_PT_advanced)
