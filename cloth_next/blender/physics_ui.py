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
import os
from dataclasses import dataclass
from pathlib import Path

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
    def draw(self, context):
        from . import solver_test
        layout = self.layout
        status = _solver_status(context)
        header = layout.column(align=True)
        header.label(text="PPF Contact Solver",
                     **icon_registry.icon_kwargs("solver", "SETTINGS"))
        header.label(text=status.title)
        for detail in status.details:
            header.label(text=detail)
        if not status.ready:
            layout.operator("clothnext.open_preferences",
                            text="Open Add-on Preferences")

        model = _bake_panel_model(context, status)
        snapshot = shared_controller.snapshot()
        action = layout.row()
        action.scale_y = 1.6
        action.enabled = model.enabled and not snapshot.active
        action.operator("clothnext.bake", text=model.action,
                        **icon_registry.icon_kwargs("bake", "RENDER_ANIMATION"))
        if snapshot.active:
            progress_text = _run_state_text(snapshot)
            layout.label(text=progress_text)
            if snapshot.can_cancel:
                layout.operator("clothnext.bake_cancel", text="Cancel",
                                **icon_registry.icon_kwargs("cancel", "CANCEL"))
        elif model.reason:
            layout.label(text=model.reason, icon="ERROR")
        summary = layout.column(align=True)
        summary.label(text=model.summary_line)
        try:
            cloth, _ = solver_test._enabled_objects_by_role(context)
            start, end = cloth.cloth_next.bake_start, cloth.cloth_next.bake_end
            summary.label(text=f"Frames {start}–{end} · {end-start+1} cached "
                               f"frames · {model.cache_label}")
        except solver_test.SceneValidationError:
            summary.label(text=model.cache_label)


@dataclass(frozen=True, slots=True)
class _SolverStatus:
    ready: bool
    title: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _BakePanelModel:
    enabled: bool
    action: str
    reason: str
    summary_line: str
    cache_label: str


def _solver_status(context) -> _SolverStatus:
    """Non-blocking readiness view; never probes or displays a local path."""
    from . import solver_test
    plan = solver_test._active_plan
    if plan is not None:
        resolved = plan.resolved
        details = tuple(value for value in (
            f"Package {resolved.package_version}" if resolved.package_version else "",
            f"Protocol {resolved.protocol_version}" if resolved.protocol_version else "",
            f"Schema {resolved.schema_version}" if resolved.schema_version else "",
            resolved.mode.name.replace("_", " ").title(),
        ) if value)
        protocol = resolved.protocol_version or "unknown"
        return _SolverStatus(True, f"Ready · Protocol {protocol}", details)
    addon_id = __package__.partition(".blender")[0]
    try:
        prefs = context.preferences.addons[addon_id].preferences
        raw = str(prefs.external_solver_path or "").strip()
    except (KeyError, AttributeError):
        raw = ""
    if raw:
        root = Path(raw)
        configured = root.is_file() or root.is_dir()
        return _SolverStatus(configured,
                             "External installation" if configured
                             else "Solver unavailable")
    try:
        from ..updater.install_paths import ManagedSolverPaths, read_current
        paths = ManagedSolverPaths.default()
        active = read_current(paths)
        if active is not None and active.executable_path(paths).is_file():
            from ..updater.solver_manifest import load_bundled_manifest
            entry = load_bundled_manifest().entry_for("windows-x86_64")
            details = (f"Package {active.version}",
                       f"Protocol {entry.protocol_version}",
                       f"Schema {entry.schema_version}",
                       "Managed installation") if entry else (
                           f"Package {active.version}", "Managed installation")
            protocol = entry.protocol_version if entry else "unknown"
            return _SolverStatus(True, f"Ready · Protocol {protocol}", details)
    except (OSError, ValueError):
        return _SolverStatus(False, "Solver unavailable")
    development = os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE", "").strip()
    if development and Path(development).is_file():
        return _SolverStatus(True, "External installation",
                             ("Development configuration",))
    return _SolverStatus(False, "Not configured")


def _cache_state(context) -> tuple[str, str]:
    from . import solver_test
    from ..ppf_run import import_result
    try:
        cloth, _collider = solver_test._enabled_objects_by_role(context)
    except solver_test.SceneValidationError:
        return "EMPTY", "Cache empty"
    modifier = next((mod for mod in cloth.modifiers
                     if mod.name == import_result.MODIFIER_NAME), None)
    baked = getattr(cloth.cloth_next, "baked_settings_fingerprint", "")
    if modifier is None or not baked:
        return "EMPTY", "Cache empty"
    current = solver_test.current_settings_fingerprint(context)
    if current != baked:
        return "STALE", "Cache stale"
    return "MATCHING", "Cache ready"


def _bake_panel_model(context, solver_status: _SolverStatus | None = None) \
        -> _BakePanelModel:
    from . import solver_test
    status = solver_status or _solver_status(context)
    objects = getattr(getattr(context, "scene", None), "objects", ())
    cloths = [obj for obj in objects if getattr(getattr(obj, "cloth_next", None),
                                                "enabled", False)
              and obj.cloth_next.role == "CLOTH"]
    colliders = [obj for obj in objects if getattr(getattr(obj, "cloth_next", None),
                                                   "enabled", False)
                 and obj.cloth_next.role == "COLLIDER"]
    preset = (cloths[0].cloth_next.material.preset.replace("_", " ").title()
              if len(cloths) == 1 else "No material")
    summary = f"{preset} · {len(cloths)} Cloth · {len(colliders)} Collider"
    cache_state, cache_label = _cache_state(context)
    action = {"STALE": "REBAKE", "MATCHING": "BAKE AGAIN"}.get(
        cache_state, "BAKE")
    reason = ""
    if not status.ready:
        reason = "PPF is not configured."
    elif len(cloths) != 1:
        reason = "Exactly one Cloth object is currently supported."
    elif len(colliders) != 1:
        reason = "Exactly one static Collider is currently supported."
    elif getattr(colliders[0], "animation_data", None) is not None:
        reason = "Animated colliders are not supported yet."
    else:
        try:
            from ..bake.frame_range import BakeFrameRange
            BakeFrameRange(int(cloths[0].cloth_next.bake_start),
                           int(cloths[0].cloth_next.bake_end))
            solver_test._snapshot_materials(cloths[0], colliders[0])
        except Exception as exc:
            reason = str(exc) or "Material settings are invalid."
    return _BakePanelModel(not reason, action, reason, summary, cache_label)


def _run_state_text(snapshot) -> str:
    if snapshot.state.value == "SIMULATING" and snapshot.current_frame is not None:
        return f"Simulating {snapshot.current_frame} / {snapshot.frame_end}"
    title = snapshot.status_title
    return title if snapshot.state.value in {"FINISHED", "CANCELLED", "ERROR"} \
        else title + "…"


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
        behavior.prop(material, "surface_weight")
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
        layout.prop(damping, "shape_damping")
        layout.prop(damping, "fold_damping")


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
        column.prop(collision, "collision_gap")
        column.prop(collision, "surface_offset")


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
    cloth_only = True
    header_icon = "cache"
    def draw(self, context):
        layout = self.layout
        settings = context.object.cloth_next
        controls = layout.column(align=True)
        controls.enabled = not shared_controller.snapshot().active
        controls.prop(settings, "bake_start")
        controls.prop(settings, "bake_end")
        controls.operator("clothnext.use_scene_range", text="Use Scene Range")
        controls.prop(settings, "cache_directory")
        try:
            from ..bake.frame_range import BakeFrameRange
            selected = BakeFrameRange(int(settings.bake_start),
                                      int(settings.bake_end))
            layout.label(text=f"Frames {selected.start}–{selected.end} · "
                              f"{selected.output_count} cached frames")
        except Exception as exc:
            layout.label(text=str(exc), icon="ERROR")
        _draw_stale_result_notice(layout, context)
        clear_row = layout.row()
        clear_row.enabled = not shared_controller.snapshot().active
        clear_row.operator("clothnext.solver_test_clear", text="Clear Result",
                           icon="TRASH")
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
        info.label(text="Current scope: one cloth and one static collider",
                   icon="EXPERIMENTAL")


CLASSES = (CLOTHNEXT_PT_physics, CLOTHNEXT_PT_overview, CLOTHNEXT_PT_solver,
           CLOTHNEXT_PT_material, CLOTHNEXT_PT_damping,
           CLOTHNEXT_PT_collisions, CLOTHNEXT_PT_cache,
           CLOTHNEXT_PT_advanced)
