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

import os
from dataclasses import dataclass
from pathlib import Path

import bpy

from ..bake.controller import shared_controller
from ..developer import is_dev_build
from ..materials import formatting
from ..materials import presets as material_presets
from ..solver_quality import QUALITY_PRESETS, matching_quality_preset
from . import (beta_tools, collider_proxy, icon_registry, object_properties,
               physics_operators, validation_state)
from .playback_cache import has_cloth_next_playback_marker

_add_entry_appended = False

UNAVAILABLE_OBJECT_TYPES = (
    ("RIGID_BODY", "Rigid Body",
     "Coming soon. PPF PDRD rigid bodies are not supported yet."),
    ("SAND", "Sand",
     "Coming soon. Granular simulation is not supported yet."),
)


class CLOTHNEXT_OT_unavailable_object_type(bpy.types.Operator):
    """Coming soon. This PPF object type is not supported by Cloth NeXt yet."""

    bl_idname = "clothnext.unavailable_object_type"
    bl_label = "Coming Soon"
    bl_options = {"INTERNAL"}

    tooltip: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, _context, properties):
        return properties.tooltip or cls.__doc__

    def execute(self, _context):
        return {"CANCELLED"}


class CLOTHNEXT_MT_object_type(bpy.types.Menu):
    """Presentation-only selector for the authoritative settings.role enum."""

    bl_idname = "CLOTHNEXT_MT_object_type"
    bl_label = "Object Type"

    def draw(self, context):
        layout = self.layout
        obj = getattr(context, "object", None)
        settings = getattr(obj, "cloth_next", None)
        active_role = getattr(settings, "role", "")
        for identifier, label, _description in object_properties.ROLE_ITEMS:
            row = layout.row()
            row.enabled = ((identifier == "FORCE") ==
                           (getattr(obj, "type", "") == "EMPTY"))
            if getattr(obj, "type", "") == "CURVE":
                row.enabled = identifier == "ROD"
            operator = row.operator(
                physics_operators.CLOTHNEXT_OT_set_object_type.bl_idname,
                text=label, depress=identifier == active_role,
                **object_properties.role_icon_kwargs(identifier))
            operator.role = identifier
        layout.separator()
        for _identifier, label, description in UNAVAILABLE_OBJECT_TYPES:
            row = layout.row()
            row.alert = True
            row.enabled = False
            operator = row.operator(
                CLOTHNEXT_OT_unavailable_object_type.bl_idname,
                text=label, icon="LOCKED")
            operator.tooltip = description


def _draw_object_type_selector(layout, settings) -> None:
    """Draw a compact menu without introducing any duplicate state."""
    labels = {identifier: label for identifier, label, _ in object_properties.ROLE_ITEMS}
    row = layout.row(align=True)
    row.label(text="Object Type")
    row.menu(CLOTHNEXT_MT_object_type.bl_idname,
             text=labels.get(settings.role, settings.role.title()))


def _draw_add_physics_entry(panel, context) -> None:
    """Appended to PHYSICS_PT_add; draws the Cloth NeXt add/remove entry."""
    obj = getattr(context, "object", None)
    if obj is None or obj.type not in {"MESH", "CURVE", "EMPTY"}:
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
        if obj is None or obj.type not in {"MESH", "CURVE", "EMPTY"}:
            return False
        settings = getattr(obj, "cloth_next", None)
        return settings is not None and settings.enabled

    def draw(self, context):
        from . import addon_update_operators
        layout = self.layout
        settings = context.object.cloth_next
        layout.use_property_split = True
        layout.use_property_decorate = False
        _draw_object_type_selector(layout, settings)
        addon_update_operators.request_automatic_update_check(context)
        update_session = addon_update_operators.session()
        update_view = addon_update_operators.addon_updates.build_section_view(
            update_session.state, update_session.latest,
            update_session.message)
        version = layout.row(align=True)
        version.label(
            text=f"Version: {addon_update_operators.INSTALLED_VERSION}",
            icon="PACKAGE")
        update_icon = ("ERROR" if update_session.state is
                       addon_update_operators.AddonUpdateState.UPDATE_AVAILABLE
                       else "CHECKMARK" if update_session.state is
                       addon_update_operators.AddonUpdateState.UP_TO_DATE
                       else "INFO")
        version.label(text=update_view.status_text, icon=update_icon)
        snapshot = shared_controller.snapshot()
        box = layout.box()
        col = box.column(align=True)
        role_icon = settings.role.lower() if settings.role in {
            "CLOTH", "COLLIDER", "FORCE"} else "cloth_next"
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


class CLOTHNEXT_PT_empty_force(bpy.types.Panel):
    """Guaranteed Cloth NeXt entry for Emptys in Object Data Properties."""

    bl_label = "Cloth NeXt Force"
    bl_idname = "CLOTHNEXT_PT_empty_force"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return obj is not None and obj.type == "EMPTY"

    def draw_header(self, _context):
        self.layout.label(
            text="", **icon_registry.icon_kwargs("force", "FORCE_FORCE"))

    def draw(self, context):
        layout = self.layout
        obj = context.object
        settings = getattr(obj, "cloth_next", None)
        if settings is None:
            layout.label(text="Cloth NeXt is not registered", icon="ERROR")
            return
        if not settings.enabled:
            layout.operator(
                physics_operators.CLOTHNEXT_OT_add_physics.bl_idname,
                text="Enable Cloth NeXt Force", icon="FORCE_FORCE")
            return
        layout.use_property_split = True
        layout.use_property_decorate = True
        _draw_object_type_selector(layout, settings)
        force = settings.force
        layout.prop(force, "force_type")
        if force.force_type in {"GRAVITY", "WIND"}:
            layout.prop(force, "strength")
            direction = ("local -Z" if force.force_type == "GRAVITY"
                         else "local +Z")
            layout.label(text=f"Direction: Empty {direction}",
                         icon="ORIENTATION_LOCAL")
            layout.label(text="Rotate the Empty to aim the force")
        elif force.force_type == "AIR_DENSITY":
            layout.prop(force, "air_density")
        elif force.force_type == "AIR_FRICTION":
            layout.prop(force, "air_friction")
        else:
            layout.prop(force, "vertex_air_damp")
        layout.label(text="Properties and rotation can be keyframed")
        layout.operator(
            physics_operators.CLOTHNEXT_OT_remove_physics.bl_idname,
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
        role = context.object.cloth_next.role
        if role == "FORCE":
            return bool(getattr(cls, "force_only", False))
        if getattr(cls, "force_only", False):
            return False
        if getattr(cls, "cloth_only", False):
            return role == "CLOTH"
        if getattr(cls, "deformable_only", False):
            return role in {"CLOTH", "ROD", "SOFT_BODY"}
        return True


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
        if not status.ready:
            header.label(text=status.title)
            layout.operator("clothnext.open_preferences",
                            text="Open Add-on Preferences")

        model = _bake_panel_model(context, status)
        snapshot = shared_controller.snapshot()
        action = layout.row(align=True)
        split = action.split(factor=0.86, align=True)
        # Use columns as the split's direct children so both operators fill
        # their allocated width. An aligned child row shrink-wraps the folder
        # operator to its icon and leaves part of the panel visibly unused.
        bake_button = split.column(align=True)
        bake_button.scale_y = 1.6
        bake_button.enabled = model.enabled and not snapshot.active
        bake_button.operator("clothnext.bake", text=model.action,
                             **icon_registry.icon_kwargs("bake",
                                                         "RENDER_ANIMATION"))
        # Small folder button on the right sets the Cache Directory. It stays
        # enabled even when Bake is disabled for a missing directory, so the
        # artist can satisfy the requirement without leaving the panel.
        set_dir = split.column(align=True)
        set_dir.scale_y = 1.6
        set_dir.enabled = not snapshot.active
        set_dir.operator("clothnext.set_cache_directory", text="",
                         icon="FILE_FOLDER")
        if not snapshot.active:
            warning = _animated_collider_capture_warning(context, solver_test)
            if warning is not None:
                warning_row = layout.row()
                warning_row.alert = True
                warning_row.label(
                    text=(f'Large animated Collider capture: '
                          f'~{warning.size_label}'), icon="ERROR")
                layout.label(
                    text="Bake allowed · Low-poly collision proxy recommended.")
            contact_warning = _contact_stability_warning(context)
            if contact_warning:
                warning_row = layout.row()
                warning_row.alert = True
                warning_row.label(text=contact_warning, icon="ERROR")
                layout.label(text="Bake allowed · Try Gap 0.001 and Friction 0.2–0.3")
        if snapshot.active:
            progress_text = _run_state_text(snapshot)
            layout.label(text=progress_text)
            if snapshot.can_cancel:
                layout.operator("clothnext.bake_cancel", text="Cancel",
                                **icon_registry.icon_kwargs("cancel", "CANCEL"))
        elif model.reason:
            layout.label(text=model.reason, icon="ERROR")
        if snapshot.state.value == "ERROR" and snapshot.error_summary:
            layout.operator("clothnext.companion_open_logs", text="Open Logs",
                            icon="FILE_FOLDER")
        if not snapshot.active:
            validation = layout.row(align=True)
            validation.label(text=_validation_line(context))
            validation.operator("clothnext.validate", text="Validate",
                                **icon_registry.icon_kwargs("validate",
                                                            "CHECKMARK"))
        summary = layout.column(align=True)
        summary.label(text=model.summary_line)
        try:
            cloth, _ = solver_test._enabled_objects_for_bake(context)
            start, end = cloth.cloth_next.bake_start, cloth.cloth_next.bake_end
            summary.label(text=f"Frames {start}–{end} · {end-start+1} cached "
                               f"frames · {model.cache_label}")
        except solver_test.SceneValidationError:
            summary.label(text=model.cache_label)
        _draw_solver_quality(layout, context, snapshot.active)


def _draw_solver_quality(layout, context, bake_active: bool) -> None:
    quality = getattr(context.scene, "cloth_next_quality", None)
    if quality is None:
        return
    section = layout.column(align=True)
    section.label(text="Solver Quality · Scene-wide")
    current = matching_quality_preset(
        object_properties.solver_quality_from(context.scene))
    buttons = section.row(align=True)
    buttons.enabled = not bake_active
    for preset in QUALITY_PRESETS:
        button = buttons.row(align=True)
        button.alert = preset.identifier == "EXTREME"
        operator = button.operator(
            physics_operators.CLOTHNEXT_OT_apply_solver_quality_preset.bl_idname,
            text=preset.label, depress=current is preset)
        operator.preset = preset.identifier

    if current is None:
        section.label(text="Custom")
        section.label(text="Manually adjusted solver settings.")
    else:
        section.label(text=current.label)
        section.label(text=current.description)
        if current.warning:
            section.label(text=current.warning, icon="ERROR")

    foldout = section.row(align=True)
    foldout.prop(quality, "show_advanced", text="Advanced Settings",
                 icon="TRIA_DOWN" if quality.show_advanced else "TRIA_RIGHT",
                 emboss=False)
    if quality.show_advanced:
        advanced = section.column(align=True)
        advanced.enabled = not bake_active
        advanced.use_property_split = True
        advanced.use_property_decorate = False
        advanced.prop(quality, "time_step")
        advanced.prop(quality, "min_newton_steps")
        advanced.prop(quality, "cg_max_iter")
        advanced.prop(quality, "cg_tol")


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
    """Cheap, honest cache view. Never hashes a mesh and never scans pins.

    The settings half of the Bake fingerprint is recomputable for free, so
    "the settings changed" is stated as fact. The geometry half is not, so
    until a full validation confirms it this reports "needs validation"
    rather than claiming the cache safely matches.
    """
    from . import solver_test
    try:
        cloth, _collider = solver_test._enabled_objects_for_bake(context)
    except solver_test.SceneValidationError:
        return "EMPTY", "Cache empty"
    settings = cloth.cloth_next
    # Marker-only check: no Path.resolve(), so no filesystem syscall in draw.
    modifier = next((mod for mod in cloth.modifiers
                     if has_cloth_next_playback_marker(cloth, mod)), None)
    rod_cache = (str(getattr(cloth.data, "get", lambda *_: "")(
        "cloth_next_rod_cache", "") or "") if cloth.type == "CURVE" else "")
    baked_settings = getattr(settings, "baked_settings_fingerprint", "")
    if (modifier is None and not rod_cache) or not baked_settings:
        return "EMPTY", "Cache empty"

    disk_condition = str(getattr(settings, "baked_cache_condition", "") or "")
    disk_message = str(getattr(settings, "baked_cache_message", "") or "")
    if disk_condition == "CORRUPT":
        return "INVALID", disk_message or "Cache invalid · file damaged"
    if disk_condition == "PARTIAL":
        return "INVALID", disk_message or "Cache incomplete · Rebake required"
    if disk_condition == "MISSING":
        return "INVALID", disk_message or "Cache invalid · files missing"

    record = validation_state.record_for(cloth)
    if record.state is validation_state.ValidationState.INVALID:
        return "INVALID", f"Cache invalid · {record.message}" if record.message \
            else "Cache invalid · topology mismatch"

    # A result baked before the fingerprint was split carries no geometry half.
    # It is never presented as matching; it has to be validated first.
    version = int(getattr(settings, "baked_fingerprint_version", 0) or 0)
    baked_geometry = getattr(settings, "baked_geometry_fingerprint", "")
    if version != solver_test.BAKE_FINGERPRINT_VERSION or not baked_geometry:
        return "NEEDS_VALIDATION", "Cache needs validation · mesh may have changed"

    current_settings = solver_test.cheap_settings_fingerprint(context)
    if current_settings is None or current_settings != baked_settings:
        return "STALE", "Cache stale · settings changed"

    if record.state is validation_state.ValidationState.VALID:
        if record.geometry_fingerprint == baked_geometry:
            return "MATCHING", "Cache ready"
        return "STALE", "Cache stale · mesh changed"
    return "NEEDS_VALIDATION", "Cache needs validation · mesh may have changed"


_VALIDATION_TITLES = {
    validation_state.ValidationState.UNKNOWN: "Ready to validate and bake",
    validation_state.ValidationState.DIRTY: "Scene validation required",
    validation_state.ValidationState.VALIDATING: "Validating scene…",
    validation_state.ValidationState.VALID: "Scene validated",
}


def _validation_line(context) -> str:
    """One recorded status string. Computes nothing."""
    obj = getattr(context, "object", None)
    record = validation_state.record_for(obj)
    if record.state is validation_state.ValidationState.INVALID:
        return record.message or "Scene validation failed"
    return _VALIDATION_TITLES.get(record.state, "Ready to validate and bake")


def _bake_panel_model(context, solver_status: _SolverStatus | None = None) \
        -> _BakePanelModel:
    """Enable/disable the Bake button from cheap state only.

    Obviously invalid *cheap* states (no solver, wrong object counts, an
    animated collider, a broken frame range, an invalid mapped material value,
    a missing pin group) disable the button immediately. Everything that needs
    the mesh — topology, pin indices — is validated when Bake is clicked, not
    on every redraw. A previously recorded INVALID verdict remains visible,
    but never disables retry: otherwise correcting a scene could leave the
    artist permanently locked out of the validation path.
    """
    from . import solver_test
    status = solver_status or _solver_status(context)
    objects = getattr(getattr(context, "scene", None), "objects", ())
    cloths = [obj for obj in objects if getattr(getattr(obj, "cloth_next", None),
                                                "enabled", False)
              and obj.cloth_next.role in {"CLOTH", "ROD", "SOFT_BODY"}]
    colliders = [obj for obj in objects if getattr(getattr(obj, "cloth_next", None),
                                                   "enabled", False)
                 and obj.cloth_next.role == "COLLIDER"]
    role = (cloths[0].cloth_next.role if len(cloths) == 1
            else "MULTI" if cloths else "")
    preset = (cloths[0].cloth_next.material.preset.replace("_", " ").title()
              if role == "CLOTH" else "Multi Object" if role == "MULTI"
              else role.replace("_", " ").title() if role else "No material")
    summary = f"{preset} · {len(cloths)} Deformable · {len(colliders)} Collider"
    cache_state, cache_label = _cache_state(context)
    action = {"STALE": "REBAKE", "INVALID": "REBAKE",
              "NEEDS_VALIDATION": "REBAKE",
              "MATCHING": "BAKE AGAIN"}.get(cache_state, "BAKE")
    reason = ""
    if not status.ready:
        reason = "PPF is not configured."
    elif not cloths:
        reason = "At least one deformable object is required."
    else:
        try:
            from ..bake.frame_range import BakeFrameRange
            ranges = {(int(obj.cloth_next.bake_start),
                       int(obj.cloth_next.bake_end)) for obj in cloths}
            if len(ranges) != 1:
                raise ValueError("All deformables need the same Bake range.")
            BakeFrameRange(*next(iter(ranges)))
            contacts = {bool(obj.cloth_next.collision.enabled)
                        for obj in cloths}
            if len(contacts) != 1:
                raise ValueError(
                    "All deformables need the same Enable Contact setting.")
            # Property-only validation: touches no mesh.
            for obj in cloths:
                solver_test._snapshot_materials(
                    obj, colliders[0] if colliders else None)
        except Exception as exc:  # noqa: BLE001 — an invalid value stays visible
            reason = str(exc) or "Material settings are invalid."
        else:
            for obj in cloths:
                reason = _cheap_pin_reason(solver_test, obj)
                if reason:
                    break
    # A production bake requires a chosen cache folder so the result is not
    # written to the temp directory and lost on the next Blender launch. Kept
    # last so more fundamental problems surface first.
    if not reason and cloths and any(
            not str(getattr(obj.cloth_next, "cache_directory", "") or "").strip()
            for obj in cloths):
        reason = ("Set a Cache Directory (folder button next to Bake) so the "
                  "result survives a Blender restart.")
    return _BakePanelModel(not reason, action, reason, summary, cache_label)


def _animated_collider_capture_warning(context, solver_test):
    """Cheap panel warning; malformed scene state is handled by Bake itself."""
    try:
        deformables, colliders = solver_test._enabled_objects_for_solve(context)
        ranges = {(int(obj.cloth_next.bake_start),
                   int(obj.cloth_next.bake_end)) for obj in deformables}
        if len(ranges) != 1:
            return None
        from ..bake.frame_range import BakeFrameRange
        bake_range = BakeFrameRange(*next(iter(ranges)))
        return solver_test.animated_collider_capture_warning(
            colliders, bake_range)
    except Exception:  # noqa: BLE001 - Panel.draw must remain failure-safe
        return None


def _contact_stability_warning(context) -> str:
    """Warn without overriding deliberate, scale-dependent contact values."""
    objects = getattr(getattr(context, "scene", None), "objects", ())
    deformables = [obj for obj in objects
        if getattr(getattr(obj, "cloth_next", None), "enabled", False)
        and obj.cloth_next.role in {"CLOTH", "ROD", "SOFT_BODY"}]
    if not deformables or not any(
            bool(obj.cloth_next.collision.enabled) for obj in deformables):
        return ""
    for obj in objects:
        settings = getattr(obj, "cloth_next", None)
        if (settings is None or not bool(getattr(settings, "enabled", False))
                or getattr(settings, "role", "") != "COLLIDER"):
            continue
        collision = settings.collision
        if (float(collision.collision_gap) >= 0.01
                and float(collision.surface_grip) >= 0.4):
            return "High Collision Gap and Friction can destabilize pinned Cloth."
    return ""


def _cheap_pin_reason(solver_test, cloth) -> str:
    """Pin problems detectable without reading a single vertex."""
    summary = solver_test.cheap_pin_summary(cloth)
    if not summary.enabled:
        return ""
    if not summary.group_name:
        return "Select a Pin Group."
    if not summary.group_exists:
        return "The selected Pin Group no longer exists."
    return ""


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


def _preset_label(identifier: str) -> str:
    preset = material_presets.preset_by_identifier(identifier)
    if preset is not None:
        return preset.label
    return material_presets.CUSTOM_LABEL


def _draw_material_category(self, context):
    selected = getattr(context.object.cloth_next.material, "preset", "")
    for preset in material_presets.presets_in_category(self.category):
        operator = self.layout.operator(
            "clothnext.apply_material_preset", text=preset.label,
            icon="CHECKMARK" if preset.identifier == selected else "NONE")
        operator.preset = preset.identifier


def _make_material_category_menu(category: str):
    class_name = f"CLOTHNEXT_MT_material_{category.lower()}"
    return type(class_name, (bpy.types.Menu,), {
        "__module__": __name__,
        "bl_idname": class_name,
        "bl_label": material_presets.CATEGORY_LABELS[category],
        "category": category,
        "draw": _draw_material_category,
    })


MATERIAL_PRESET_CATEGORY_MENUS = tuple(
    _make_material_category_menu(category)
    for category in material_presets.CATEGORY_ORDER
)


class CLOTHNEXT_MT_material_presets(bpy.types.Menu):
    """Categorized, hover-opened fabric material library."""

    bl_idname = "CLOTHNEXT_MT_material_presets"
    bl_label = "Material Presets"

    def draw(self, _context):
        for menu in MATERIAL_PRESET_CATEGORY_MENUS:
            if material_presets.presets_in_category(menu.category):
                self.layout.menu(menu.bl_idname, text=menu.bl_label)


class CLOTHNEXT_PT_force(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Force"
    bl_idname = "CLOTHNEXT_PT_force"
    force_only = True
    header_icon = "force"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = True
        force = context.object.cloth_next.force
        layout.prop(force, "force_type")
        info = layout.column(align=True)
        if force.force_type in {"GRAVITY", "WIND"}:
            layout.prop(force, "strength")
            if force.force_type == "WIND":
                layout.prop(force, "wind_variation")
            direction = "local -Z" if force.force_type == "GRAVITY" else "local +Z"
            info.label(text=f"Direction: Empty {direction}", icon="ORIENTATION_LOCAL")
            info.label(text="Rotate the Empty to aim the force")
        elif force.force_type == "AIR_DENSITY":
            layout.prop(force, "air_density")
        elif force.force_type == "AIR_FRICTION":
            layout.prop(force, "air_friction")
        else:
            layout.prop(force, "vertex_air_damp")
        info.label(text="Properties and rotation can be keyframed")
        info.label(text="Forces of the same type are added together")


class CLOTHNEXT_PT_material(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Material"; bl_idname = "CLOTHNEXT_PT_material"; deformable_only = True
    header_icon = "physical"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = context.object.cloth_next
        if settings.role == "ROD":
            rod = settings.rod
            layout.label(text="Cable behavior")
            info = layout.box()
            info.label(text="Curve Bevel is visual only.", icon="INFO")
            info.label(text="Use Collisions > Surface Offset as cable radius.")
            layout.prop(rod, "linear_density")
            layout.prop(rod, "stretch_resistance")
            layout.prop(rod, "bend_resistance")
            layout.prop(rod, "length_factor")
            layout.prop(rod, "stretch_limit_percent")
            return
        if settings.role == "SOFT_BODY":
            soft = settings.soft_body
            layout.label(text="Soft-body behavior")
            layout.label(text="The closed mesh is filled automatically for simulation",
                         icon="INFO")
            layout.prop(soft, "volume_density")
            layout.prop(soft, "stretch_resistance")
            layout.prop(soft, "poisson_ratio")
            layout.prop(soft, "volume_scale")
            layout.prop(soft, "tetrahedralizer")
            return
        material = settings.material
        error = material_presets.load_error()
        if error:
            layout.label(text="Bundled presets unavailable:", icon="ERROR")
            layout.label(text=error)
        preset_row = layout.row()
        preset_row.label(text="Material Preset")
        preset_row.menu(CLOTHNEXT_MT_material_presets.bl_idname,
                        text=_preset_label(material.preset))
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
        pressure = context.object.cloth_next.pressure
        pressure_box = layout.box()
        pressure_box.label(text="Pressure")
        pressure_box.prop(pressure, "enable_inflate")
        pressure_row = pressure_box.row()
        pressure_row.enabled = pressure.enable_inflate
        pressure_row.prop(pressure, "inflate_pressure")
        pressure_box.label(text="Use consistent normals; closed meshes are "
                                "recommended for balloon-like results.",
                           icon="INFO")


class CLOTHNEXT_PT_pinning(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Pinning"; bl_idname = "CLOTHNEXT_PT_pinning"; cloth_only = True
    header_icon = "pinning"

    def draw(self, context):
        from . import solver_test
        layout = self.layout
        settings = context.object.cloth_next
        controls = layout.column(align=True)
        controls.enabled = not shared_controller.snapshot().active
        controls.prop(settings, "pinning_enabled")
        group_row = controls.row()
        group_row.enabled = bool(settings.pinning_enabled)
        group_row.prop_search(settings, "pin_group", context.object,
                              "vertex_groups", text="Pin Group")
        mode_row=controls.row(); mode_row.enabled=bool(settings.pinning_enabled)
        mode_row.prop(settings,"pin_mode",text="Pin Mode")
        # The pin count comes from the last full validation. Scanning the
        # vertex group here would cost one pass over every vertex on every
        # single redraw — that is what made large meshes unusable.
        summary = solver_test.cheap_pin_summary(context.object)
        for text, icon in _pin_status_lines(summary):
            layout.label(text=text, icon=icon)


def _pin_status_lines(summary):
    """Recorded pinning status, labelled by how trustworthy it is."""
    states = validation_state.ValidationState
    if not summary.enabled:
        return (("Static hard Pinning is disabled", "NONE"),)
    if not summary.group_name:
        return (("Select a Pin Group.", "ERROR"),)
    if not summary.group_exists:
        return (("The selected Pin Group no longer exists.", "ERROR"),)
    if summary.state is states.INVALID:
        return ((summary.message or "Pin selection is invalid.", "ERROR"),)
    if summary.state is states.VALID and summary.counted_group == summary.group_name:
        return ((f"Pinned Vertices: {summary.pin_count}", "NONE"),)
    if summary.state is states.VALIDATING:
        return (("Validating Pin selection…", "INFO"),)
    if summary.state is states.DIRTY:
        return (("Pin selection changed · validation required", "INFO"),)
    return (("Pin selection will be validated before Bake", "INFO"),)


class CLOTHNEXT_PT_damping(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Damping"; bl_idname = "CLOTHNEXT_PT_damping"; deformable_only = True
    header_icon = "damping"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        damping = context.object.cloth_next.damping
        layout.prop(damping, "shape_damping")
        if context.object.cloth_next.role in {"CLOTH", "ROD"}:
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
        if settings.role == "COLLIDER":
            layout.prop(settings, "collider_motion")
            if settings.collider_motion == "ANIMATED":
                layout.prop(settings, "collider_samples_per_frame")
                samples = int(settings.collider_samples_per_frame)
                if samples < 8:
                    layout.label(text="Low sampling can let fast colliders cross cloth",
                                 icon="ERROR")
                elif samples < 12:
                    layout.label(text="Fast or curved motion: consider 12–16 samples",
                                 icon="INFO")
                else:
                    layout.label(text="High-fidelity animated Collider sampling",
                                 icon="CHECKMARK")
                if not collider_proxy.is_generated_proxy(context.object):
                    proxy_box = layout.box()
                    proxy_box.label(text="Simulation Proxy · Preview",
                                    icon="ERROR")
                    proxy_box.prop(settings, "collider_proxy_target_vertices")
                    proxy = getattr(settings, "collider_proxy_object", None)
                    action = proxy_box.row(align=True)
                    action.operator(
                        "clothnext.generate_collider_proxy",
                        text="Regenerate Proxy" if proxy else "Generate Proxy")
                    if proxy:
                        proxy_box.prop(settings, "collider_proxy_enabled")
                        estimate = collider_proxy.proxy_estimate(
                            context.object, proxy)
                        proxy_box.label(
                            text=(f"Geometry: {estimate.source_vertices:,} → "
                                  f"{estimate.proxy_vertices:,} vertices"))
                        proxy_box.label(
                            text=(f"Estimated PPF peak: "
                                  f"{collider_proxy.format_bytes(estimate.source_peak_bytes)} "
                                  f"→ {collider_proxy.format_bytes(estimate.proxy_peak_bytes)}"),
                            icon="MEMORY")
                        proxy_box.label(
                            text="Regenerate after topology or deformer changes",
                            icon="INFO")
                    else:
                        proxy_box.label(
                            text="Original Collider remains active until generated",
                            icon="INFO")
        if settings.role in {"CLOTH", "ROD", "SOFT_BODY"}:
            layout.prop(collision, "enabled")
        column = layout.column()
        if settings.role in {"CLOTH", "ROD", "SOFT_BODY"}:
            column.enabled = collision.enabled
        column.prop(collision, "surface_grip")
        column.prop(collision, "collision_gap")
        column.prop(collision, "surface_offset")
        if (settings.role == "COLLIDER"
                and float(collision.collision_gap) >= 0.01
                and float(collision.surface_grip) >= 0.4):
            warning = layout.row()
            warning.alert = True
            warning.label(
                text="High Collision Gap and Friction may destabilize pinned Cloth",
                icon="ERROR")


def _developer_tools_enabled(context) -> bool:
    if not _developer_tools_build_enabled():
        return False
    addon_id = __package__.partition(".blender")[0]
    try:
        return bool(context.preferences.addons[addon_id]
                    .preferences.developer_tools)
    except (KeyError, AttributeError):
        return False


def _developer_tools_build_enabled() -> bool:
    """Developer UI exists only in explicitly prepared Dev snapshots."""
    return is_dev_build()


def _draw_solver_test_controls(layout, context) -> None:
    """Draw real-solver developer controls into a supplied container."""
    from . import solver_test
    layout.label(text="Real Solver Test", icon="EXPERIMENTAL")
    snapshot = shared_controller.snapshot()
    running = solver_test.run_active()
    layout.operator("clothnext.create_test_scene", icon="MESH_GRID")
    run_row = layout.row()
    run_row.enabled = not running and not snapshot.active
    run_row.operator("clothnext.solver_test_run",
                     **icon_registry.icon_kwargs("bake", "RENDER_ANIMATION"))
    if running or snapshot.active:
        cancel_row = layout.row()
        cancel_row.enabled = snapshot.can_cancel
        cancel_row.operator("clothnext.solver_test_cancel",
                            **icon_registry.icon_kwargs("cancel","CANCEL"))
    column = layout.column(align=True)
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
        details = tuple(line.strip() for line in snapshot.error_details.splitlines()
                        if line.strip())
        for prefix in ("Stage:", "Blender frame:", "What to do:",
                       "Diagnostic log:"):
            line = next((value for value in details
                         if value.startswith(prefix)), None)
            if line:
                column.label(text=line[:180])
    layout.operator("clothnext.inspect_parameters",
                    **icon_registry.icon_kwargs("info", "VIEWZOOM"))
    actions=layout.row(align=True)
    actions.operator("clothnext.companion_launch", text="Bake Window",
                     **icon_registry.icon_kwargs("bake","WINDOW"))
    actions.operator("clothnext.solver_test_open_logs", text="Logs",
                     **icon_registry.icon_kwargs("folder","FILE_FOLDER"))
    actions.operator("clothnext.solver_test_clear", text="Clear", icon="TRASH")


def _draw_ui_diagnostics_controls(layout, _context) -> None:
    layout.label(text="UI Diagnostics")
    snapshot = shared_controller.snapshot()
    layout.operator("clothnext.preview_start", text="Start UI Preview",
                    **icon_registry.icon_kwargs("play", "PLAY"))
    if snapshot.preview and snapshot.active:
        layout.operator("clothnext.preview_cancel", text="Cancel UI Preview",
                        **icon_registry.icon_kwargs("cancel", "CANCEL"))


_STALE_NOTICES = {
    "STALE": "Result is stale — settings or mesh changed since this bake. "
             "Rebake or Clear it explicitly.",
    "INVALID": "Result cannot be trusted — the last validation failed. "
               "Fix the scene, then Rebake.",
    "NEEDS_VALIDATION": "Result is not confirmed — the mesh has not been "
                        "validated since this bake. Validate or Rebake.",
}


def _draw_stale_result_notice(layout, context) -> None:
    """Show the recorded cache verdict.

    Reads the cheap settings fingerprint and the recorded validation state.
    It never hashes the mesh, so it can be honest about "settings changed"
    while staying explicitly unsure about "mesh changed".
    """
    obj = context.object
    if not getattr(obj.cloth_next, "baked_settings_fingerprint", ""):
        return
    try:
        state, _label = _cache_state(context)
    except Exception:  # noqa: BLE001 — a broken scene must not break draw
        return
    notice = _STALE_NOTICES.get(state)
    if notice:
        layout.label(text=notice,
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


class CLOTHNEXT_PT_developer_tools(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Developer Tools"
    bl_idname = "CLOTHNEXT_PT_developer_tools"
    bl_parent_id = "CLOTHNEXT_PT_cache"
    bl_options = {"DEFAULT_CLOSED"}
    cloth_only = True
    header_icon = "warning"

    @classmethod
    def poll(cls, context):
        return (CLOTHNEXT_PT_cache.poll(context)
                and _developer_tools_enabled(context))

    def draw(self, context):
        developer_box = self.layout.box()
        developer_box.alert = True
        developer_box.label(text="Developer-only controls", icon="EXPERIMENTAL")
        developer_box.label(
            text="Internal testing tools. Do not use in production scenes.",
            icon="ERROR")
        _draw_solver_test_controls(developer_box, context)
        developer_box.separator()
        _draw_ui_diagnostics_controls(developer_box, context)


class CLOTHNEXT_PT_beta_readiness(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Scene Health & Recovery"
    bl_idname = "CLOTHNEXT_PT_beta_readiness"
    bl_parent_id = "CLOTHNEXT_PT_cache"
    bl_options = {"DEFAULT_CLOSED"}
    cloth_only = True
    header_icon = "validate"

    def draw(self, context):
        layout = self.layout
        actions = layout.column(align=True)
        actions.enabled = not shared_controller.snapshot().active
        actions.operator("clothnext.scene_health", text="Run Scene Health Check",
                         icon="CHECKMARK")
        for check in beta_tools._last_health[:8]:
            icon = ("ERROR" if check.severity.value == "ERROR" else
                    "INFO" if check.severity.value == "WARNING" else "CHECKMARK")
            row = layout.row(align=True)
            row.alert = check.severity.value == "ERROR"
            row.label(text=f"{check.title}: {check.detail}", icon=icon)
            if check.action:
                layout.label(text=check.action, icon="BLANK1")
        layout.separator()
        cache = layout.box()
        cache.label(text="Cache Recovery", icon="FILE_CACHE")
        cache.operator("clothnext.cache_scan", text="Scan Cache Directory",
                       icon="VIEWZOOM")
        if beta_tools._last_cache_root is not None:
            invalid = sum(entry.deletable for entry in beta_tools._last_cache)
            total = sum(entry.size_bytes for entry in beta_tools._last_cache)
            cache.label(text=(f"{len(beta_tools._last_cache)} cache(s) · "
                              f"{beta_tools.human_bytes(total)} · "
                              f"{invalid} safely removable"))
            for entry in beta_tools._last_cache[:5]:
                cache.label(text=f"{entry.cache_path.name}: {entry.condition}",
                            icon=("ERROR" if entry.deletable else "INFO"))
            clear = cache.row()
            clear.enabled = invalid > 0 and not shared_controller.snapshot().active
            clear.operator("clothnext.cache_clear_invalid",
                           text="Remove Invalid Caches", icon="TRASH")
        layout.separator()
        support = layout.box()
        support.label(text="Support", icon="HELP")
        support.operator("clothnext.export_support_report",
                         text="Export Privacy-Safe Report", icon="TEXT")
        if beta_tools._last_support_report is not None:
            support.label(text=beta_tools._last_support_report.name,
                          icon="CHECKMARK")


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
        info.label(text="Shared solve: multiple deformables and colliders",
                   icon="INFO")


CLASSES = (CLOTHNEXT_OT_unavailable_object_type, CLOTHNEXT_MT_object_type,
           *MATERIAL_PRESET_CATEGORY_MENUS, CLOTHNEXT_MT_material_presets,
           CLOTHNEXT_PT_physics, CLOTHNEXT_PT_empty_force,
           CLOTHNEXT_PT_solver,
           CLOTHNEXT_PT_force, CLOTHNEXT_PT_material, CLOTHNEXT_PT_pinning,
           CLOTHNEXT_PT_damping,
           CLOTHNEXT_PT_collisions, CLOTHNEXT_PT_cache,
           CLOTHNEXT_PT_beta_readiness,
           CLOTHNEXT_PT_developer_tools,
           CLOTHNEXT_PT_advanced)
