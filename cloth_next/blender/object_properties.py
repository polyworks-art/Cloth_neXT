# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persistent per-object Cloth NeXt state (Phase 3B).

Only plain, serializable Blender properties live here. Runtime solver
handles, threads, sockets, and process objects are never stored in Blender
properties; those belong to session-scoped Python state elsewhere.

Phase-3B material model: every displayed property is really sent to the
PPF solver. The old Phase-2.8 placeholder groups (Quality, the
Stretch/Shear/Thickness "Physical Properties", per-mode damping, self
collision, Pressure, Shape, and the editable Cache range) were never read
by any solver and are gone; their values remain as orphaned ID properties
in old .blend files and are deliberately never reinterpreted as physical
PPF values. Existing Cloth NeXt-enabled objects therefore start from the
DEFAULT CLOTH preset while keeping their enabled state and Cloth/Collider
role (see docs/PPF_PARAMETER_MAPPING.md, "Migration").
"""

from __future__ import annotations

import bpy

from ..materials import (
    MODEL_FABRIC,
    MODEL_SHAPE_PRESERVING,
    ShellMaterialSettings,
    StaticMaterialSettings,
)
from ..materials import presets as material_presets
from ..solver_quality import (
    DEFAULT_CG_MAX_ITER,
    DEFAULT_CG_TOL,
    DEFAULT_MIN_NEWTON_STEPS,
    DEFAULT_TIME_STEP,
    MAX_CG_MAX_ITER,
    MAX_CG_TOL,
    MAX_NEWTON_STEPS,
    MAX_TIME_STEP,
    MIN_CG_MAX_ITER,
    MIN_CG_TOL,
    MIN_NEWTON_STEPS,
    MIN_TIME_STEP,
    SolverQualitySettings,
)
from ..materials.deformables import (RodMaterialSettings,
                                     SoftBodyMaterialSettings)
from . import icon_registry, validation_state

ROLE_ITEMS = (
    ("CLOTH", "Cloth", "Simulate this object as cloth"),
    ("ROD", "Rod / Cable", "Simulate this Curve as a one-dimensional rod"),
    ("SOFT_BODY", "Soft Body", "Simulate this closed mesh as a tetrahedral solid"),
    ("COLLIDER", "Collider", "Use this object as a collision obstacle"),
    ("FORCE", "Force", "Add scene-wide gravity or wind from an Empty"),
)

ROLE_ICONS = {
    "CLOTH": ("cloth", "MOD_CLOTH"),
    "ROD": ("rod", "CURVE_DATA"),
    "SOFT_BODY": ("soft_body", "MOD_SOFT"),
    "COLLIDER": ("collider", "MESH_CUBE"),
    "FORCE": ("force", "FORCE_FORCE"),
}

DEFAULT_ROLE = "CLOTH"


def role_icon_kwargs(identifier: str) -> dict:
    """Custom role preview for menus, with a distinct built-in fallback."""
    icon_name, fallback = ROLE_ICONS.get(identifier, ("cloth_next", "OBJECT_DATA"))
    return icon_registry.icon_kwargs(icon_name, fallback)

# ---------------------------------------------------------------------------
# Preset plumbing.
#
# The bundled TOML is parsed exactly once at import (registration) time;
# Panel.draw never touches the file. If the bundle is unusable the enum
# degrades to Custom only, the Material panel shows the load error, and no
# preset can partially apply.

def _build_preset_items() -> tuple[tuple[str, str, str], ...]:
    items: list[tuple[str, str, str]] = []
    try:
        for preset in material_presets.builtin_presets():
            items.append((preset.identifier, preset.label,
                          preset.description))
    except material_presets.PresetError:
        pass
    items.append((material_presets.PRESET_CUSTOM,
                  material_presets.CUSTOM_LABEL,
                  material_presets.CUSTOM_DESCRIPTION))
    return tuple(items)


# Kept alive for the whole session: Blender requires enum item strings to
# stay referenced, and a static tuple guarantees no draw-time file access.
PRESET_ITEMS = _build_preset_items()
_PRESET_IDENTIFIERS = tuple(item[0] for item in PRESET_ITEMS)
DEFAULT_PRESET = (material_presets.DEFAULT_PRESET_ID
                  if material_presets.DEFAULT_PRESET_ID
                  in _PRESET_IDENTIFIERS
                  else material_presets.PRESET_CUSTOM)

# Reentrancy guard: while a preset is being applied, the per-property
# update callbacks must not flip the selection back to Custom.
_applying_preset = False


def _object_settings_of(property_group):
    """The owning CLOTHNEXT_PG_object_settings for a nested group."""
    owner = getattr(property_group, "id_data", None)
    return getattr(owner, "cloth_next", None)


# ---------------------------------------------------------------------------
# Dirty marking.
#
# Every property below that the solver actually reads flips the object's
# recorded validation status to DIRTY. This is a dict write — no vertex, edge,
# polygon, or vertex group is touched. The expensive re-validation happens once,
# later, at Bake (or in the debounced validation timer).

def _mark_dirty(property_group) -> None:
    owner = getattr(property_group, "id_data", None)
    if owner is None:
        return
    if getattr(owner, "cloth_next", None) is None:
        # A Scene-level group (solver quality) — scene-wide, so every enabled
        # Cloth NeXt object has to be re-validated.
        validation_state.mark_all_settings_dirty()
        return
    validation_state.mark_settings_dirty(owner)


def _on_settings_update(self, _context) -> None:
    """Solver-visible value changed: record DIRTY, compute nothing."""
    _mark_dirty(self)


def apply_preset(settings, identifier: str) -> bool:
    """Deterministically copy one bundled preset onto the property groups.

    Main-thread only (property writes). Returns False — and changes
    nothing — when the preset does not exist or the bundle failed to load.
    """
    preset = material_presets.preset_by_identifier(identifier)
    if preset is None:
        return False
    shell = preset.settings
    global _applying_preset
    _applying_preset = True
    try:
        material = settings.material
        material.model = shell.model
        material.surface_weight = shell.surface_weight
        material.stretch_resistance = shell.stretch_resistance
        material.sideways_response = shell.sideways_response
        material.bend_resistance = shell.bend_resistance
        material.stretch_limit_enabled = shell.stretch_limit_enabled
        material.maximum_stretch_percent = shell.maximum_stretch_percent
        settings.damping.shape_damping = shell.shape_damping
        settings.damping.fold_damping = shell.fold_damping
        settings.collision.surface_grip = shell.surface_grip
        settings.collision.collision_gap = shell.collision_gap
        settings.collision.surface_offset = shell.surface_offset
    finally:
        _applying_preset = False
    return True


def select_preset(settings, identifier: str) -> bool:
    """Apply a bundled preset and make it the visible selection atomically."""
    if not apply_preset(settings, identifier):
        return False
    global _applying_preset
    _applying_preset = True
    try:
        settings.material.preset = identifier
        _mark_dirty(settings.material)
    finally:
        _applying_preset = False
    return True


def mark_custom(settings) -> None:
    """Switch the visible preset to Custom without touching any value."""
    if _applying_preset or settings is None:
        return
    material = settings.material
    if material.preset != material_presets.PRESET_CUSTOM:
        material.preset = material_presets.PRESET_CUSTOM


def _on_preset_update(self, _context) -> None:
    _mark_dirty(self)
    if _applying_preset:
        return
    if self.preset == material_presets.PRESET_CUSTOM:
        return  # selecting Custom never alters the current values
    settings = _object_settings_of(self)
    if settings is not None:
        apply_preset(settings, self.preset)


def _on_material_value_update(self, _context) -> None:
    """Any manual edit of a preset-controlled value selects Custom."""
    _mark_dirty(self)
    mark_custom(_object_settings_of(self))


class CLOTHNEXT_PG_material_settings(bpy.types.PropertyGroup):
    """Cloth material — every field maps to a real PPF shell parameter."""

    preset: bpy.props.EnumProperty(
        name="Material Preset", items=PRESET_ITEMS, default=DEFAULT_PRESET,
        update=_on_preset_update,
        description="Calibrated PPF fabric presets bundled with Cloth NeXt; "
                    "editing any mapped value switches to Custom without "
                    "resetting anything")
    model: bpy.props.EnumProperty(
        name="Solver Model",
        items=((MODEL_FABRIC, "Fabric (Baraff-Witkin)",
                "Calibrated model used by the bundled PPF fabric presets"),
               (MODEL_SHAPE_PRESERVING, "Shape Preserving (ARAP)",
                "Advanced shape-preserving alternative")),
        default=MODEL_FABRIC, update=_on_material_value_update,
        description="Selects the mathematical shell material model. Fabric "
                    "(Baraff-Witkin) is the calibrated model used by the "
                    "bundled PPF fabric presets. Shape Preserving (ARAP) is "
                    "an advanced alternative. Technical PPF parameter: "
                    "model")
    surface_weight: bpy.props.FloatProperty(
        name="Surface Weight", default=1.0, min=0.01, soft_max=10.0,
        max=10000.0, precision=3, update=_on_material_value_update,
        description="Mass of the fabric per square meter. Higher values "
                    "give the cloth more inertia and make it react more "
                    "heavily, but do not directly make it stiffer. "
                    "Unit: kg/m². Technical PPF parameter: density")
    stretch_resistance: bpy.props.FloatProperty(
        name="Stretch Resistance", default=1000.0, min=0.0,
        soft_max=100000.0, max=1e9, precision=1,
        update=_on_material_value_update,
        description="Controls how strongly the fabric resists being pulled "
                    "longer. Lower values create softer, more stretchable "
                    "cloth. Higher values preserve its original size more "
                    "strongly. Sent directly as PPF's density-normalized "
                    "young-mod value")
    sideways_response: bpy.props.FloatProperty(
        name="Sideways Response", default=0.35, min=0.0, max=0.4999,
        precision=4, update=_on_material_value_update,
        description="Controls how strongly stretching in one direction "
                    "affects the fabric sideways. Lower values allow the "
                    "directions to stretch more independently. Higher "
                    "values make the fabric contract sideways more "
                    "strongly. Technical PPF parameter: poiss-rat")
    bend_resistance: bpy.props.FloatProperty(
        name="Bend Resistance", default=10.0, min=0.0, soft_max=100.0,
        precision=2, update=_on_material_value_update,
        description="Controls how easily the fabric bends and forms folds. "
                    "Lower values create soft, flowing folds. Higher "
                    "values create broader, stiffer folds and stronger "
                    "shape retention. Technical PPF parameter: bend")
    stretch_limit_enabled: bpy.props.BoolProperty(
        name="Stretch Limit", default=False,
        update=_on_material_value_update,
        description="Prevents the fabric from stretching beyond the "
                    "specified percentage. When disabled, Cloth NeXt sends "
                    "a strain-limit value of zero to PPF")
    maximum_stretch_percent: bpy.props.FloatProperty(
        name="Maximum Stretch", default=5.0, min=0.01, soft_max=20.0,
        max=100.0, precision=2, subtype="PERCENTAGE",
        update=_on_material_value_update,
        description="Maximum permitted extension beyond the original size. "
                    "A value of 5% allows approximately five percent "
                    "stretch. Converted to PPF's fractional strain-limit "
                    "value")


class CLOTHNEXT_PG_damping_settings(bpy.types.PropertyGroup):
    """Both values are stiffness-proportional Rayleigh damping (seconds)."""

    shape_damping: bpy.props.FloatProperty(
        name="Shape Damping", default=0.0, min=0.0, soft_max=0.1,
        precision=4, update=_on_material_value_update,
        description="Reduces oscillation caused by stretching and in-plane "
                    "deformation. Small values can calm jitter without "
                    "making the fabric visibly sluggish. Unit: seconds. "
                    "Technical PPF parameter: deformation-damping")
    fold_damping: bpy.props.FloatProperty(
        name="Fold Damping", default=0.0, min=0.0, soft_max=0.1,
        precision=4, update=_on_material_value_update,
        description="Reduces oscillation and flutter in folds and bending "
                    "motion. Small values can calm unstable folds. Unit: "
                    "seconds. Technical PPF parameter: bending-damping")


class CLOTHNEXT_PG_pressure_settings(bpy.types.PropertyGroup):
    enable_inflate: bpy.props.BoolProperty(
        name="Enable Pressure", default=False, update=_on_settings_update,
        description="Apply uniform pressure along the Cloth mesh surface "
                    "normals. Consistent normals and a closed mesh are "
                    "recommended for balloon-like results")
    inflate_pressure: bpy.props.FloatProperty(
        name="Pressure", default=0.0, min=0.0, soft_max=100.0, precision=3,
        update=_on_settings_update,
        description="Uniform pressure along the Cloth surface normals. "
                    "Technical PPF parameter: pressure")


class CLOTHNEXT_PG_solver_quality_settings(bpy.types.PropertyGroup):
    show_advanced: bpy.props.BoolProperty(
        name="Advanced Settings", default=False,
        description="Show the four numeric solver quality controls")
    time_step: bpy.props.FloatProperty(
        name="Time Step", default=DEFAULT_TIME_STEP,
        min=MIN_TIME_STEP, max=MAX_TIME_STEP, precision=5,
        update=_on_settings_update,
        description="Scene-wide solver time step in seconds; technical PPF parameter: dt")
    min_newton_steps: bpy.props.IntProperty(
        name="Minimum Newton Steps", default=DEFAULT_MIN_NEWTON_STEPS,
        min=MIN_NEWTON_STEPS, max=MAX_NEWTON_STEPS,
        update=_on_settings_update,
        description="Scene-wide minimum nonlinear solver steps; technical PPF parameter: min-newton-steps")
    cg_max_iter: bpy.props.IntProperty(
        name="PCG Max Iterations", default=DEFAULT_CG_MAX_ITER,
        min=MIN_CG_MAX_ITER, max=MAX_CG_MAX_ITER,
        update=_on_settings_update,
        description="Scene-wide PCG iteration limit; technical PPF parameter: cg-max-iter")
    cg_tol: bpy.props.FloatProperty(
        name="PCG Tolerance", default=DEFAULT_CG_TOL,
        min=MIN_CG_TOL, max=MAX_CG_TOL, precision=5,
        update=_on_settings_update,
        description="Scene-wide PCG convergence tolerance; technical PPF parameter: cg-tol")


class CLOTHNEXT_PG_collision_settings(bpy.types.PropertyGroup):
    """Contact values; on a Collider these are the STATIC group values."""

    enabled: bpy.props.BoolProperty(
        name="Enable Contact", default=True, update=_on_settings_update,
        description="Turns solver contact handling on or off for the whole "
                    "run. When disabled, PPF receives disable-contact and "
                    "the cloth falls through every obstacle. Technical PPF "
                    "parameter: scene disable-contact")
    surface_grip: bpy.props.FloatProperty(
        name="Surface Grip", default=0.5, min=0.0, max=1.0, precision=2,
        update=_on_material_value_update,
        description="Controls sliding friction at contacts. Lower values "
                    "slide more easily. Higher values grip more strongly. "
                    "Cloth NeXt currently uses PPF's Minimum friction "
                    "combination mode, so both touching surfaces need "
                    "sufficiently high values for a grippy result. "
                    "Technical PPF parameter: friction")
    collision_gap: bpy.props.FloatProperty(
        name="Collision Gap", default=0.001, min=0.0, soft_max=0.01,
        precision=4, update=_on_material_value_update,
        description="Distance before PPF's contact barrier begins "
                    "reacting. Larger values keep surfaces farther apart. "
                    "Excessive values can make the cloth appear to float. "
                    "Unit: Blender world units. Technical PPF parameter: "
                    "contact-gap")
    surface_offset: bpy.props.FloatProperty(
        name="Surface Offset", default=0.0, min=0.0, soft_max=0.03,
        precision=4, update=_on_material_value_update,
        description="Adds a collision skin around the surface. Use small "
                    "values to represent surface thickness without changing "
                    "the simulated mesh. Excessive values create visible "
                    "separation. Unit: Blender world units. Technical PPF "
                    "parameter: contact-offset")


class CLOTHNEXT_PG_rod_settings(bpy.types.PropertyGroup):
    linear_density: bpy.props.FloatProperty(name="Linear Density", default=1.0,
        min=0.01, max=10000.0, update=_on_settings_update,
        description="Mass per unit length; PPF density")
    stretch_resistance: bpy.props.FloatProperty(name="Stretch Resistance",
        default=10000.0, min=0.0, max=1e9, update=_on_settings_update,
        description="Density-normalized axial stiffness; PPF young-mod")
    bend_resistance: bpy.props.FloatProperty(name="Bend Resistance", default=10.0,
        min=0.0, max=1e9, update=_on_settings_update,
        description="Rod joint bending stiffness; PPF bend")
    length_factor: bpy.props.FloatProperty(name="Rest Length Scale", default=1.0,
        min=0.01, max=10.0, update=_on_settings_update,
        description="Scale applied to rod rest edge lengths; PPF length-factor")
    stretch_limit_percent: bpy.props.FloatProperty(name="Maximum Stretch",
        default=0.0, min=0.0, max=100.0, subtype="PERCENTAGE",
        update=_on_settings_update, description="Zero disables PPF strain-limit")


class CLOTHNEXT_PG_soft_body_settings(bpy.types.PropertyGroup):
    volume_density: bpy.props.FloatProperty(name="Volume Density", default=100.0,
        min=0.01, max=10000.0, update=_on_settings_update,
        description="Mass per unit volume; PPF density")
    stretch_resistance: bpy.props.FloatProperty(name="Elastic Stiffness",
        default=500.0, min=0.0, max=1e9, update=_on_settings_update,
        description="Density-normalized solid stiffness; PPF young-mod")
    poisson_ratio: bpy.props.FloatProperty(name="Poisson Ratio", default=0.35,
        min=0.0, max=0.4999, update=_on_settings_update,
        description="Solid lateral response; PPF poiss-rat")
    volume_scale: bpy.props.FloatProperty(name="Rest Volume Scale", default=1.0,
        min=0.01, max=10.0, update=_on_settings_update,
        description="Scale applied to solid rest volume; PPF shrink")
    tetrahedralizer: bpy.props.EnumProperty(name="Tetrahedralizer",
        items=(("FTETWILD", "fTetWild", "Robust automatic tetrahedralization"),
               ("TETGEN", "TetGen", "TetGen automatic tetrahedralization")),
        default="FTETWILD", update=_on_settings_update)


class CLOTHNEXT_PG_force_settings(bpy.types.PropertyGroup):
    force_type: bpy.props.EnumProperty(
        name="Force Type", default="GRAVITY", update=_on_settings_update,
        items=(("GRAVITY", "Gravity", "Acceleration along the Empty's local -Z axis"),
               ("WIND", "Wind", "PPF wind vector along the Empty's local +Z axis"),
               ("AIR_DENSITY", "Air Density", "PPF air density used for aerodynamic forces"),
               ("AIR_FRICTION", "Air Friction", "PPF tangential air friction used for drag and lift"),
               ("VERTEX_AIR_DAMP", "Vertex Air Damping", "PPF isotropic per-vertex air damping")))
    strength: bpy.props.FloatProperty(
        name="Strength", default=9.81, min=0.0, soft_max=50.0,
        precision=3, update=_on_settings_update,
        description="PPF vector magnitude in Blender-space units; rotate the Empty to set direction")
    air_density: bpy.props.FloatProperty(
        name="Air Density", default=0.001, min=0.0, soft_max=2.0,
        precision=4, update=_on_settings_update,
        description="PPF air-density coefficient for drag and lift")
    air_friction: bpy.props.FloatProperty(
        name="Air Friction", default=0.2, min=0.0, soft_max=2.0,
        precision=4, update=_on_settings_update,
        description="PPF tangential air-friction ratio")
    vertex_air_damp: bpy.props.FloatProperty(
        name="Vertex Air Damping", default=0.0, min=0.0, soft_max=2.0,
        precision=4, update=_on_settings_update,
        description="PPF isotropic-air-friction coefficient applied per vertex")


class CLOTHNEXT_PG_object_settings(bpy.types.PropertyGroup):
    """Phase 3B object-level Cloth NeXt settings."""

    enabled: bpy.props.BoolProperty(
        name="Enabled", default=False, update=_on_settings_update,
        description="Cloth NeXt is enabled on this object")
    role: bpy.props.EnumProperty(
        name="Object Role", items=ROLE_ITEMS, default=DEFAULT_ROLE,
        update=_on_settings_update,
        description="How Cloth NeXt treats this object in a simulation")
    collider_motion: bpy.props.EnumProperty(
        name="Collider Motion", default="STATIC", update=_on_settings_update,
        items=(
            ("STATIC", "Static", "Use the evaluated collider shape at Bake Start"),
            ("ANIMATED", "Animated", "Use the evaluated Blender animation "
             "during the bake. Collider topology must remain unchanged."),
        ),
        description="Choose whether this Collider stays fixed or follows its "
                    "evaluated Blender animation during the bake")
    collider_samples_per_frame: bpy.props.IntProperty(
        name="Motion Samples / Frame", default=8, min=2, max=32,
        update=_on_settings_update,
        description="Animated Collider samples per Blender frame. Increase "
                    "this for fast or strongly curved motion to prevent the "
                    "interpolated Collider from crossing the cloth")
    collider_proxy_enabled: bpy.props.BoolProperty(
        name="Use Experimental Proxy", default=False,
        update=_on_settings_update,
        description="Replace this logical Collider with its generated "
                    "low-poly simulation Proxy during Bake")
    collider_proxy_target_vertices: bpy.props.IntProperty(
        name="Target Vertices", default=12000, min=500, max=250000,
        update=_on_settings_update,
        description="Approximate vertex target for the generated "
                    "experimental Collider Proxy")
    collider_proxy_object: bpy.props.PointerProperty(
        name="Generated Proxy", type=bpy.types.Object,
        description="Generated low-poly Collider used in place of this source")
    collider_proxy_source: bpy.props.PointerProperty(
        name="Proxy Source", type=bpy.types.Object, options={"HIDDEN"},
        description="Dense source object followed by this generated Proxy")
    collider_proxy_source_vertices: bpy.props.IntProperty(
        name="Proxy Source Vertices", default=0, options={"HIDDEN"})
    collider_proxy_result_vertices: bpy.props.IntProperty(
        name="Proxy Result Vertices", default=0, options={"HIDDEN"})
    material: bpy.props.PointerProperty(type=CLOTHNEXT_PG_material_settings)
    damping: bpy.props.PointerProperty(type=CLOTHNEXT_PG_damping_settings)
    pressure: bpy.props.PointerProperty(type=CLOTHNEXT_PG_pressure_settings)
    collision: bpy.props.PointerProperty(type=CLOTHNEXT_PG_collision_settings)
    rod: bpy.props.PointerProperty(type=CLOTHNEXT_PG_rod_settings)
    soft_body: bpy.props.PointerProperty(type=CLOTHNEXT_PG_soft_body_settings)
    force: bpy.props.PointerProperty(type=CLOTHNEXT_PG_force_settings)
    pinning_enabled: bpy.props.BoolProperty(
        name="Enable Pinning", default=False, update=_on_settings_update,
        description="Hold vertices in the selected Blender vertex group at "
                    "their evaluated Bake Start positions")
    pin_group: bpy.props.StringProperty(
        name="Pin Group", default="", update=_on_settings_update,
        description="Vertex group on this Cloth object used for static hard Pinning")
    pin_mode: bpy.props.EnumProperty(
        name="Pin Mode", default="STATIC", update=_on_settings_update,
        items=(("STATIC","Static","Keep pinned vertices fixed at their evaluated positions on Bake Start."),
               ("FOLLOW_ANIMATION","Follow Animation","Make pinned vertices follow their evaluated Blender positions throughout the Bake range.")))
    bake_start: bpy.props.IntProperty(
        name="Bake Start", default=1, min=-1048574, max=1048574,
        update=_on_settings_update,
        description="First Blender frame captured into the solver cache")
    bake_end: bpy.props.IntProperty(
        name="Bake End", default=250, min=-1048574, max=1048574,
        update=_on_settings_update,
        description="Last Blender frame produced by the solver cache")
    cache_directory: bpy.props.StringProperty(
        name="Cache Directory", default="", subtype="DIR_PATH",
        update=_on_settings_update,
        description="Optional directory for this object's Cloth NeXt result")
    # The Bake fingerprint is stored in halves. The settings half can be
    # recomputed in a Panel.draw for free (no mesh access), so the UI can say
    # "stale — settings changed" with certainty. The geometry half can only be
    # confirmed by a full validation, so a draw may only ever report it as
    # unconfirmed — never as safely matching.
    baked_settings_fingerprint: bpy.props.StringProperty(
        name="Baked Settings Fingerprint", default="", options={"HIDDEN"},
        description="Settings fingerprint of the last completed solver "
                    "result; a mismatch marks the cached result as stale")
    baked_geometry_fingerprint: bpy.props.StringProperty(
        name="Baked Geometry Fingerprint", default="", options={"HIDDEN"},
        description="Topology and pin-index fingerprint of the last completed "
                    "solver result; only a full validation can confirm it")
    baked_fingerprint_version: bpy.props.IntProperty(
        name="Baked Fingerprint Version", default=0, options={"HIDDEN"},
        description="Internal fingerprint schema of the stored result. Zero "
                    "marks a legacy result from before the split fingerprint; "
                    "it is treated as needing validation, never as matching")
    baked_cache_condition: bpy.props.StringProperty(
        name="Baked Cache Condition", default="", options={"HIDDEN"},
        description="Last authenticated on-disk cache condition")
    baked_cache_message: bpy.props.StringProperty(
        name="Baked Cache Message", default="", options={"HIDDEN"},
        description="Actionable result of the last cache integrity check")
    baked_metadata_digest: bpy.props.StringProperty(
        name="Baked Metadata Digest", default="", options={"HIDDEN"},
        description="Authenticated digest of the current cache sidecar")


# ---------------------------------------------------------------------------
# Blender-to-pure snapshot (main thread only; raises
# MaterialValidationError with property, value, range, and remedy).

def shell_settings_from(settings) -> ShellMaterialSettings:
    """Freeze the cloth object's properties into the pure material model."""
    material = settings.material
    damping = settings.damping
    collision = settings.collision
    return ShellMaterialSettings(
        model=str(material.model),
        surface_weight=float(material.surface_weight),
        stretch_resistance=float(material.stretch_resistance),
        sideways_response=float(material.sideways_response),
        bend_resistance=float(material.bend_resistance),
        shape_damping=float(damping.shape_damping),
        fold_damping=float(damping.fold_damping),
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset),
        stretch_limit_enabled=bool(material.stretch_limit_enabled),
        maximum_stretch_percent=float(material.maximum_stretch_percent),
        enable_inflate=bool(settings.pressure.enable_inflate),
        inflate_pressure=float(settings.pressure.inflate_pressure))


def solver_quality_from(scene) -> SolverQualitySettings:
    quality = getattr(scene, "cloth_next_quality", None)
    if quality is None:
        return SolverQualitySettings()
    return SolverQualitySettings(
        time_step=float(quality.time_step),
        min_newton_steps=int(quality.min_newton_steps),
        cg_max_iter=int(quality.cg_max_iter),
        cg_tol=float(quality.cg_tol))


def static_settings_from(settings) -> StaticMaterialSettings:
    """Freeze the collider object's contact properties."""
    collision = settings.collision
    return StaticMaterialSettings(
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset))


def rod_settings_from(settings) -> RodMaterialSettings:
    rod, damping, collision = settings.rod, settings.damping, settings.collision
    return RodMaterialSettings(
        linear_density=float(rod.linear_density),
        stretch_resistance=float(rod.stretch_resistance),
        bend_resistance=float(rod.bend_resistance),
        length_factor=float(rod.length_factor),
        shape_damping=float(damping.shape_damping),
        bend_damping=float(damping.fold_damping),
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset),
        stretch_limit=float(rod.stretch_limit_percent) / 100.0)


def soft_body_settings_from(settings) -> SoftBodyMaterialSettings:
    soft, damping, collision = (settings.soft_body, settings.damping,
                                settings.collision)
    return SoftBodyMaterialSettings(
        volume_density=float(soft.volume_density),
        stretch_resistance=float(soft.stretch_resistance),
        poisson_ratio=float(soft.poisson_ratio),
        volume_scale=float(soft.volume_scale),
        shape_damping=float(damping.shape_damping),
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset),
        tetrahedralizer=str(soft.tetrahedralizer).lower())


def reset_settings(settings) -> None:
    """Reset object-level settings to safe defaults.

    Touches only Cloth NeXt state; never modifiers, vertex groups,
    materials, caches, or files.
    """
    settings.enabled = False
    settings.role = DEFAULT_ROLE
    settings.collider_motion = "STATIC"
    settings.collider_samples_per_frame = 8
    settings.collider_proxy_enabled = False
    owner = getattr(settings, "id_data", None)
    if owner is not None:
        validation_state.forget(owner)


def attach_to_object() -> None:
    """Attach the settings to every object; requires the class registered."""
    bpy.types.Object.cloth_next = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_object_settings)
    bpy.types.Scene.cloth_next_quality = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_solver_quality_settings)


def detach_from_object() -> None:
    if hasattr(bpy.types.Scene, "cloth_next_quality"):
        del bpy.types.Scene.cloth_next_quality
    if hasattr(bpy.types.Object, "cloth_next"):
        del bpy.types.Object.cloth_next


CLASSES = (CLOTHNEXT_PG_material_settings, CLOTHNEXT_PG_damping_settings,
           CLOTHNEXT_PG_pressure_settings,
           CLOTHNEXT_PG_collision_settings,
           CLOTHNEXT_PG_rod_settings, CLOTHNEXT_PG_soft_body_settings,
           CLOTHNEXT_PG_force_settings,
           CLOTHNEXT_PG_solver_quality_settings,
           CLOTHNEXT_PG_object_settings)
