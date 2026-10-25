# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS Wetness panels (a Physics panel next to GAIA Water Flow), plus zone wetness overrides."""
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


def _split(layout):
    layout.use_property_split = True
    layout.use_property_decorate = False
    return layout


# The main panel holds what an artist touches: the fabric, the bake and the
# look. Simulation and shading internals live in collapsed sub-panels.

class CLOTHNEXT_PT_tethys(_Panel, bpy.types.Panel):
    bl_label = "TETHYS Wetness"
    bl_idname = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        s = _settings(context)
        row = self.layout.row(align=True)
        # Switching on needs GAIA; switching off always stays possible.
        row.enabled = not _locked(context) and (s.enabled or tethys_bake.availability().available)
        row.prop(s, "enabled", text="")
        from . import icon_registry
        row.label(text="", **icon_registry.icon_kwargs("tethys", "MOD_FLUIDSIM"))

    def draw(self, context):
        s = _settings(context)
        obj = context.object
        layout = self.layout
        state = tethys_bake.availability()
        if not state.available:
            row = layout.row()
            row.label(text=state.reason, icon="LOCKED")
            if "Preferences" in state.reason or "access" in state.reason:
                row.operator("clothnext.open_preferences", text="", icon="PREFERENCES")
        if not s.enabled:
            layout.label(text="Switch on to let this cloth get wet from the GAIA water.", icon="INFO")
            return

        # Fabric
        column = _split(layout.column())
        column.enabled = not _locked(context)
        column.prop(s, "profile", text="Fabric")
        if s.profile == profiles.AUTO:
            label = profiles.profile(tethys_bake.base_profile(obj)).label
            row = column.row()
            row.alignment = "RIGHT"
            row.label(text=f"{label} (from the cloth preset)")

        # Bake
        box = layout.box()
        cache_state, message = tethys_bake.cache_status(obj)
        icon = {"READY": "CHECKMARK", "STALE": "ERROR", "MISSING": "ERROR",
                "PARTIAL": "ERROR"}.get(cache_state, "INFO")
        box.label(text=message, icon=icon)
        row = box.row(align=True)
        row.scale_y = 1.4
        bake = row.row(align=True)
        bake.enabled = state.available and not _locked(context)
        bake.operator("clothnext.tethys_bake", text="Bake Wetness", icon="MOD_FLUIDSIM")
        clear = row.row(align=True)
        clear.enabled = not tethys_bake.is_running() and cache_state != "NONE"
        clear.operator("clothnext.tethys_clear", text="", icon="TRASH")

        # Look (changes apply at once, no rebake)
        column = _split(layout.column(align=True))
        column.prop(s, "appearance_strength", text="Wetness", slider=True)
        column.prop(s, "darkening", text="Darkening", slider=True)
        column.prop(s, "wet_roughness", text="Wet Roughness", slider=True)
        column.prop(s, "water_relief", text="Drop Relief")


class CLOTHNEXT_PT_tethys_detail(_Panel, bpy.types.Panel):
    bl_label = "Detail"
    bl_idname = "CLOTHNEXT_PT_tethys_detail"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and _settings(context).enabled

    def draw(self, context):
        s = _settings(context)
        layout = _split(self.layout)
        layout.enabled = not _locked(context)
        column = layout.column(align=True)
        column.prop(s, "texture_resolution", text="Texture")
        column.prop(s, "surface_detail", text="Water Cells")
        column.prop(s, "feature_size", text="Drop Size (mm)")


class CLOTHNEXT_PT_tethys_advanced(_Panel, bpy.types.Panel):
    bl_label = "Advanced"
    bl_idname = "CLOTHNEXT_PT_tethys_advanced"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and _settings(context).enabled

    def draw(self, context):
        s = _settings(context)
        layout = _split(self.layout)
        layout.enabled = not _locked(context)
        groups = (
            ("runoff", "Runoff", (("flow_strength", "Flow"), ("retention", "Retention"),
                                  ("repellency", "Repellency"))),
            ("soaking", "Soaking and Drying", (("absorption", "Absorption"), ("capillary", "Spreading"),
                                               ("evaporation", "Evaporation"),
                                               ("ambient_drying", "Ambient Drying"))),
            ("contact", "Water Contact", (("immersion_film", "Film"), ("wetting_time", "Wetting Time"),
                                          ("contact_low", "Contact Start"), ("contact_high", "Full Contact"),
                                          ("quality", "Quality"))),
            ("shading", "Shading Response", (("film_response", "Film"), ("moisture_response", "Moisture"))),
        )
        for key, title, props in groups:
            header, body = layout.panel(f"cnx_tethys_{key}", default_closed=True)
            header.label(text=title)
            if body is not None:
                column = body.column(align=True)
                for prop, text in props:
                    column.prop(s, prop, text=text)


class CLOTHNEXT_PT_tethys_overrides(_Panel, bpy.types.Panel):
    bl_label = "Fabric Overrides"
    bl_idname = "CLOTHNEXT_PT_tethys_overrides"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and _settings(context).enabled

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.enabled = not _locked(context)
        draw_overrides(layout.column(align=True), context.object, s.overrides, "")
        zones = context.object.cloth_next.material_zones
        if zones:
            layout.label(text=f"Each of the {len(zones)} Material Zones has its own wetness settings",
                         icon="MOD_MASK")


class CLOTHNEXT_PT_tethys_debug(_Panel, bpy.types.Panel):
    bl_label = "Debug"
    bl_idname = "CLOTHNEXT_PT_tethys_debug"
    bl_parent_id = "CLOTHNEXT_PT_tethys"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and _settings(context).enabled

    def draw(self, context):
        s = _settings(context)
        layout = _split(self.layout)
        layout.prop(s, "preview_mode", text="Preview")
        if s.preview_mode in {"SURFACE", "ABSORBED", "DRAINAGE", "FLOW"}:
            layout.prop(s, "preview_scale", text="Range")
        if s.preview_mode in {"FLOW", "CONTACT", "DRAINAGE"} and not s.debug_channels:
            layout.label(text="Enable Debug Channels and bake again", icon="INFO")
        layout.prop(s, "debug_channels")


CLASSES = (CLOTHNEXT_OT_tethys_override, CLOTHNEXT_PT_tethys, CLOTHNEXT_PT_tethys_detail,
           CLOTHNEXT_PT_tethys_advanced, CLOTHNEXT_PT_tethys_overrides, CLOTHNEXT_PT_tethys_debug)
