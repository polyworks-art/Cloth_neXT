# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS Dynamic Wetness panels inside GAIA Water Flow, plus zone wetness overrides."""
from __future__ import annotations

import bpy

from ..tethys import profiles
from . import tethys_bake


def _settings(context):
    return context.object.cloth_next.tethys


def _locked(context) -> bool:
    from ..bake.controller import shared_controller
    return shared_controller.snapshot().active or tethys_bake.is_running()


def _target(obj, identity):
    """Override collection of the Base Material ('' ) or one Material Zone."""
    if not identity:
        return obj.cloth_next.tethys.overrides
    zone = next((z for z in obj.cloth_next.material_zones if z.identity == identity), None)
    if zone is None:
        raise ValueError("Material Zone no longer exists")
    return zone.wetness_overrides


def _profile_values(obj, identity):
    base = tethys_bake.base_profile(obj)
    if identity:
        zone = next(z for z in obj.cloth_next.material_zones if z.identity == identity)
        if zone.wetness_profile != profiles.INHERIT:
            return profiles.profile(zone.wetness_profile).values()
        base_overrides = tuple((o.key, o.value) for o in obj.cloth_next.tethys.overrides)
        return profiles.profile(base).with_overrides(base_overrides).values()
    return profiles.profile(base).values()


class CLOTHNEXT_OT_tethys_override(bpy.types.Operator):
    bl_idname = "clothnext.tethys_override"
    bl_label = "Wetness Property"
    bl_description = "Add or remove a TETHYS wetness override. Does not affect the cloth simulation"
    bl_options = {"UNDO", "INTERNAL"}
    identity: bpy.props.StringProperty(options={"HIDDEN"})
    key: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    remove: bpy.props.BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and getattr(obj, "cloth_next", None) and not _locked(context))

    def invoke(self, context, _event):
        if self.key:
            return self.execute(context)
        obj = context.object
        try:
            present = {o.key for o in _target(obj, self.identity)}
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        identity, idname = self.identity, self.bl_idname

        def menu(menu_self, _context):
            for parameter in profiles.PARAMETERS:
                if parameter.key not in present:
                    op = menu_self.layout.operator(idname, text=parameter.label)
                    op.identity, op.key, op.remove = identity, parameter.key, False

        context.window_manager.popup_menu(menu, title="Add Wetness Property")
        return {"FINISHED"}

    def execute(self, context):
        obj = context.object
        try:
            target = _target(obj, self.identity)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        index = next((i for i, o in enumerate(target) if o.key == self.key), None)
        if self.remove:
            if index is not None:
                target.remove(index)
            return {"FINISHED"}
        if self.key not in profiles.PARAMETER_BY_KEY:
            self.report({"ERROR"}, "Unsupported wetness property")
            return {"CANCELLED"}
        if index is None:
            item = target.add()
            item.key = self.key
            item.value = _profile_values(obj, self.identity)[self.key]
        return {"FINISHED"}


def draw_overrides(layout, obj, collection, identity):
    for item in collection:
        parameter = profiles.PARAMETER_BY_KEY.get(item.key)
        row = layout.row(align=True)
        label = parameter.label if parameter else item.key
        if parameter and parameter.unit:
            label = f"{label} ({parameter.unit})"
        row.prop(item, "value", text=label)
        op = row.operator("clothnext.tethys_override", text="", icon="X")
        op.identity, op.key, op.remove = identity, item.key, True
    op = layout.operator("clothnext.tethys_override", text="Add Wetness Property", icon="ADD")
    op.identity, op.key, op.remove = identity, "", False


def draw_zone_wetness(box, obj, zone):
    """Optional 'TETHYS Wetness Properties' inside an expanded Material Zone."""
    if not obj.cloth_next.tethys.enabled:
        return
    row = box.row(align=True)
    row.prop(zone, "wetness_expanded", text="", emboss=False,
             icon="TRIA_DOWN" if zone.wetness_expanded else "TRIA_RIGHT")
    row.label(text="TETHYS Wetness Properties")
    if zone.wetness_expanded:
        column = box.column(align=True)
        column.prop(zone, "wetness_profile", text="Profile")
        draw_overrides(column, obj, zone.wetness_overrides, zone.identity)


class _Panel:
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "physics"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and getattr(obj, "cloth_next", None) and obj.cloth_next.enabled
                    and obj.cloth_next.role == "CLOTH")


class CLOTHNEXT_PT_tethys(_Panel, bpy.types.Panel):
    bl_label = "TETHYS Dynamic Wetness"
    bl_idname = "CLOTHNEXT_PT_tethys"
    bl_parent_id = "CLOTHNEXT_PT_water_flow"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        s = _settings(context)
        row = self.layout.row()
        # Switching on needs GAIA; switching off always stays possible.
        row.enabled = not _locked(context) and (s.enabled or tethys_bake.availability().available)
        row.prop(s, "enabled", text="")

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        state = tethys_bake.availability()
        if not state.available:
            row = layout.row()
            row.label(text=state.reason, icon="LOCKED")
            if "Preferences" in state.reason or "access" in state.reason:
                row.operator("clothnext.open_preferences", text="Preferences")
        column = layout.column()
        column.enabled = s.enabled and not _locked(context)
        column.prop(s, "profile")
        if s.profile == profiles.AUTO:
            label = profiles.profile(tethys_bake.base_profile(context.object)).label
            column.label(text=f"Using {label}", icon="INFO")
        sub = column.column(align=True)
        draw_overrides(sub, context.object, s.overrides, "")
        zones = context.object.cloth_next.material_zones
        if zones:
            column.label(text=f"{len(zones)} Material Zone(s): wetness overrides are in each zone",
                         icon="MOD_MASK")


class CLOTHNEXT_PT_tethys_flow(_Panel, bpy.types.Panel):
    bl_label = "Surface Flow"
    bl_idname = "CLOTHNEXT_PT_tethys_flow"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = s.enabled and not _locked(context)
        column = layout.column(align=True)
        column.prop(s, "flow_strength")
        column.prop(s, "retention")
        column.prop(s, "repellency")
        layout.prop(s, "quality")
        column = layout.column(align=True)
        column.prop(s, "surface_detail")
        column.prop(s, "feature_size")
        column.prop(s, "texture_resolution")
        column = layout.column(align=True)
        column.prop(s, "immersion_film")
        column.prop(s, "wetting_time")
        column = layout.column(align=True)
        column.prop(s, "contact_low")
        column.prop(s, "contact_high")


class CLOTHNEXT_PT_tethys_drying(_Panel, bpy.types.Panel):
    bl_label = "Absorption and Drying"
    bl_idname = "CLOTHNEXT_PT_tethys_drying"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = s.enabled and not _locked(context)
        column = layout.column(align=True)
        column.prop(s, "absorption")
        column.prop(s, "capillary")
        column = layout.column(align=True)
        column.prop(s, "evaporation")
        column.prop(s, "ambient_drying")


class CLOTHNEXT_PT_tethys_bake(_Panel, bpy.types.Panel):
    bl_label = "Wetness Cache"
    bl_idname = "CLOTHNEXT_PT_tethys_bake"
    bl_parent_id = "CLOTHNEXT_PT_tethys"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.enabled = s.enabled
        state, message = tethys_bake.cache_status(context.object)
        icon = {"READY": "CHECKMARK", "STALE": "ERROR", "MISSING": "ERROR", "PARTIAL": "ERROR"}.get(state, "INFO")
        layout.label(text=message, icon=icon)
        if s.status and s.status != message:
            layout.label(text=s.status)
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.enabled = tethys_bake.availability().available and not _locked(context)
        row.operator("clothnext.tethys_bake", icon="MOD_FLUIDSIM")
        row = layout.row()
        row.enabled = not tethys_bake.is_running()
        row.operator("clothnext.tethys_clear", icon="TRASH")


class CLOTHNEXT_PT_tethys_appearance(_Panel, bpy.types.Panel):
    bl_label = "Wet Appearance"
    bl_idname = "CLOTHNEXT_PT_tethys_appearance"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = s.enabled
        column = layout.column(align=True)
        column.prop(s, "appearance_strength")
        column.prop(s, "darkening")
        column.prop(s, "wet_roughness")
        column.prop(s, "film_response")
        column.prop(s, "moisture_response")
        column.prop(s, "water_relief")


class CLOTHNEXT_PT_tethys_debug(_Panel, bpy.types.Panel):
    bl_label = "Debug Visualization"
    bl_idname = "CLOTHNEXT_PT_tethys_debug"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = s.enabled
        layout.prop(s, "preview_mode")
        if s.preview_mode in {"SURFACE", "ABSORBED", "DRAINAGE", "FLOW"}:
            layout.prop(s, "preview_scale")
        if s.preview_mode in {"FLOW", "CONTACT", "DRAINAGE"} and not s.debug_channels:
            layout.label(text="Enable Debug Channels and bake again", icon="INFO")
        layout.prop(s, "debug_channels")


CLASSES = (CLOTHNEXT_OT_tethys_override, CLOTHNEXT_PT_tethys, CLOTHNEXT_PT_tethys_flow,
           CLOTHNEXT_PT_tethys_drying, CLOTHNEXT_PT_tethys_bake, CLOTHNEXT_PT_tethys_appearance,
           CLOTHNEXT_PT_tethys_debug)
