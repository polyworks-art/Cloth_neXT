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
    DEFAULT_CCD_MAX_ITER,
    DEFAULT_CCD_REDUCTION,
    DEFAULT_MAX_NEWTON_STEPS, DEFAULT_MAX_DX, DEFAULT_EIGENANALYSIS_EPS,
    DEFAULT_FRICTION_EPS, DEFAULT_CSRMAT_MAX_NNZ, DEFAULT_CONTACT_BARRIER,
    DEFAULT_CONSTRAINT_GHAT,
    DEFAULT_CONSTRAINT_TOL,
    DEFAULT_LINE_SEARCH_MAX_T,
    DEFAULT_MIN_NEWTON_STEPS,
    DEFAULT_TIME_STEP,
    DEFAULT_TARGET_TOI,
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
                                     SoftBodyMaterialSettings,
                                     RigidBodyMaterialSettings)
from . import icon_registry, validation_state, viewport_colors

ROLE_ITEMS = (
    ("CLOTH", "Cloth", "Simulate this object as cloth"),
    ("ROD", "Cable / Rope",
     "Simulate this Curve as a one-dimensional cable or rope"),
    ("SOFT_BODY", "Soft Body", "Simulate this closed mesh as a tetrahedral solid"),
    ("RIGID_BODY", "Rigid Body", "Simulate this closed mesh as a solid moving object"),
    ("COLLIDER", "Collider", "Use this object as a collision obstacle"),
    ("FORCE", "Force",
     "Configure scene-wide gravity, wind, and aerodynamic forces from an Empty"),
)

ROLE_ICONS = {
    "CLOTH": ("cloth", "MOD_CLOTH"),
    "ROD": ("rod", "CURVE_DATA"),
    "SOFT_BODY": ("soft_body", "MOD_SOFT"),
    "RIGID_BODY": ("physical", "MESH_CUBE"),
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
    owner = getattr(self, "id_data", None)
    settings = getattr(owner, "cloth_next", None)
    # Blender may return a fresh Python wrapper for the same RNA PropertyGroup,
    # so object identity with ``self`` is not stable. Scene settings do not
    # expose the object-level enabled/role pair and are intentionally skipped.
    if (settings is not None and hasattr(settings, "enabled")
            and hasattr(settings, "role")):
        viewport_colors.apply_object(owner)


def _on_collider_proxy_type_update(self, _context) -> None:
    """Changing strategy invalidates the generated proxy from the other mode."""
    self.collider_proxy_enabled = False
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
        material.stretch_plasticity_enabled = shell.stretch_plasticity_enabled
        material.stretch_plasticity_rate = shell.stretch_plasticity_rate
        material.stretch_plasticity_threshold_percent = \
            shell.stretch_plasticity_threshold_percent
        material.bend_plasticity_enabled = shell.bend_plasticity_enabled
        material.bend_plasticity_rate = shell.bend_plasticity_rate
        material.bend_plasticity_threshold_degrees = \
            shell.bend_plasticity_threshold_degrees
        material.bend_rest_from_geometry = shell.bend_rest_from_geometry
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
        description="Choose a fabric starting point. Editing a material value "
                    "switches to Custom without resetting your settings")
    model: bpy.props.EnumProperty(
        name="Solver Model",
        items=((MODEL_FABRIC, "Fabric (Baraff-Witkin)",
                "Calibrated model used by the bundled PPF fabric presets"),
               (MODEL_SHAPE_PRESERVING, "Shape Preserving (ARAP)",
                "Advanced shape-preserving alternative")),
        default=MODEL_FABRIC, update=_on_material_value_update,
        description="Choose how the surface behaves. Fabric (Baraff-Witkin) is "
                    "recommended for cloth; Shape Preserving (ARAP) retains the "
                    "original form more strongly. Technical PPF parameter: model")
    surface_weight: bpy.props.FloatProperty(
        name="Surface Weight", default=1.0, min=0.01, soft_max=10.0,
        max=10000.0, precision=3, update=_on_material_value_update,
        description="Mass of the fabric per square meter. Higher values "
                    "give the cloth more inertia and make it react more "
                    "heavily, but do not directly make it stiffer. Unit: kg/m². "
                    "Technical PPF parameter: density")
    stretch_resistance: bpy.props.FloatProperty(
        name="Stretch Resistance", default=1000.0, min=0.0,
        soft_max=100000.0, max=1e9, precision=1,
        update=_on_material_value_update,
        description="Controls how strongly the fabric resists being pulled "
                    "longer. Lower values create softer, more stretchable "
                    "cloth. Higher values preserve its original size more strongly. "
                    "Technical PPF parameter: density-normalized young-mod")
    sideways_response: bpy.props.FloatProperty(
        name="Sideways Response", default=0.35, min=0.0, max=0.4999,
        precision=4, update=_on_material_value_update,
        description="Controls how strongly stretching in one direction "
                    "affects the fabric sideways. Lower values allow the "
                    "directions to stretch more independently. Higher "
                    "values make the fabric contract sideways more strongly. "
                    "Technical PPF parameter: poiss-rat")
    bend_resistance: bpy.props.FloatProperty(
        name="Bend Resistance", default=10.0, min=0.0, soft_max=100.0,
        precision=2, update=_on_material_value_update,
        description="Controls how easily the fabric bends and forms folds. "
                    "Lower values create soft, flowing folds. Higher "
                    "values create broader, stiffer folds and stronger shape retention. "
                    "Technical PPF parameter: bend")
    stretch_plasticity_enabled: bpy.props.BoolProperty(
        name="Permanent Stretch", default=False,
        update=_on_material_value_update,
        description="Allow sustained stretching or compression to permanently "
                    "change the Cloth rest shape")
    stretch_plasticity_rate: bpy.props.FloatProperty(
        name="Stretch Creep Rate", default=1.0, min=0.0, soft_max=10.0,
        precision=3, update=_on_material_value_update,
        description="Speed per second at which overstretched Cloth adopts its "
                    "current shape. Technical PPF parameter: plasticity")
    stretch_plasticity_threshold_percent: bpy.props.FloatProperty(
        name="Stretch Threshold", default=5.0, min=0.0, soft_max=25.0,
        precision=2, subtype="PERCENTAGE", update=_on_material_value_update,
        description="Stretch or compression required before permanent "
                    "deformation begins. Technical PPF parameter: "
                    "plasticity-threshold")
    bend_plasticity_enabled: bpy.props.BoolProperty(
        name="Permanent Bends", default=False,
        update=_on_material_value_update,
        description="Allow folds held under load to become part of the Cloth "
                    "rest shape")
    bend_plasticity_rate: bpy.props.FloatProperty(
        name="Bend Creep Rate", default=1.0, min=0.0, soft_max=10.0,
        precision=3, update=_on_material_value_update,
        description="Speed per second at which folds become permanent. "
                    "Technical PPF parameter: bend-plasticity")
    bend_plasticity_threshold_degrees: bpy.props.FloatProperty(
        name="Bend Threshold", default=10.0, min=0.0, max=180.0,
        soft_max=90.0, precision=2,
        update=_on_material_value_update,
        description="Angular deviation required before a fold becomes "
                    "permanent. Technical PPF parameter: "
                    "bend-plasticity-threshold")
    bend_rest_from_geometry: bpy.props.BoolProperty(
        name="Use Initial Bend Shape", default=True,
        update=_on_material_value_update,
        description="Use the mesh's starting folds as its bend rest shape. "
                    "Disable for an initially flat rest shape")
    stretch_limit_enabled: bpy.props.BoolProperty(
        name="Stretch Limit", default=False,
        update=_on_material_value_update,
        description="Prevents the fabric from stretching beyond the "
                    "specified percentage. Disable it for unrestricted stretch. "
                    "Technical PPF parameter: strain-limit")
    maximum_stretch_percent: bpy.props.FloatProperty(
        name="Maximum Stretch", default=5.0, min=0.01, soft_max=20.0,
        max=100.0, precision=2, subtype="PERCENTAGE",
        update=_on_material_value_update,
        description="Maximum permitted extension beyond the original size. "
                    "A value of 5% allows approximately five percent stretch. "
                    "Technical PPF parameter: strain-limit")


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
                    "motion. Small values can calm unstable folds. Unit: seconds. "
                    "Technical PPF parameter: bending-damping")


class CLOTHNEXT_PG_pressure_settings(bpy.types.PropertyGroup):
    enable_inflate: bpy.props.BoolProperty(
        name="Enable Pressure", default=False, update=_on_settings_update,
        description="Apply uniform pressure along the Cloth mesh surface "
                    "normals. Consistent normals and a closed mesh are "
                    "recommended for balloon-like results")
    inflate_pressure: bpy.props.FloatProperty(
        name="Pressure", default=0.0, min=0.0, soft_max=100.0, precision=3,
        update=_on_settings_update,
        description="Uniform outward pressure along the Cloth surface normals")
    shrink_percent: bpy.props.FloatProperty(
        name="Shrink", default=0.0, min=0.0, max=90.0, soft_max=25.0,
        precision=2, subtype="PERCENTAGE", update=_on_settings_update,
        description="Physically contract the Cloth rest shape uniformly. "
                    "For example, 5% sets both warp and weft rest lengths "
                    "to 95%. This is not object or geometry scaling. The "
                    "solver applies the target from the start of the Bake")
    sewing_enabled: bpy.props.BoolProperty(
        name="Sewing", default=False, update=_on_settings_update,
        description="Treat edges that are not used by any face as Sewing "
                    "springs and pull their endpoints together")
    sewing_stiffness: bpy.props.FloatProperty(
        name="Sewing Strength", default=1.0, min=0.0, soft_max=10.0,
        precision=3, update=_on_settings_update,
        description="PPF stiffness for Sewing edges. Higher values close "
                    "seams more strongly against gravity and collisions")


class CLOTHNEXT_PG_solver_quality_settings(bpy.types.PropertyGroup):
    show_advanced: bpy.props.BoolProperty(
        name="Advanced Settings", default=False,
        description="Show the four numeric solver quality controls")
    time_step: bpy.props.FloatProperty(
        name="Time Step", default=DEFAULT_TIME_STEP,
        min=MIN_TIME_STEP, max=MAX_TIME_STEP, precision=5,
        update=_on_settings_update,
        description="Simulation step size in seconds. Smaller values can improve "
                    "stability but take longer to calculate")
    min_newton_steps: bpy.props.IntProperty(
        name="Minimum Newton Steps", default=DEFAULT_MIN_NEWTON_STEPS,
        min=MIN_NEWTON_STEPS, max=MAX_NEWTON_STEPS,
        update=_on_settings_update,
        description="Minimum correction passes per simulation step. Increase only "
                    "when a difficult scene does not converge")
    cg_max_iter: bpy.props.IntProperty(
        name="PCG Max Iterations", default=DEFAULT_CG_MAX_ITER,
        min=MIN_CG_MAX_ITER, max=MAX_CG_MAX_ITER,
        update=_on_settings_update,
        description="Maximum internal linear-solver iterations. Higher values may "
                    "help difficult scenes but increase bake time")
    cg_tol: bpy.props.FloatProperty(
        name="PCG Tolerance", default=DEFAULT_CG_TOL,
        min=MIN_CG_TOL, max=MAX_CG_TOL, precision=5,
        update=_on_settings_update,
        description="Linear-solver accuracy target. Lower values are stricter and "
                    "may increase bake time")
    target_toi: bpy.props.FloatProperty(
        name="Contact Completion", default=DEFAULT_TARGET_TOI,
        min=1e-6, max=1.0, precision=4, update=_on_settings_update,
        description="How much safe collision progress is required before a "
                    "contact step may finish. Lower is more conservative. "
                    "Technical PPF parameter: target-toi")
    line_search_max_t: bpy.props.FloatProperty(
        name="Motion Safety Margin", default=DEFAULT_LINE_SEARCH_MAX_T,
        min=1.0, max=10.0, precision=3, update=_on_settings_update,
        description="Extra motion range checked to keep fast contact stable. "
                    "Technical PPF parameter: line-search-max-t")
    ccd_max_iter: bpy.props.IntProperty(
        name="Collision Search Limit", default=DEFAULT_CCD_MAX_ITER,
        min=1, max=100000, update=_on_settings_update,
        description="Maximum collision-search effort for difficult or fast "
                    "contact. Technical PPF parameter: ccd-max-iter")
    constraint_ghat: bpy.props.FloatProperty(
        name="Constraint Contact Distance", default=DEFAULT_CONSTRAINT_GHAT,
        min=1e-8, soft_max=0.01, max=1.0, precision=6,
        update=_on_settings_update,
        description="Distance at which animated Pins and other moving "
                    "constraints begin avoiding contact. Technical PPF "
                    "parameter: constraint-ghat")
    constraint_tol: bpy.props.FloatProperty(
        name="Moving Constraint Precision", default=DEFAULT_CONSTRAINT_TOL,
        min=1e-6, max=1.0, precision=5, update=_on_settings_update,
        description="Collision precision for moving constraints as a fraction "
                    "of Constraint Contact Distance. Lower is stricter. "
                    "Technical PPF parameter: constraint-tol")
    ccd_reduction: bpy.props.FloatProperty(
        name="Collision Detection Threshold", default=DEFAULT_CCD_REDUCTION,
        min=1e-6, max=1.0, precision=5, update=_on_settings_update,
        description="Fraction of the initial surface gap used to detect an "
                    "approaching collision. Lower is more conservative. "
                    "Technical PPF parameter: ccd-reduction")
    max_newton_steps: bpy.props.IntProperty(
        name="Contact Iteration Limit", default=DEFAULT_MAX_NEWTON_STEPS,
        min=1, max=100000, update=_on_settings_update,
        description="Maximum nonlinear correction passes before a difficult contact is reported as failed. Technical PPF parameter: max-newton-steps")
    max_dx: bpy.props.FloatProperty(
        name="Maximum Contact Correction", default=DEFAULT_MAX_DX,
        min=1e-6, max=1000.0, precision=5, update=_on_settings_update,
        description="Largest correction distance allowed during one contact solve. Technical PPF parameter: max-dx")
    eigenanalysis_eps: bpy.props.FloatProperty(
        name="Contact Stability Threshold", default=DEFAULT_EIGENANALYSIS_EPS,
        min=1e-10, max=1.0, precision=7, update=_on_settings_update,
        description="Numerical threshold used to stabilize nearly singular contact directions. Technical PPF parameter: eiganalysis-eps")
    friction_eps: bpy.props.FloatProperty(
        name="Friction Stability Threshold", default=DEFAULT_FRICTION_EPS,
        min=1e-10, max=1.0, precision=8, update=_on_settings_update,
        description="Numerical threshold that stabilizes very slow friction motion. Technical PPF parameter: friction-eps")
    csrmat_max_nnz: bpy.props.IntProperty(
        name="Contact Capacity", default=DEFAULT_CSRMAT_MAX_NNZ,
        min=1000, max=1_000_000_000, update=_on_settings_update,
        description="Preallocated GPU contact entries. Too low can stop dense contact; too high consumes GPU memory. Technical PPF parameter: csrmat-max-nnz")
    contact_barrier: bpy.props.EnumProperty(
        name="Contact Response Model", default=DEFAULT_CONTACT_BARRIER,
        update=_on_settings_update,
        items=(("cubic", "Smooth", "Cubic contact barrier; recommended default"),
               ("quad", "Firm", "Quadratic contact barrier"),
               ("log", "Sharp", "Logarithmic contact barrier")),
        description="Mathematical response used as surfaces approach contact. Technical PPF parameter: barrier")


class CLOTHNEXT_PG_recovery_settings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="Enable Recovery", default=True,
        description="Keep verified solver checkpoints so an interrupted Bake "
                    "can continue after restarting Blender")
    auto_save: bpy.props.BoolProperty(
        name="Auto Save Checkpoints", default=True,
        description="Ask the solver to save resumable states periodically")
    checkpoint_interval: bpy.props.IntProperty(
        name="Checkpoint Interval", default=20, min=1, max=10000,
        description="Number of solver frames between automatic checkpoints")
    keep_saved_states: bpy.props.IntProperty(
        name="Keep Saved States", default=3, min=1, max=100,
        description="Maximum number of verified checkpoints retained")
    save_on_cancel: bpy.props.BoolProperty(
        name="Save State on Cancel", default=True,
        description="Request save_and_quit and preserve the confirmed solver "
                    "project when cancelling")
    save_on_finish: bpy.props.BoolProperty(
        name="Save State on Finish", default=False,
        description="Ask the solver to save a final state before completing")
    # Draw-time UI reads only this cached snapshot. Operators and Bake start
    # refresh it; Panel.draw never scans files or contacts the solver.
    status: bpy.props.StringProperty(default="", options={"HIDDEN"})
    status_detail: bpy.props.StringProperty(default="", options={"HIDDEN"})
    compatible: bpy.props.BoolProperty(default=False, options={"HIDDEN"})
    resumable: bpy.props.BoolProperty(default=False, options={"HIDDEN"})
    latest_checkpoint_frame: bpy.props.IntProperty(
        default=0, options={"HIDDEN"})
    checkpoint_count: bpy.props.IntProperty(default=0, options={"HIDDEN"})
    older_checkpoint_preserved: bpy.props.BoolProperty(
        default=False, options={"HIDDEN"})
    recovery_directory: bpy.props.StringProperty(default="", options={"HIDDEN"})
    resume_requested: bpy.props.BoolProperty(default=False, options={"HIDDEN"})


class CLOTHNEXT_PG_collision_settings(bpy.types.PropertyGroup):
    """Contact values; on a Collider these are the STATIC group values."""

    enabled: bpy.props.BoolProperty(
        name="Enable Contact", default=True, update=_on_settings_update,
        description="Enable collisions for the simulation. When disabled, "
                    "deformable objects can pass through colliders")
    surface_grip: bpy.props.FloatProperty(
        name="Friction", default=0.5, min=0.0, max=1.0, precision=2,
        update=_on_material_value_update,
        description="Controls how easily touching surfaces slide. Lower values "
                    "are slippery; higher values resist sliding. Minimum mode "
                    "uses the lower Friction value of the two touching objects. "
                    "Technical PPF parameter: friction")
    collision_gap: bpy.props.FloatProperty(
        name="Collision Gap", default=0.001, min=0.0, soft_max=0.01,
        precision=4, update=_on_material_value_update,
        description="Distance at which collision response begins. Larger values "
                    "keep surfaces farther apart. "
                    "Excessive values can make the cloth appear to float. "
                    "Unit: Blender world units. Technical PPF parameter: contact-gap")
    surface_offset: bpy.props.FloatProperty(
        name="Surface Offset", default=0.0, min=0.0, soft_max=0.03,
        precision=4, update=_on_material_value_update,
        description="Adds a collision skin around the surface. Use small "
                    "values to represent surface thickness without changing "
                    "the simulated mesh. Excessive values create visible "
                    "separation. Unit: Blender world units. Technical PPF parameter: "
                    "contact-offset")


class CLOTHNEXT_PG_friction_region(bpy.types.PropertyGroup):
    vertex_group: bpy.props.StringProperty(
        name="Vertex Group", default="", update=_on_settings_update,
        description="Vertices whose weights blend from the general Friction "
                    "value to this region's Friction")
    friction: bpy.props.FloatProperty(
        name="Friction", default=0.5, min=0.0, max=1.0, precision=2,
        update=_on_settings_update,
        description="Target friction at full weight in this Vertex Group")


class CLOTHNEXT_PG_soft_constraint(bpy.types.PropertyGroup):
    target: bpy.props.PointerProperty(
        name="Target", type=bpy.types.Object, update=_on_settings_update,
        description="Animated object that drives this Pin motion constraint")
    constraint_type: bpy.props.EnumProperty(
        name="Constraint", default="LOCATION", update=_on_settings_update,
        items=(("LOCATION", "Location", "Follow the Target translation"),
               ("ROTATION", "Rotation", "Follow the Target rotation"),
               ("SCALE", "Scale", "Follow the Target scale")))
    strength: bpy.props.FloatProperty(
        name="Strength", default=1.0, min=0.0, soft_max=1000.0,
        precision=3, update=_on_settings_update,
        description="How strongly this constraint pulls toward its Target")


class CLOTHNEXT_PG_advanced_pin_target(bpy.types.PropertyGroup):
    vertex_group: bpy.props.StringProperty(
        name="Pin Group", default="", update=_on_settings_update,
        description="Vertex Group whose vertices follow this Target")
    target: bpy.props.PointerProperty(
        name="Target", type=bpy.types.Object, update=_on_settings_update,
        description="Animated Empty or object followed by this Pin Group")
    strength: bpy.props.FloatProperty(
        name="Strength", default=1.0, min=1e-6, soft_max=1000.0,
        precision=3, update=_on_settings_update,
        description="Soft Pull strength for this Pin Group")


class CLOTHNEXT_PG_motion_override(bpy.types.PropertyGroup):
    frame: bpy.props.IntProperty(
        name="Frame", default=1, min=-1048574, max=1048574,
        update=_on_settings_update,
        description="Blender frame at which this motion replaces the current velocity")
    motion_type: bpy.props.EnumProperty(
        name="Motion", default="LINEAR", update=_on_settings_update,
        items=(("LINEAR", "Move", "Set a world-space linear velocity"),
               ("ANGULAR", "Spin", "Set a world-space angular velocity")))
    velocity: bpy.props.FloatVectorProperty(
        name="Velocity", size=3, default=(0.0, 0.0, 0.0),
        subtype="VELOCITY", update=_on_settings_update,
        description="World-space speed in Blender units per second")
    angular_velocity: bpy.props.FloatVectorProperty(
        name="Spin", size=3, default=(0.0, 0.0, 0.0),
        subtype="EULER", unit="ROTATION", update=_on_settings_update,
        description="World-space spin vector in radians per second")


class CLOTHNEXT_PG_rod_settings(bpy.types.PropertyGroup):
    linear_density: bpy.props.FloatProperty(name="Linear Density", default=1.0,
        min=0.01, max=10000.0, update=_on_settings_update,
        description="Mass per unit length. Higher values make the cable feel heavier")
    stretch_resistance: bpy.props.FloatProperty(name="Stretch Resistance",
        default=10000.0, min=0.0, max=1e9, update=_on_settings_update,
        description="Resistance to lengthwise stretching. Higher values preserve length")
    bend_resistance: bpy.props.FloatProperty(name="Bend Resistance", default=10.0,
        min=0.0, max=1e9, update=_on_settings_update,
        description="Resistance to bending. Lower values create flexible cables")
    length_factor: bpy.props.FloatProperty(name="Rest Length Scale", default=1.0,
        min=0.01, max=10.0, update=_on_settings_update,
        description="Scale the cable's resting length. Values below 1 contract it; "
                    "values above 1 make it longer")
    stretch_limit_percent: bpy.props.FloatProperty(name="Maximum Stretch",
        default=0.0, min=0.0, max=100.0, subtype="PERCENTAGE",
        update=_on_settings_update,
        description="Maximum lengthwise stretch. Set to 0% to disable the limit")
    bend_plasticity_enabled: bpy.props.BoolProperty(
        name="Permanent Bends", default=False, update=_on_settings_update,
        description="Allow bends held under load to become the cable's rest shape")
    bend_plasticity_rate: bpy.props.FloatProperty(
        name="Bend Creep Rate", default=1.0, min=0.0, soft_max=10.0,
        precision=3, update=_on_settings_update,
        description="Speed per second at which cable bends become permanent")
    bend_plasticity_threshold_degrees: bpy.props.FloatProperty(
        name="Bend Threshold", default=10.0, min=0.0, max=180.0,
        soft_max=90.0, precision=2,
        update=_on_settings_update,
        description="Angular deviation required before a bend becomes permanent")
    bend_rest_from_geometry: bpy.props.BoolProperty(
        name="Use Initial Bend Shape", default=True,
        update=_on_settings_update,
        description="Use the cable's starting bends as its rest shape. "
                    "Disable for an initially straight rest shape")


class CLOTHNEXT_PG_soft_body_settings(bpy.types.PropertyGroup):
    volume_density: bpy.props.FloatProperty(name="Volume Density", default=100.0,
        min=0.01, max=10000.0, update=_on_settings_update,
        description="Mass per unit volume. Higher values make the object feel heavier")
    stretch_resistance: bpy.props.FloatProperty(name="Elastic Stiffness",
        default=500.0, min=0.0, max=1e9, update=_on_settings_update,
        description="Overall resistance to deformation. Higher values retain the "
                    "original shape more strongly")
    poisson_ratio: bpy.props.FloatProperty(name="Poisson Ratio", default=0.35,
        min=0.0, max=0.4999, update=_on_settings_update,
        description="Controls how much the object expands sideways when compressed. "
                    "Values near 0.5 preserve volume more strongly")
    volume_scale: bpy.props.FloatProperty(name="Rest Volume Scale", default=1.0,
        min=0.01, max=10.0, update=_on_settings_update,
        description="Scale the resting volume. Values below 1 shrink the object; "
                    "values above 1 expand it")
    tetrahedralizer: bpy.props.EnumProperty(name="Tetrahedralizer",
        items=(("FTETWILD", "fTetWild", "Robust automatic tetrahedralization"),
               ("TETGEN", "TetGen", "TetGen automatic tetrahedralization")),
        default="FTETWILD", update=_on_settings_update)
    stretch_plasticity_enabled: bpy.props.BoolProperty(
        name="Permanent Deformation", default=False,
        update=_on_settings_update,
        description="Allow sustained deformation to change the solid rest shape")
    stretch_plasticity_rate: bpy.props.FloatProperty(
        name="Creep Rate", default=1.0, min=0.0, soft_max=10.0,
        precision=3, update=_on_settings_update,
        description="Speed per second at which the solid adopts its deformed shape")
    stretch_plasticity_threshold_percent: bpy.props.FloatProperty(
        name="Deformation Threshold", default=5.0, min=0.0,
        soft_max=25.0, precision=2, subtype="PERCENTAGE",
        update=_on_settings_update,
        description="Deformation required before the rest shape starts changing")


class CLOTHNEXT_PG_rigid_body_settings(bpy.types.PropertyGroup):
    volume_density: bpy.props.FloatProperty(
        name="Weight Density", default=100.0, min=0.01, max=10000.0,
        soft_max=1000.0, update=_on_settings_update,
        description="Weight per unit volume. Higher values give the object "
                    "more mass and make impacts feel heavier")


class CLOTHNEXT_PG_force_settings(bpy.types.PropertyGroup):
    # Kept for loading older files. The unified Force panel no longer exposes
    # or uses a single active type.
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
    gravity_strength: bpy.props.FloatProperty(
        name="Gravity", default=9.81, min=0.0, soft_max=50.0,
        precision=3, update=_on_settings_update,
        description="Gravity magnitude along the Empty's local -Z axis")
    wind_strength: bpy.props.FloatProperty(
        name="Wind", default=0.0, min=0.0, soft_max=50.0,
        precision=3, update=_on_settings_update,
        description="Wind magnitude along the Empty's local +Z axis")
    wind_variation: bpy.props.FloatProperty(
        name="Strength Variation", default=0.0, min=0.0, soft_max=10.0,
        precision=3, update=_on_settings_update,
        description="Maximum animated Wind strength variation above or below Wind; zero disables gusts")
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


class CLOTHNEXT_PG_solver_backend_settings(bpy.types.PropertyGroup):
    """Persistent scene-wide PPF quality selection."""
    quality_preset: bpy.props.EnumProperty(
        name="Quality", default="HIGH", update=_on_settings_update,
        items=(("LOW", "Low", "Fast setup bake"),
               ("MEDIUM", "Medium", "Balanced working quality"),
               ("HIGH", "High", "Final-quality simulation"),
               ("EXTREME", "Extreme", "Maximum solve effort"),
               ("CUSTOM", "Custom", "Backend-specific custom quality")))


class CLOTHNEXT_PG_object_settings(bpy.types.PropertyGroup):
    """Phase 3B object-level Cloth NeXt settings."""

    enabled: bpy.props.BoolProperty(
        name="Enabled", default=False, update=_on_settings_update,
        description="Cloth NeXt is enabled on this object")
    persistent_export_id: bpy.props.StringProperty(
        name="Persistent Export Identity", default="",
        options={"HIDDEN"},
        description="Stable internal identity used for deterministic exports")
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
    collider_capture_mode: bpy.props.EnumProperty(
        name="Animation Capture", default="AUTO", update=_on_settings_update,
        items=(
            ("AUTO", "Auto",
             "Use transform-only capture only when geometry is provably "
             "constant; otherwise use safe deforming capture"),
            ("TRANSFORM_ONLY", "Transform Only",
             "Export the mesh once and capture only object transforms"),
            ("DEFORMING", "Deforming",
             "Evaluate and capture the Collider mesh at every motion sample"),
        ),
        description="How animated Collider geometry is captured")
    collider_samples_per_frame: bpy.props.IntProperty(
        name="Motion Samples / Frame", default=8, min=1, max=32,
        update=_on_settings_update,
        description="Animated Collider samples per Blender frame. Increase "
                    "this for fast or strongly curved motion to prevent the "
                    "interpolated Collider from crossing the cloth")
    collider_proxy_enabled: bpy.props.BoolProperty(
        name="Use Experimental Proxy", default=False,
        update=_on_settings_update,
        description="Replace this logical Collider with its generated "
                    "simulation Proxy during Bake")
    collider_proxy_type: bpy.props.EnumProperty(
        name="Proxy Type", default="SIMPLE",
        update=_on_collider_proxy_type_update,
        items=(("SIMPLE", "Simple Proxy",
                "Use the existing reduced deforming Mesh proxy"),
               ("CHARACTER_CAGE", "Character Collision Cage",
                "Build conservative rigid bone hulls from the animated Character")),
        description="Choose the generated Collider proxy strategy")
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
    collider_cage_margin: bpy.props.FloatProperty(
        name="Cage Margin", default=0.003, min=0.0, soft_max=0.02,
        precision=4, update=_on_settings_update,
        description="Outward safety margin added to every bone hull in world units")
    collider_cage_joint_overlap: bpy.props.FloatProperty(
        name="Joint Overlap", default=0.01, min=0.0, soft_max=0.05,
        precision=4, update=_on_settings_update,
        description="Extend bone hulls along the bone axis so joints overlap")
    collider_cage_sample_step: bpy.props.IntProperty(
        name="Animation Sample Step", default=1, min=1, max=32,
        update=_on_settings_update,
        description="Frames between one-time Character evaluations while fitting the cage")
    collider_cage_weight_threshold: bpy.props.FloatProperty(
        name="Bone Weight Threshold", default=0.2, min=0.0, max=1.0,
        precision=2, update=_on_settings_update,
        description="Minimum skin weight for a Character vertex to contribute to a bone hull")
    collider_cage_min_vertices: bpy.props.IntProperty(
        name="Minimum Bone Vertices", default=24, min=4, max=10000,
        update=_on_settings_update,
        description="Ignore deform bones with fewer weighted Character vertices")
    material: bpy.props.PointerProperty(type=CLOTHNEXT_PG_material_settings)
    damping: bpy.props.PointerProperty(type=CLOTHNEXT_PG_damping_settings)
    pressure: bpy.props.PointerProperty(type=CLOTHNEXT_PG_pressure_settings)
    collision: bpy.props.PointerProperty(type=CLOTHNEXT_PG_collision_settings)
    friction_regions: bpy.props.CollectionProperty(
        type=CLOTHNEXT_PG_friction_region)
    friction_region_index: bpy.props.IntProperty(
        default=0, min=0, options={"HIDDEN"})
    soft_constraints: bpy.props.CollectionProperty(
        type=CLOTHNEXT_PG_soft_constraint)
    soft_constraint_index: bpy.props.IntProperty(
        default=0, min=0, options={"HIDDEN"})
    advanced_pin_targets: bpy.props.CollectionProperty(
        type=CLOTHNEXT_PG_advanced_pin_target)
    advanced_pin_target_index: bpy.props.IntProperty(
        default=0, min=0, options={"HIDDEN"})
    motion_overrides: bpy.props.CollectionProperty(
        type=CLOTHNEXT_PG_motion_override)
    motion_override_index: bpy.props.IntProperty(
        default=0, min=0, options={"HIDDEN"})
    rod: bpy.props.PointerProperty(type=CLOTHNEXT_PG_rod_settings)
    soft_body: bpy.props.PointerProperty(type=CLOTHNEXT_PG_soft_body_settings)
    rigid_body: bpy.props.PointerProperty(type=CLOTHNEXT_PG_rigid_body_settings)
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
    advanced_pin_motion_enabled: bpy.props.BoolProperty(
        name="Enable Advanced Pin Motion", default=False,
        update=_on_settings_update,
        description="Drive the Pin group with an animated Target while "
                    "preserving offsets and yielding to Collider contact")
    pin_target: bpy.props.PointerProperty(
        name="Target", type=bpy.types.Object, update=_on_settings_update,
        description="Animated Empty or object whose transform drives the Pin group")
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
    baked_solver_backend: bpy.props.StringProperty(
        name="Baked Solver Backend", default="", options={"HIDDEN"},
        description="Solver backend that produced the attached cache")
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
        stretch_plasticity_enabled=bool(
            material.stretch_plasticity_enabled),
        stretch_plasticity_rate=float(material.stretch_plasticity_rate),
        stretch_plasticity_threshold_percent=float(
            material.stretch_plasticity_threshold_percent),
        bend_plasticity_enabled=bool(material.bend_plasticity_enabled),
        bend_plasticity_rate=float(material.bend_plasticity_rate),
        bend_plasticity_threshold_degrees=float(
            material.bend_plasticity_threshold_degrees),
        bend_rest_from_geometry=bool(material.bend_rest_from_geometry),
        shape_damping=float(damping.shape_damping),
        fold_damping=float(damping.fold_damping),
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset),
        stretch_limit_enabled=bool(material.stretch_limit_enabled),
        maximum_stretch_percent=float(material.maximum_stretch_percent),
        enable_inflate=bool(settings.pressure.enable_inflate),
        inflate_pressure=float(settings.pressure.inflate_pressure),
        shrink_percent=float(settings.pressure.shrink_percent),
        sewing_enabled=bool(settings.pressure.sewing_enabled),
        sewing_stiffness=float(settings.pressure.sewing_stiffness))


def solver_quality_from(scene) -> SolverQualitySettings:
    quality = getattr(scene, "cloth_next_quality", None)
    if quality is None:
        return SolverQualitySettings()
    return SolverQualitySettings(
        time_step=float(quality.time_step),
        min_newton_steps=int(quality.min_newton_steps),
        cg_max_iter=int(quality.cg_max_iter),
        cg_tol=float(quality.cg_tol),
        target_toi=float(getattr(quality, "target_toi", DEFAULT_TARGET_TOI)),
        line_search_max_t=float(getattr(
            quality, "line_search_max_t", DEFAULT_LINE_SEARCH_MAX_T)),
        constraint_ghat=float(getattr(
            quality, "constraint_ghat", DEFAULT_CONSTRAINT_GHAT)),
        constraint_tol=float(getattr(
            quality, "constraint_tol", DEFAULT_CONSTRAINT_TOL)),
        ccd_reduction=float(getattr(
            quality, "ccd_reduction", DEFAULT_CCD_REDUCTION)),
        max_newton_steps=int(getattr(quality, "max_newton_steps", DEFAULT_MAX_NEWTON_STEPS)),
        max_dx=float(getattr(quality, "max_dx", DEFAULT_MAX_DX)),
        eigenanalysis_eps=float(getattr(quality, "eigenanalysis_eps", DEFAULT_EIGENANALYSIS_EPS)),
        friction_eps=float(getattr(quality, "friction_eps", DEFAULT_FRICTION_EPS)),
        csrmat_max_nnz=int(getattr(quality, "csrmat_max_nnz", DEFAULT_CSRMAT_MAX_NNZ)),
        contact_barrier=str(getattr(quality, "contact_barrier", DEFAULT_CONTACT_BARRIER)),
        ccd_max_iter=int(getattr(
            quality, "ccd_max_iter", DEFAULT_CCD_MAX_ITER)))


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
        stretch_limit=float(rod.stretch_limit_percent) / 100.0,
        bend_plasticity_rate=(float(rod.bend_plasticity_rate)
                              if rod.bend_plasticity_enabled else 0.0),
        bend_plasticity_threshold_degrees=float(
            rod.bend_plasticity_threshold_degrees),
        bend_rest_from_geometry=bool(rod.bend_rest_from_geometry))


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
        tetrahedralizer=str(soft.tetrahedralizer).lower(),
        stretch_plasticity_rate=(float(soft.stretch_plasticity_rate)
                                 if soft.stretch_plasticity_enabled else 0.0),
        stretch_plasticity_threshold_percent=float(
            soft.stretch_plasticity_threshold_percent))


def rigid_body_settings_from(settings) -> RigidBodyMaterialSettings:
    rigid, collision = settings.rigid_body, settings.collision
    return RigidBodyMaterialSettings(
        volume_density=float(rigid.volume_density),
        surface_grip=float(collision.surface_grip),
        collision_gap=float(collision.collision_gap),
        surface_offset=float(collision.surface_offset))


def reset_settings(settings) -> None:
    """Reset object-level settings to safe defaults.

    Touches only Cloth NeXt state; never modifiers, vertex groups,
    materials, caches, or files.
    """
    settings.enabled = False
    settings.role = DEFAULT_ROLE
    settings.collider_motion = "STATIC"
    settings.collider_capture_mode = "AUTO"
    settings.collider_samples_per_frame = 8
    settings.collider_proxy_enabled = False
    settings.collider_proxy_type = "SIMPLE"
    settings.collider_cage_margin = 0.003
    settings.collider_cage_joint_overlap = 0.01
    settings.collider_cage_sample_step = 1
    settings.collider_cage_weight_threshold = 0.2
    settings.collider_cage_min_vertices = 24
    owner = getattr(settings, "id_data", None)
    if owner is not None:
        validation_state.forget(owner)


def attach_to_object() -> None:
    """Attach the settings to every object; requires the class registered."""
    bpy.types.Object.cloth_next = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_object_settings)
    bpy.types.Scene.cloth_next_quality = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_solver_quality_settings)
    bpy.types.Scene.cloth_next_recovery = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_recovery_settings)
    bpy.types.Scene.cloth_next_solver = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_solver_backend_settings)


def detach_from_object() -> None:
    if hasattr(bpy.types.Scene, "cloth_next_solver"):
        del bpy.types.Scene.cloth_next_solver
    if hasattr(bpy.types.Scene, "cloth_next_recovery"):
        del bpy.types.Scene.cloth_next_recovery
    if hasattr(bpy.types.Scene, "cloth_next_quality"):
        del bpy.types.Scene.cloth_next_quality
    if hasattr(bpy.types.Object, "cloth_next"):
        del bpy.types.Object.cloth_next


CLASSES = (CLOTHNEXT_PG_material_settings, CLOTHNEXT_PG_damping_settings,
           CLOTHNEXT_PG_pressure_settings,
           CLOTHNEXT_PG_collision_settings, CLOTHNEXT_PG_friction_region,
           CLOTHNEXT_PG_soft_constraint,
           CLOTHNEXT_PG_advanced_pin_target,
           CLOTHNEXT_PG_motion_override,
           CLOTHNEXT_PG_rod_settings, CLOTHNEXT_PG_soft_body_settings,
           CLOTHNEXT_PG_rigid_body_settings,
           CLOTHNEXT_PG_force_settings,
           CLOTHNEXT_PG_solver_quality_settings,
           CLOTHNEXT_PG_recovery_settings,
           CLOTHNEXT_PG_solver_backend_settings,
           CLOTHNEXT_PG_object_settings)
