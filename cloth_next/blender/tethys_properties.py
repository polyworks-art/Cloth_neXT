# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS wetness settings stored on Cloth objects and Material Zones.

None of these properties feed the PPF/GAIA cloth solver, so editing them
never marks the cloth simulation dirty; they only make the wetness cache
stale.
"""
from __future__ import annotations

import bpy

from ..tethys import profiles

PROFILE_ITEMS = tuple((p.identifier, p.label, p.description) for p in profiles.PROFILES.values())
BASE_PROFILE_ITEMS = ((profiles.AUTO, "From Material Preset",
                       "Use the wetness profile that matches the fabric preset (Generic Fabric if none fits)"),
                      ) + PROFILE_ITEMS
ZONE_PROFILE_ITEMS = ((profiles.INHERIT, "Inherit",
                       "Use the Base Material wetness profile"),) + PROFILE_ITEMS
QUALITY_SUBSTEPS = {"DRAFT": 16, "STANDARD": 64, "HIGH": 256}
PREVIEW_ITEMS = (
    ("NONE", "Off", "No wetness overlay"),
    ("SURFACE", "Surface Water", "Surface film thickness"),
    ("ABSORBED", "Absorbed Moisture", "Absorbed water per area"),
    ("SATURATION", "Saturation", "Absorbed water relative to capacity"),
    ("FLOW", "Water Flow Direction", "Surface flux direction (needs Debug Channels)"),
    ("ZONES", "Material Zone Boundaries", "Faces colored by wetness material"),
    ("CONTACT", "Water Contact", "Contact with GAIA water (needs Debug Channels)"),
    ("DRAINAGE", "Drainage / Runoff", "Water leaving open edges (needs Debug Channels)"),
)


def _tethys_changed(self, _context):
    # Wetness settings never touch cloth validation; the cache fingerprint
    # comparison reports a stale wetness bake instead. Blender reports the
    # property edit as an Object geometry update, so keep the record.
    from . import tethys_bake, validation_state
    owner = getattr(self, "id_data", None)
    if owner is not None and getattr(owner, "cloth_next", None) is not None:
        validation_state.keep_record_through_update(owner)
    tethys_bake.tag_redraw()


def _appearance_changed(self, context):
    from . import tethys_shader
    obj = getattr(self, "id_data", None)
    if obj is not None:
        tethys_shader.update_appearance(obj)


def _preview_changed(_self, _context):
    from . import tethys_overlay
    tethys_overlay.invalidate()


class CLOTHNEXT_PG_wetness_override(bpy.types.PropertyGroup):
    key: bpy.props.StringProperty(options={"HIDDEN"}, update=_tethys_changed)
    value: bpy.props.FloatProperty(name="Value", default=0.0, min=0.0, update=_tethys_changed)


class CLOTHNEXT_PG_tethys_settings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="Enable TETHYS", default=False, update=_tethys_changed,
        description="Simulate the water that stays on and inside this cloth. "
                    "GAIA moves the water; her daughter TETHYS keeps what stays")
    profile: bpy.props.EnumProperty(
        name="Wetness Profile", items=BASE_PROFILE_ITEMS, default=profiles.AUTO, update=_tethys_changed,
        description="How the Base Material takes up, holds and sheds water. Starting points, not measurements")
    overrides: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_wetness_override)
    flow_strength: bpy.props.FloatProperty(
        name="Surface Flow Strength", default=1.0, min=0.0, max=100.0, soft_max=10.0,
        update=_tethys_changed, description="Scales how freely surface water runs over every material")
    absorption: bpy.props.FloatProperty(
        name="Absorption", default=1.0, min=0.0, max=100.0, soft_max=10.0, update=_tethys_changed,
        description="Scales how fast surface water soaks into every material")
    capillary: bpy.props.FloatProperty(
        name="Capillary Spreading", default=1.0, min=0.0, max=100.0, soft_max=10.0,
        update=_tethys_changed, description="Scales how fast absorbed moisture wicks through the fabric")
    evaporation: bpy.props.FloatProperty(
        name="Evaporation", default=1.0, min=0.0, max=10000.0, soft_max=100.0, update=_tethys_changed,
        description="Scales drying. Raise it to see drying within a short shot")
    repellency: bpy.props.FloatProperty(
        name="Water Repellency", default=1.0, min=0.0, max=10.0, soft_max=2.0, update=_tethys_changed,
        description="Scales every material's resistance to wetting")
    retention: bpy.props.FloatProperty(
        name="Surface Retention", default=1.0, min=0.0, max=100.0, soft_max=5.0, update=_tethys_changed,
        description="Scales the film every material holds before water runs off or drips")
    ambient_drying: bpy.props.FloatProperty(
        name="Ambient Drying", default=0.72, min=0.0, max=36000.0, soft_max=100.0, precision=3,
        update=_tethys_changed, description="Water a fully wet surface loses per hour in this scene. Unit: mm/h")
    quality: bpy.props.EnumProperty(
        name="Quality", default="STANDARD", update=_tethys_changed,
        items=(("DRAFT", "Draft", "Up to 16 substeps per frame"),
               ("STANDARD", "Standard", "Up to 64 substeps per frame"),
               ("HIGH", "High", "Up to 256 substeps per frame")),
        description="Upper limit of solver substeps per frame. Fewer substeps only matter for fast, thick films")
    surface_detail: bpy.props.EnumProperty(
        name="Surface Detail", default="4", update=_tethys_changed,
        items=(("1", "Cloth Resolution", "One water cell per cloth triangle"),
               ("2", "2 x 2", "Four water cells per cloth triangle"),
               ("4", "4 x 4", "Sixteen water cells per cloth triangle"),
               ("6", "6 x 6", "36 water cells per cloth triangle"),
               ("8", "8 x 8", "64 water cells per cloth triangle")),
        description="Water cells per cloth triangle. Finer cells show streaks and beads; the cloth mesh is not changed")
    texture_resolution: bpy.props.EnumProperty(
        name="Wetness Texture", default="1024", update=_tethys_changed,
        items=(("0", "Per Face", "One wetness value per face, no UV texture"),
               ("512", "512 px", ""), ("1024", "1024 px", ""), ("2048", "2048 px", "")),
        description="Resolution of the baked wetness texture in the object's UV layout")
    feature_size: bpy.props.FloatProperty(
        name="Feature Size", default=4.0, min=0.2, max=200.0, precision=1, update=_tethys_changed,
        description="Size of the fabric's surface irregularities that steer streaks. Unit: mm")
    immersion_film: bpy.props.FloatProperty(
        name="Immersion Film", default=1.0, min=0.0, max=50.0, precision=2, update=_tethys_changed,
        description="Surface water a fully submerged face holds. Unit: mm")
    wetting_time: bpy.props.FloatProperty(
        name="Wetting Time", default=0.1, min=0.001, max=100.0, precision=3, update=_tethys_changed,
        description="How quickly a submerged face fills with water. Unit: s")
    contact_low: bpy.props.FloatProperty(
        name="Contact Start", default=0.25, min=0.0, max=0.99, update=_tethys_changed,
        description="GAIA water support below which a face is dry")
    contact_high: bpy.props.FloatProperty(
        name="Full Contact", default=0.75, min=0.01, max=1.0, update=_tethys_changed,
        description="GAIA water support at which a face counts as fully submerged")
    debug_channels: bpy.props.BoolProperty(
        name="Debug Channels", default=False, update=_tethys_changed,
        description="Also record contact, drainage and flow for the debug views (larger cache)")
    preview_mode: bpy.props.EnumProperty(
        name="Wetness Preview", items=PREVIEW_ITEMS, default="NONE", update=_preview_changed,
        description="Show the baked wetness state in the 3D Viewport. Display only")
    preview_scale: bpy.props.FloatProperty(
        name="Preview Range", default=1.0, min=0.001, max=1000.0, update=_preview_changed,
        description="Value shown at full color for Surface Water and Absorbed Moisture. Unit: mm")
    darkening: bpy.props.FloatProperty(
        name="Darkening", default=0.6, min=0.0, max=1.0, update=_appearance_changed,
        description="How much absorbed moisture darkens the material")
    wet_roughness: bpy.props.FloatProperty(
        name="Wet Roughness", default=0.08, min=0.0, max=1.0, update=_appearance_changed,
        description="Roughness of a surface covered by a water film")
    film_response: bpy.props.FloatProperty(
        name="Film Response", default=0.08, min=0.001, max=50.0, update=_appearance_changed,
        description="Surface water at which the wet look is complete. Unit: mm")
    moisture_response: bpy.props.FloatProperty(
        name="Moisture Response", default=0.25, min=0.001, max=50.0, update=_appearance_changed,
        description="Absorbed water at which the darkening is complete. Unit: mm")
    water_relief: bpy.props.FloatProperty(
        name="Water Relief", default=1.0, min=0.0, max=20.0, update=_appearance_changed,
        description="Strength of the water normal of drops and rivulets (1 = their physical shape)")
    appearance_strength: bpy.props.FloatProperty(
        name="Wetness Strength", default=1.0, min=0.0, max=1.0, update=_appearance_changed,
        description="Overall strength of the wet appearance")
    # Bake bookkeeping (not editable).
    cache_stem: bpy.props.StringProperty(options={"HIDDEN"})
    cache_root: bpy.props.StringProperty(options={"HIDDEN"}, subtype="DIR_PATH")
    baked_fingerprint: bpy.props.StringProperty(options={"HIDDEN"})
    status: bpy.props.StringProperty(options={"HIDDEN"})
    image_width: bpy.props.IntProperty(options={"HIDDEN"})
    image_height: bpy.props.IntProperty(options={"HIDDEN"})
    baked_first: bpy.props.IntProperty(options={"HIDDEN"})
    baked_last: bpy.props.IntProperty(options={"HIDDEN"})


CLASSES = (CLOTHNEXT_PG_wetness_override, CLOTHNEXT_PG_tethys_settings)
