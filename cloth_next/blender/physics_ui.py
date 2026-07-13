# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Physics Properties integration for Cloth NeXt (Phase 3B).

The "Cloth NeXt" entry is appended to Blender's own ``PHYSICS_PT_add`` panel
through the stable ``Panel.append``/``Panel.remove`` API. Blender does not
expose the internal two-column grid of the native Add-Physics buttons to
appended callbacks, so the entry renders as a full-width button directly
below the native buttons — the closest placement the public UI API supports
(see docs/LIMITATIONS.md). No Blender source class is monkey-patched and no
third-party add-on internals are touched. Cloth NeXt deliberately has no
N-panel; the Physics Properties tab is the primary workflow.

Honest-controls policy (Phase 3B): every visible, editable property maps to
a real PPF parameter. The former Quality, Pressure, and Shape subpanels and
the editable Cache range are gone until their mappings are verified; the
development frame slice is shown read-only instead. Preset data is parsed
once at import time — no Panel.draw ever reads the preset file.
"""

from __future__ import annotations

import bpy

from . import icon_registry, physics_operators
from ..bake.controller import shared_controller
from ..materials import formatting
from ..materials import presets as material_presets
from . import object_properties

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

    def draw_header(self, _context):
        self.layout.label(text="", **icon_registry.icon_kwargs("cloth_next", "MOD_CLOTH"))

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
        role_icon="cloth" if settings.role=="CLOTH" else "collider"
        col.label(text=f"{context.object.name} · {settings.role.title()}",
                  **icon_registry.icon_kwargs(role_icon,"OBJECT_DATA"))
        state_icon=("error" if snapshot.error_summary else "solver" if snapshot.active
                    else "success" if snapshot.state.value=="FINISHED" else "warning")
        col.label(text=f"Bake: {snapshot.status_title}",
                  **icon_registry.icon_kwargs(state_icon,"INFO"))
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
    header_icon = "info"

    def draw_header(self, _context):
        self.layout.label(text="", **icon_registry.icon_kwargs(self.header_icon, "DOT"))

    @classmethod
    def poll(cls, context):
        if not CLOTHNEXT_PT_physics.poll(context):
            return False
        return not getattr(cls, "cloth_only", False) or context.object.cloth_next.role == "CLOTH"


class CLOTHNEXT_PT_overview(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Overview"; bl_idname = "CLOTHNEXT_PT_overview"
    header_icon = "cloth"
    def draw(self, context):
        s = context.object.cloth_next
        self.layout.label(text=f"{s.role.title()} · {context.object.name}")
        self.layout.label(text="Material and contact values map to the real "
                               "PPF solver parameters",
                          **icon_registry.icon_kwargs("info","INFO"))


class CLOTHNEXT_PT_solver(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Solver"; bl_idname = "CLOTHNEXT_PT_solver"
    header_icon = "solver"
    def draw(self, _context):
        self.layout.label(text="Installation and compatibility are managed in Add-on Preferences.")


def _preset_description(identifier: str) -> str:
    for item_id, _label, description in object_properties.PRESET_ITEMS:
        if item_id == identifier:
            return description
    return ""


class CLOTHNEXT_PT_material(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Material"; bl_idname = "CLOTHNEXT_PT_material"; cloth_only = True
    header_icon = "physical"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        material = context.object.cloth_next.material
        error = material_presets.load_error()
        if error:
            layout.label(text="Bundled presets unavailable:", icon="ERROR")
            layout.label(text=error)
        layout.prop(material, "preset")
        description = _preset_description(material.preset)
        if description:
            layout.label(text=description)
        behavior = layout.column(align=True)
        behavior.label(text="Fabric Behavior")
        behavior.prop(material, "surface_density")
        behavior.prop(material, "stretch_resistance")
        behavior.prop(material, "sideways_response")
        behavior.prop(material, "bend_resistance")
        protection = layout.column(align=True)
        protection.label(text="Stretch Protection")
        protection.prop(material, "stretch_limit_enabled")
        row = protection.row()
        row.enabled = material.stretch_limit_enabled
        row.prop(material, "maximum_stretch_percent")


class CLOTHNEXT_PT_damping(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Damping"; bl_idname = "CLOTHNEXT_PT_damping"; cloth_only = True
    header_icon = "damping"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        damping = context.object.cloth_next.damping
        layout.prop(damping, "deformation_damping")
        layout.prop(damping, "bending_damping")


class CLOTHNEXT_PT_collisions(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Collisions"; bl_idname = "CLOTHNEXT_PT_collisions"
    header_icon = "collision"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = context.object.cloth_next
        collision = settings.collision
        if settings.role == "CLOTH":
            layout.prop(collision, "enabled")
        column = layout.column()
        if settings.role == "CLOTH":
            column.enabled = collision.enabled
        column.prop(collision, "surface_grip")
        column.prop(collision, "contact_gap")
        column.prop(collision, "contact_offset")


def _developer_tools_enabled(context) -> bool:
    addon_id = __package__.partition(".blender")[0]
    try:
        return bool(context.preferences.addons[addon_id]
                    .preferences.developer_tools)
    except (KeyError, AttributeError):
        return False


def _draw_solver_test_section(layout, context) -> None:
    """Developer actions for the real solver slice, clearly separated from
    the production Bake workflow (which does not exist yet)."""
    from . import solver_test
    box = layout.box()
    box.label(text="Developer: Real Solver Test (Phase 3B)", icon="EXPERIMENTAL")
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
        cancel_row.operator("clothnext.solver_test_cancel",
                            **icon_registry.icon_kwargs("cancel","CANCEL"))
    column = box.column(align=True)
    column.label(text=f"State: {snapshot.status_title}")
    if snapshot.status_message:
        column.label(text=snapshot.status_message)
    progress=getattr(column,"progress",None)
    if progress is not None and snapshot.progress_total:
        progress(factor=snapshot.progress_fraction,
                 text=f"{snapshot.progress_current} / {snapshot.progress_total}")
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
    box.operator("clothnext.inspect_parameters",
                 **icon_registry.icon_kwargs("info", "VIEWZOOM"))
    actions=box.row(align=True)
    actions.operator("clothnext.companion_launch", text="Bake Window",
                     **icon_registry.icon_kwargs("bake","WINDOW"))
    actions.operator("clothnext.solver_test_open_logs", text="Logs",
                     **icon_registry.icon_kwargs("folder","FILE_FOLDER"))
    actions.operator("clothnext.solver_test_clear", text="Clear", icon="TRASH")


def _draw_stale_result_notice(layout, context) -> None:
    """Compare the object's baked fingerprint with the current settings.

    Pure in-memory computation on already-loaded properties — no file or
    preset access happens here.
    """
    from . import solver_test
    from ..ppf_run import import_result
    obj = context.object
    settings = obj.cloth_next
    baked = getattr(settings, "baked_settings_fingerprint", "")
    if not baked:
        return
    if not any(mod.name == import_result.MODIFIER_NAME
               for mod in obj.modifiers):
        return
    try:
        current = solver_test.current_settings_fingerprint(context)
    except Exception:  # noqa: BLE001 — a broken scene must not break draw
        return
    if current is not None and current != baked:
        layout.label(text="Result is stale — settings changed since this "
                          "bake. Rebake or Clear it explicitly.",
                     **icon_registry.icon_kwargs("warning", "ERROR"))


class CLOTHNEXT_PT_cache(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Cache"; bl_idname = "CLOTHNEXT_PT_cache"
    header_icon = "cache"
    def draw(self, context):
        layout = self.layout
        layout.label(text="Development slice: Blender frames 1–8",
                     **icon_registry.icon_kwargs("info", "INFO"))
        _draw_stale_result_notice(layout, context)
        if _developer_tools_enabled(context):
            _draw_solver_test_section(layout, context)
        diagnostics=layout.box(); diagnostics.label(text="UI Diagnostics")
        diagnostics.operator("clothnext.preview_start", text="Start UI Preview",
                             **icon_registry.icon_kwargs("play", "PLAY"))
        if shared_controller.snapshot().preview and shared_controller.snapshot().active:
            diagnostics.operator("clothnext.preview_cancel", text="Cancel UI Preview",
                                 **icon_registry.icon_kwargs("cancel","CANCEL"))


class CLOTHNEXT_PT_advanced(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Advanced PPF"; bl_idname = "CLOTHNEXT_PT_advanced"; bl_options = {"DEFAULT_CLOSED"}
    header_icon = "advanced"

    def draw(self, context):
        layout = self.layout
        settings = context.object.cloth_next
        if settings.role == "CLOTH":
            layout.use_property_split = True
            layout.use_property_decorate = False
            layout.prop(settings.material, "model")
        column = layout.column(align=True)
        column.label(text="Exact PPF wire values:")
        try:
            if settings.role == "CLOTH":
                shell = object_properties.shell_settings_from(settings)
                rows = formatting.shell_wire_rows(shell)
            else:
                static = object_properties.static_settings_from(settings)
                rows = formatting.static_wire_rows(static)
        except Exception as exc:  # noqa: BLE001 — invalid values stay visible
            column.label(text=str(exc), icon="ERROR")
            rows = ()
        for artist_label, ppf_key, value in rows:
            column.label(text=f"{artist_label} · {ppf_key}: {value}")
        info = layout.column(align=True)
        info.label(text="Friction mode: Minimum (fixed) — the lower of the "
                        "two touching surfaces wins")
        if settings.role == "CLOTH":
            info.label(text="Stiffness basis: density-normalized PPF "
                            "young-mod (not textbook Pa)")
            contact = ("enabled" if settings.collision.enabled
                       else "DISABLED (disable-contact: true)")
            info.label(text=f"Contact: {contact}")
        info.label(text="Experimental development slice: one cloth, one "
                        "static collider, frames 1–8", icon="EXPERIMENTAL")


CLASSES = (CLOTHNEXT_PT_physics, CLOTHNEXT_PT_overview, CLOTHNEXT_PT_solver,
           CLOTHNEXT_PT_material, CLOTHNEXT_PT_damping,
           CLOTHNEXT_PT_collisions, CLOTHNEXT_PT_cache,
           CLOTHNEXT_PT_advanced)
