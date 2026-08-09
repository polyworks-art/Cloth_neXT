# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared material-aware real PPF run service for Bake and diagnostics.

Threading contract:
- Blender's main thread validates the scene, builds the immutable
  :class:`RunPlan`, starts one worker thread, and registers one timer.
- The worker never touches ``bpy``: it drives the pure
  :class:`~cloth_next.ppf_run.session.SolverSession`, converts validated
  frames, writes the PC2 cache atomically, and posts immutable messages
  into one bounded queue.
- The timer drains the queue, feeds the shared bake controller (panel, HUD,
  and companion all read the same snapshots), and on success attaches the
  Mesh Cache modifier and sets the timeline — all on the main thread.

This is a real simulation: no sleeps, no fake progress, no mocked solver.
The production Bake operator and Developer Real Solver Test deliberately call
the same :func:`start_run` service.
"""

from __future__ import annotations

import hashlib
import copy
import json
import logging
import math
import os
import queue
import re
import shutil
import struct
import threading
import time
import traceback
import uuid as uuid_module
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import bpy
import numpy as np

try:
    from bpy.app.handlers import persistent
except (ImportError, ModuleNotFoundError):  # pragma: no cover - pytest fake bpy
    def persistent(function):
        # Match Blender's decorator: persistence is identified by attribute
        # presence; Blender stores None rather than a truthy value.
        function._bpy_persistent = None
        return function

from .addon_identity import addon_preferences, package_addon_id

from .. import manifest_version
from .. import export_identity, recovery
from .. import intersection_diagnostics
from ..export_cache import ExportPayloadCache, deterministic_key
from ..sample_plan import build_collider_timeline, build_sample_plan
from ..bake import cache_metadata
from ..bake import pc2
from ..bake.controller import InvalidTransition, shared_controller
from ..bake.frame_range import BakeFrameRange, BakeRangeError
from ..bake.status import (BakeActivity, BakeJobKind, BakeState,
                           FrameEtaEstimator)
from ..bake.transport import EnterBakeMode
from ..core.errors import ClothNextError, ErrorRecord
from ..core.error_codes import classify_error
from ..core.logging import get_logger, log_with_context
from ..materials import DEFAULT_STATIC_SETTINGS, MaterialValidationError
from ..materials import formatting as material_formatting
from ..pinning import (
    STATIC_PIN_WEIGHT_THRESHOLD,
    AnimatedPinTargetSample,
    PinMode,
    StaticPinError,
    StaticPinConfig,
    StaticPinSnapshot,
    static_pin_config,
)
from ..ppf.coordinates import (
    matrix_is_finite_and_invertible,
    solver_world_matrix,
    solver_world_to_object_local,
    transform_points_numpy,
)
from ..ppf.resolver import (
    ResolvedSolver,
    SolverMode,
    SolverResolutionContext,
    SolverResolver,
    development_executable_from_environment,
)
from ..ppf.schema.data import (GROUP_PDRD, GROUP_ROD, GROUP_SHELL, GROUP_SOLID,
                               INTERNAL_STATIC_UUID,
                               SceneObject, encode_deformable_scene,
                               encode_multi_deformable_scene,
                               encode_multi_deformable_scene_file, encode_scene,
                               internal_static_sentinel, zero_area_triangles)
from ..ppf.schema.params import (
    SimulationSettings,
    build_multi_collider_param_payload,
    encode_deformable_param,
    encode_multi_collider_param,
    encode_multi_deformable_param,
    static_wire_params,
)
from ..materials.deformables import DeformableMaterialError
from ..curve_rod import CurveRodError, sample_curve
from ..ppf_run import import_result
from ..ppf_run.session import (
    SessionCancelled,
    SessionDeformable,
    SessionScene,
    SolverFrame,
    SolverSession,
    RecoveryOptions,
    RecoveryOutcome,
    RecoveryOutcomeKind,
    new_project_name,
)
from ..telemetry import shared_telemetry
from ..telemetry.hud_layout import RamAutoCancelGuard
from ..topology import geometry_fingerprint as combine_geometry_fingerprint
from ..topology import mesh_geometry_signature
from ..topology import mesh_topology_signature as _hash_mesh_topology
from ..topology import pin_indices_signature
from ..updater.install_paths import ManagedSolverPaths, read_current
from ..updater.solver_registry import load_registry
from . import (collider_proxy, companion_manager, modal_lock,
               object_properties, validation_state)
from .playback_cache import (
    OBJECT_OWNERSHIP_KEY,
    has_cloth_next_playback_marker,
    is_cloth_next_playback_modifier,
    mark_owned_playback,
    without_owned_playback,
)

_EVENT_STATE = {
    "STARTING_SOLVER": BakeState.STARTING_SOLVER,
    "UPLOADING": BakeState.UPLOADING,
    "BUILDING": BakeState.BUILDING,
    "SIMULATING": BakeState.SIMULATING,
    "FETCHING": BakeState.FETCHING,
}

_worker: threading.Thread | None = None
_cancel_event = threading.Event()
_queue: queue.Queue = queue.Queue(maxsize=256)
_active_plan: "RunPlan | None" = None
_last_work_directory: Path | None = None
_run_started_at: float = 0.0
_unsubscribe = None
_pending_plan: "RunPlan | None" = None
_pending_job_id = ""
_pin_capture = None
_export_timing_sink: dict[str, float] | None = None
_export_cache_event_sink: dict[str, str] | None = None
_ram_auto_cancel = RamAutoCancelGuard()
_ram_auto_cancel_enabled = False
_ram_auto_cancel_triggered = False
_eta_estimator = FrameEtaEstimator()
_intersection_violations: tuple[
    intersection_diagnostics.IntersectionViolation, ...] = ()
_intersection_violation_index = 0
_show_solver_input = False


def intersection_violations():
    """Immutable solver diagnostics currently available to Blender UI."""
    return _intersection_violations


def _clear_intersection_diagnostics() -> None:
    """Retire solver violations when a distinct Bake attempt begins."""
    global _intersection_violations, _intersection_violation_index
    from . import intersection_overlay
    intersection_overlay.clear()
    _intersection_violations = ()
    _intersection_violation_index = 0


def _ensure_solver_static(scene_colliders, collider_specs):
    """Satisfy PPF 0.11's internal STATIC-group build requirement."""
    if collider_specs:
        return scene_colliders, collider_specs
    sentinel = internal_static_sentinel()
    return ([*scene_colliders,sentinel],
            [*collider_specs,(sentinel.name,sentinel.uuid,
                              DEFAULT_STATIC_SETTINGS)])


def _on_controller_snapshot(snapshot) -> None:
    """Any CANCELLING transition (panel, HUD, or companion IPC) reaches the
    worker through the shared cancel event."""
    if snapshot.state is BakeState.CANCELLING and _worker is not None:
        _cancel_event.set()


class SceneValidationError(ValueError):
    pass


def _console_error(stage: str, message: str, details: str = "",
                   error_code: str = "") -> str:
    """Make artist-facing failures unmissable in Blender's System Console."""
    code = error_code or classify_error(stage, message, details)
    output = f"[Cloth NeXt] ERROR {code} · {stage}\n{message}"
    if details and details.strip() != message.strip():
        output += f"\n{details.rstrip()}"
    print(output, flush=True)
    return code


@dataclass(slots=True)
class ColliderMotionCapture:
    """Compact main-thread capture ready for the official PPF scene fields."""

    motion_type: str
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    transform: tuple[tuple[float, float, float, float], ...]
    animation: dict | None = None
    temporary_path: Path | None = None
    # Digest of the exact sampled motion that produced ``animation``. It is
    # part of the persistent Scene payload identity so an old Frame-1 collider
    # can never be restored after animation or exporter changes.
    content_digest: str = ""

    def cleanup(self) -> None:
        # Release a memmap before deleting its backing file on Windows.
        if self.animation is not None:
            frames = self.animation.get("vert_frames")
            mapping = getattr(frames, "_mmap", None)
            if mapping is not None:
                mapping.close()
        if self.temporary_path is not None:
            try:
                self.temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


@dataclass(frozen=True, slots=True)
class DeformablePlan:
    initial_local: tuple[tuple[float, float, float], ...]
    world_matrix: tuple[tuple[float, float, float, float], ...]
    object_name: str
    uuid: str
    pc2_path: Path
    topology_signature: str
    material_meta: dict
    role: str
    stitch_pairs: tuple[tuple[int, int], ...] = ()
    stitch_snap_distance: float = 0.0


@dataclass(frozen=True, slots=True)
class RunPlan:
    """Everything the worker and the import step need; no bpy references."""

    scene: SessionScene
    resolved: ResolvedSolver
    initial_local: tuple[tuple[float, float, float], ...]
    world_matrix: tuple[tuple[float, float, float, float], ...]
    cloth_object_name: str
    work_directory: Path
    pc2_path: Path
    frame_count: int
    frame_start: int = 1
    frame_end: int = 1
    fps: float = 24.0
    # Immutable pure snapshot metadata: the fingerprint marks the finished
    # result and the JSON-safe meta dict is written next to the PC2 cache so a
    # stale result stays detectable. The fingerprint is stored in halves — the
    # cheap settings half lets a Panel.draw detect "settings changed" without
    # touching the mesh; the geometry half can only be confirmed by a full
    # validation.
    settings_fingerprint: str = ""
    geometry_fingerprint: str = ""
    topology_signature: str = ""
    preset_identifier: str = ""
    material_meta: dict = field(default_factory=dict)
    deformable_role: str = "CLOTH"
    deformables: tuple[DeformablePlan, ...] = ()
    stitch_pairs: tuple[tuple[int, int], ...] = ()
    stitch_snap_distance: float = 0.0
    export_timings: dict[str, float] = field(default_factory=dict)
    export_cache_events: dict[str, str] = field(default_factory=dict)
    scene_cache_key: str = ""
    pin_configs: tuple[StaticPinConfig | None, ...] = ()
    param_cache_key: str = ""
    recovery_options: RecoveryOptions | None = None
    solver_input: intersection_diagnostics.SolverInputSnapshot | None = None
    backend_id: str = "PPF"


def _plan_deformables(plan: RunPlan) -> tuple[DeformablePlan, ...]:
    deformables = getattr(plan, "deformables", ())
    if deformables:
        return deformables
    return (DeformablePlan(
        plan.initial_local, plan.world_matrix, plan.cloth_object_name,
        str(getattr(plan.scene, "cloth_uuid", "legacy-cloth")), plan.pc2_path,
        getattr(plan, "topology_signature", ""),
        getattr(plan, "material_meta", {}),
        getattr(plan, "deformable_role", "CLOTH"),
        getattr(plan, "stitch_pairs", ()),
        getattr(plan, "stitch_snap_distance", 0.0)),)


def _merge_export_diagnostics(diagnostics, plan: RunPlan) -> None:
    """Attach main-thread export measurements to session diagnostics."""
    if not hasattr(diagnostics, "timings"):
        diagnostics.timings = {}
    diagnostics.timings.update(plan.export_timings)
    events = getattr(diagnostics, "cache_events", None)
    if events is None:
        try:
            diagnostics.cache_events = {}
        except (AttributeError, TypeError):
            return
        events = diagnostics.cache_events
    events.update(plan.export_cache_events)


def _plan_for_target(plan: RunPlan, target: DeformablePlan) -> RunPlan:
    return replace(plan, initial_local=target.initial_local,
        world_matrix=target.world_matrix, cloth_object_name=target.object_name,
        pc2_path=target.pc2_path, topology_signature=target.topology_signature,
        material_meta=target.material_meta, deformable_role=target.role,
        deformables=(), stitch_pairs=target.stitch_pairs,
        stitch_snap_distance=target.stitch_snap_distance)


# ---------------------------------------------------------------------------
# Solver resolution (reuses the existing resolver and preferences)

def _version_probe(executable: Path) -> tuple[str, str, str]:
    from ..ppf.models import ConnectionOwnership
    from ..ppf.process import SolverProcessConfig, SolverProcessManager
    config = SolverProcessConfig(
        executable_path=executable, working_directory=executable.parent,
        connect_timeout=10.0, ownership_mode=ConnectionOwnership.OWNED_PROCESS)
    return SolverProcessManager(config).executable_version()


def _resolved_installation_id(resolved) -> str:
    return str(getattr(resolved, "installation_id", "") or "")


def _resolved_release_tag(resolved) -> str | None:
    installation = getattr(resolved, "installation", None)
    return (getattr(installation, "official_release_tag", None)
            if installation is not None else None)


def _managed_root() -> Path | None:
    try:
        paths = ManagedSolverPaths.default()
        active = read_current(paths)
        if active is None:
            return None
        executable = active.executable_path(paths)
        bundle_root = paths.version_dir(active.installation_id)
        # Managed installs are owned by Cloth NeXt, so the pinned and tested
        # frontend extension can be applied safely before the process starts.
        from ..ppf.solver_overlay import apply_managed_solver_overlay
        apply_managed_solver_overlay(bundle_root)
        return bundle_root
    except (OSError, ValueError):
        return None


def resolve_solver(context) -> ResolvedSolver:
    selected = None
    try:
        preferences = addon_preferences(context, __package__)
        registry = load_registry(ManagedSolverPaths.default().registry_json)
        requested = (getattr(
            preferences, "selected_solver_installation_id", "") or "").strip()
        if requested == "NONE":
            requested = ""
        requested = requested or (registry.selected_installation_id or "")
        if requested:
            selected = registry.get(requested)
            if selected is None:
                raise SceneValidationError(
                    "The selected solver installation is missing. Choose "
                    "another installation in the Cloth NeXt preferences.")
    except (KeyError, AttributeError):
        pass
    if selected is not None and selected.managed:
        from ..ppf.solver_overlay import apply_solver_overlay
        apply_solver_overlay(
            selected.root,
            protocol_version=selected.protocol_version or "",
            schema_version=selected.schema_version or "",
            official_release_tag=selected.official_release_tag,
            managed=True)
    resolver = SolverResolver(_version_probe)
    resolved = resolver.resolve(SolverResolutionContext(
        selected_installation=selected,
        development_executable=development_executable_from_environment()))
    if resolved is None or resolved.executable_path is None:
        raise SceneValidationError(
            "No compatible PPF solver installation is configured. Select or "
            "install one in the Cloth NeXt add-on preferences.")
    return resolved


# ---------------------------------------------------------------------------
# Main-thread scene snapshot and validation

def _enabled_objects_by_role(context) -> tuple[object, object | None]:
    cloth_objects, collider_objects = [], []
    for obj in context.scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is None or not settings.enabled:
            continue
        if settings.role in {"CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY"}:
            cloth_objects.append(obj)
        elif settings.role == "COLLIDER":
            if collider_proxy.is_generated_proxy(obj):
                continue
            try:
                resolved_collider = collider_proxy.resolve_proxy(obj)
            except collider_proxy.ColliderProxyError as exc:
                raise SceneValidationError(str(exc)) from exc
            collider_objects.append(resolved_collider)
    if len(cloth_objects) != 1:
        raise SceneValidationError(
            f"Exactly one enabled Cloth NeXt cloth object is required for the "
            f"test run; found {len(cloth_objects)}.")
    if len(collider_objects) > 1:
        raise SceneValidationError(
            f"At most one enabled Cloth NeXt collider object is supported by "
            f"the test run; found {len(collider_objects)}.")
    return cloth_objects[0], collider_objects[0] if collider_objects else None


def _enabled_objects_for_bake(context) -> tuple[object, tuple[object, ...]]:
    """Compatibility view: first deformable and all enabled colliders."""
    deformables, colliders = _enabled_objects_for_solve(context)
    return deformables[0], colliders


def _enabled_objects_for_solve(context) -> tuple[tuple[object, ...],
                                                  tuple[object, ...]]:
    """Return deterministically ordered dynamic objects and colliders."""
    cloth_objects, collider_objects = [], []
    for obj in context.scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is None or not settings.enabled:
            continue
        if settings.role in {"CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY"}:
            cloth_objects.append(obj)
        elif settings.role == "COLLIDER":
            # Generated proxies are implementation objects owned by their
            # logical source Collider; never count them a second time merely
            # because their copied settings are enabled.
            if collider_proxy.is_generated_proxy(obj):
                continue
            try:
                resolved_collider = collider_proxy.resolve_proxy(obj)
            except collider_proxy.ColliderProxyError as exc:
                raise SceneValidationError(str(exc)) from exc
            collider_objects.append(resolved_collider)
    if not cloth_objects:
        raise SceneValidationError(
            "At least one enabled Cloth NeXt Cloth, Cable / Rope, or Soft Body object "
            "is required.")
    order = lambda obj: (
        str(getattr(getattr(obj, "cloth_next", None),
                    "persistent_export_id", "") or
            validation_state.object_key(obj)),
        str(getattr(obj, "name_full", getattr(obj, "name", ""))))
    cloth_objects.sort(key=order)
    collider_objects.sort(key=order)
    return tuple(cloth_objects), tuple(collider_objects)


def _enabled_force_objects(context) -> tuple[object, ...]:
    forces = []
    for obj in context.scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is not None and settings.enabled and settings.role == "FORCE":
            if getattr(obj, "type", "") != "EMPTY":
                raise SceneValidationError(
                    f"{obj.name}: Force is only supported on Empty objects.")
            forces.append(obj)
    forces.sort(key=lambda obj: (
        str(getattr(getattr(obj, "cloth_next", None),
                    "persistent_export_id", "") or
            validation_state.object_key(obj)),
        str(getattr(obj, "name_full", getattr(obj, "name", "")))))
    return tuple(forces)


def _sync_enabled_proxy_settings(context) -> None:
    """Synchronize generated proxies from an explicit mutable operation.

    Object discovery is also used by Blender panel drawing and therefore must
    stay read-only.  Validation and Bake operators call this helper before
    taking their scene snapshot, where writing ID properties is permitted.
    """
    for obj in context.scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if (settings is None or not settings.enabled or
                settings.role != "COLLIDER" or
                collider_proxy.is_generated_proxy(obj)):
            continue
        try:
            resolved = collider_proxy.resolve_proxy(obj)
        except collider_proxy.ColliderProxyError as exc:
            raise SceneValidationError(str(exc)) from exc
        if resolved is not obj:
            collider_proxy.sync_proxy_settings(obj, resolved)


@dataclass(frozen=True, slots=True)
class ForceState:
    gravity: tuple[float, float, float]
    wind: tuple[float, float, float]
    air_density: float = 0.001
    air_friction: float = 0.2
    vertex_air_damp: float = 0.0


@dataclass(frozen=True, slots=True)
class ForceCapture:
    initial: ForceState
    active_scalar_types: frozenset[str]
    dynamic_parameters: tuple[
        tuple[str, tuple[tuple[float, tuple[float, ...], bool], ...]], ...
    ] = ()


_SCALAR_FORCE_FIELDS = {
    "AIR_DENSITY": ("air_density", 0.001),
    "AIR_FRICTION": ("air_friction", 0.2),
    "VERTEX_AIR_DAMP": ("vertex_air_damp", 0.0),
}


def _wind_oscillation(obj, frame: int, fps: float) -> float:
    """Stable smooth pseudo-random gust value in the closed range [-1, 1]."""
    identity = str(getattr(obj, "name_full", getattr(obj, "name", "Wind")))
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    phase_a = int.from_bytes(digest[:4], "big") / 2**32 * math.tau
    phase_b = int.from_bytes(digest[4:8], "big") / 2**32 * math.tau
    rate_a = 0.31 + digest[8] / 255.0 * 0.23
    rate_b = 0.73 + digest[9] / 255.0 * 0.41
    seconds = float(frame) / max(1, int(fps))
    return (math.sin(math.tau * rate_a * seconds + phase_a)
            + 0.5 * math.sin(math.tau * rate_b * seconds + phase_b)) / 1.5


def _force_state(context, *, wind_frame: int | None = None) \
        -> tuple[ForceState, frozenset[str]]:
    """Resolve every PPF force/environment parameter in Blender space."""
    forces = _enabled_force_objects(context)
    gravity_forces = [obj for obj in forces
                      if obj.cloth_next.force.force_type == "GRAVITY"]
    gravity = ([0.0, 0.0, 0.0] if gravity_forces else
               list(context.scene.gravity) if context.scene.use_gravity else
               [0.0, 0.0, 0.0])
    wind = [0.0, 0.0, 0.0]
    scalars = {force_type: default
               for force_type, (_field, default) in _SCALAR_FORCE_FIELDS.items()}
    active_scalars = set()
    for obj in forces:
        force = obj.cloth_next.force
        force_type = str(force.force_type)
        if force_type in _SCALAR_FORCE_FIELDS:
            field, _default = _SCALAR_FORCE_FIELDS[force_type]
            value = float(getattr(force, field))
            if not math.isfinite(value) or value < 0.0:
                raise SceneValidationError(
                    f"{obj.name}: {field.replace('_', ' ')} is invalid.")
            if force_type not in active_scalars:
                scalars[force_type] = 0.0
                active_scalars.add(force_type)
            scalars[force_type] += value
            continue
        if force_type not in {"GRAVITY", "WIND"}:
            raise SceneValidationError(
                f"{obj.name}: unsupported Force type {force_type!r}.")
        matrix = obj.matrix_world
        axis = [float(matrix[row][2]) for row in range(3)]
        length = math.sqrt(sum(value * value for value in axis))
        if not math.isfinite(length) or length <= 1e-12:
            raise SceneValidationError(
                f"{obj.name}: Force Empty has an invalid local Z axis.")
        axis = [value / length for value in axis]
        strength = float(force.strength)
        if not math.isfinite(strength) or strength < 0.0:
            raise SceneValidationError(f"{obj.name}: Force strength is invalid.")
        if force_type == "WIND":
            variation = float(getattr(force, "wind_variation", 0.0))
            if not math.isfinite(variation) or variation < 0.0:
                raise SceneValidationError(
                    f"{obj.name}: Wind strength variation is invalid.")
            if wind_frame is not None and variation:
                strength = max(0.0, strength + variation * _wind_oscillation(
                    obj, wind_frame, _scene_fps(context)))
        target = gravity if force_type == "GRAVITY" else wind
        sign = -1.0 if force_type == "GRAVITY" else 1.0
        for index in range(3):
            target[index] += sign * strength * axis[index]
    return ForceState(tuple(gravity), tuple(wind),
        scalars["AIR_DENSITY"], scalars["AIR_FRICTION"],
        scalars["VERTEX_AIR_DAMP"]), frozenset(active_scalars)


def _force_vectors(context) -> tuple[tuple[float, float, float],
                                     tuple[float, float, float]]:
    """Compatibility helper returning the two vector-valued PPF forces."""
    state, _active = _force_state(context)
    return state.gravity, state.wind


def _extract_mesh(obj, depsgraph, *, needs_edges: bool):
    """Evaluated local vertices + loop triangles, original vertex order."""
    if obj.type != "MESH":
        raise SceneValidationError(f"{obj.name} is not a mesh object.")
    if any(mod.type == "CLOTH" for mod in obj.modifiers):
        raise SceneValidationError(
            f"{obj.name} carries a native Blender Cloth modifier; remove it — "
            "Cloth NeXt never uses native cloth.")
    evaluated = obj.evaluated_get(depsgraph)
    if _export_timing_sink is not None:
        _export_timing_sink["evaluated_get_count"] = (
            _export_timing_sink.get("evaluated_get_count", 0.0) + 1.0)
    mesh = evaluated.to_mesh()
    if _export_timing_sink is not None:
        _export_timing_sink["to_mesh_count"] = (
            _export_timing_sink.get("to_mesh_count", 0.0) + 1.0)
    try:
        vertex_count = len(mesh.vertices)
        if vertex_count == 0:
            raise SceneValidationError(f"{obj.name} has no vertices.")
        if vertex_count != len(obj.data.vertices):
            raise SceneValidationError(
                f"{obj.name}: {len(obj.data.vertices)} source vertices and "
                f"{vertex_count} evaluated vertices; topology-changing "
                "modifiers are unsupported.")
        if needs_edges and len(mesh.edges) == 0:
            raise SceneValidationError(f"{obj.name} has no edges.")
        if len(mesh.polygons) == 0:
            raise SceneValidationError(f"{obj.name} has no faces.")
        vertices = np.empty((vertex_count, 3), dtype="<f4")
        mesh.vertices.foreach_get("co", vertices.reshape(-1))
        if not np.isfinite(vertices).all():
            raise SceneValidationError(
                f"{obj.name} contains non-finite vertex coordinates.")
        mesh.calc_loop_triangles()
        triangle_count = len(mesh.loop_triangles)
        if not triangle_count:
            raise SceneValidationError(
                f"{obj.name} cannot be triangulated into a shell.")
        triangles = np.empty((triangle_count, 3), dtype="<u4")
        mesh.loop_triangles.foreach_get(
            "vertices", triangles.reshape(-1))
        invalid = ((triangles[:, 0] == triangles[:, 1])
                   | (triangles[:, 0] == triangles[:, 2])
                   | (triangles[:, 1] == triangles[:, 2])
                   | (triangles >= vertex_count).any(axis=1))
        if invalid.any():
            tri = tuple(int(value) for value in
                        triangles[int(np.flatnonzero(invalid)[0])])
            raise SceneValidationError(
                f"{obj.name} produced an invalid triangle {tri}.")
        return vertices, triangles
    finally:
        evaluated.to_mesh_clear()


def _extract_source_mesh(obj, *, needs_edges: bool):
    """Original mesh data before every modifier in the artist's stack."""
    if obj.type != "MESH":
        raise SceneValidationError(f"{obj.name} is not a mesh object.")
    mesh = getattr(obj, "data", None)
    if mesh is None:
        raise SceneValidationError(f"{obj.name} has no mesh data.")
    vertex_count = len(mesh.vertices)
    if vertex_count == 0:
        raise SceneValidationError(f"{obj.name} has no vertices.")
    if needs_edges and len(mesh.edges) == 0:
        raise SceneValidationError(f"{obj.name} has no edges.")
    if len(mesh.polygons) == 0:
        raise SceneValidationError(f"{obj.name} has no faces.")
    vertices = tuple((v.co.x, v.co.y, v.co.z) for v in mesh.vertices)
    for position in vertices:
        if any(not math.isfinite(c) for c in position):
            raise SceneValidationError(
                f"{obj.name} contains non-finite vertex coordinates.")
    mesh.calc_loop_triangles()
    triangles = tuple(tuple(tri.vertices) for tri in mesh.loop_triangles)
    if not triangles:
        raise SceneValidationError(
            f"{obj.name} cannot be triangulated into a shell.")
    for tri in triangles:
        if len(set(tri)) != 3 or any(
                not 0 <= index < vertex_count for index in tri):
            raise SceneValidationError(
                f"{obj.name} produced an invalid triangle {tri}.")
    return vertices, triangles


def _source_polygon_indices(obj, expected_triangle_count: int) \
        -> tuple[int, ...]:
    """Map exported loop triangles to source polygons when topology is stable."""
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "calc_loop_triangles"):
        return ()
    mesh.calc_loop_triangles()
    loop_triangles = tuple(getattr(mesh, "loop_triangles", ()))
    if len(loop_triangles) != int(expected_triangle_count):
        return ()
    try:
        return tuple(int(triangle.polygon_index)
                     for triangle in loop_triangles)
    except (AttributeError, TypeError, ValueError):
        return ()


@contextmanager
def _evaluate_through_last_armature(context, obj):
    """Expose the modifier stack only through its last enabled Armature.

    Rigged deformables must enter PPF in their visible Bake-Start pose. Any
    modifier after the rig remains a downstream display modifier and must not
    change solver geometry. Objects without an enabled Armature keep the
    original pre-modifier export path.
    """
    modifiers = tuple(getattr(obj, "modifiers", ()))
    armatures = [index for index, modifier in enumerate(modifiers)
                 if (getattr(modifier, "type", "") == "ARMATURE"
                     and bool(getattr(modifier, "show_viewport", True)))]
    if not armatures:
        yield False
        return
    cutoff = armatures[-1]
    changed = []
    try:
        for modifier in modifiers[cutoff + 1:]:
            if bool(getattr(modifier, "show_viewport", True)):
                changed.append(modifier)
                modifier.show_viewport = False
        _depsgraph_update(context)
        yield True
    finally:
        for modifier in changed:
            modifier.show_viewport = True
        _depsgraph_update(context)


def _extract_deformable_mesh(context, obj, *, needs_edges: bool):
    """Bake-Start rig pose when present, otherwise untouched source mesh."""
    with _evaluate_through_last_armature(context, obj) as rigged:
        if not rigged:
            return _extract_source_mesh(obj, needs_edges=needs_edges)
        return _extract_mesh(
            obj, context.evaluated_depsgraph_get(), needs_edges=needs_edges)


def _extract_source_uv_faces(obj) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Active per-corner UVs aligned with Blender's loop triangles.

    PPF only applies shell shrink-x/shrink-y while a UV rest basis exists.
    Keeping this separate from geometry extraction preserves the existing
    source-mesh API used by rods, soft bodies, colliders, and tests.
    """
    mesh = obj.data
    uv_layers = getattr(mesh, "uv_layers", None)
    active = getattr(uv_layers, "active", None) if uv_layers is not None else None
    data = getattr(active, "data", None) if active is not None else None
    mesh.calc_loop_triangles()
    if data is None or len(data) == 0:
        # PPF gates shrink-x/y on the presence of a UV basis. Cloth NeXt's
        # public Shrink control is isotropic, so a neutral non-degenerate
        # basis is sufficient when the artist has not authored UVs: rotation
        # cannot affect an equal X/Y scale.
        neutral = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
        return tuple(neutral for _triangle in mesh.loop_triangles)
    result = []
    for triangle in mesh.loop_triangles:
        loops = getattr(triangle, "loops", ())
        if len(loops) != 3:
            raise SceneValidationError(
                f"{obj.name} produced invalid loop-triangle UV indices.")
        face = []
        for loop_index in loops:
            uv = data[int(loop_index)].uv
            coords = (float(uv[0]), float(uv[1]))
            if any(not math.isfinite(c) for c in coords):
                raise SceneValidationError(
                    f"{obj.name} contains non-finite UV coordinates.")
            face.append(coords)
        result.append(tuple(face))
    return tuple(result)


def _friction_region_settings(obj) -> tuple[tuple[str, float], ...]:
    settings = getattr(obj, "cloth_next", None)
    regions = getattr(settings, "friction_regions", ())
    return tuple((str(region.vertex_group or ""), float(region.friction))
                 for region in regions)


def _extract_face_friction(
        obj, triangles, base_friction: float) -> tuple[float, ...]:
    """Blend Vertex Group targets and average vertex friction per triangle."""
    configured = _friction_region_settings(obj)
    if not configured:
        return ()
    groups = getattr(obj, "vertex_groups", None)
    resolved = {}
    names = set()
    for name, target in configured:
        if not name:
            raise SceneValidationError(
                f"{obj.name}: select a Vertex Group for every Friction entry.")
        if name in names:
            raise SceneValidationError(
                f"{obj.name}: Friction Vertex Group {name!r} is listed twice.")
        names.add(name)
        group = groups.get(name) if groups is not None else None
        if group is None:
            raise SceneValidationError(
                f"{obj.name}: Friction Vertex Group {name!r} does not exist.")
        resolved[int(group.index)] = max(0.0, min(1.0, target))

    vertex_values = []
    for vertex in obj.data.vertices:
        weighted_target = 0.0
        total_weight = 0.0
        for membership in vertex.groups:
            target = resolved.get(int(membership.group))
            if target is None:
                continue
            weight = max(0.0, min(1.0, float(membership.weight)))
            weighted_target += target * weight
            total_weight += weight
        blend = min(total_weight, 1.0)
        target = (weighted_target / total_weight
                  if total_weight > 0.0 else base_friction)
        vertex_values.append(base_friction * (1.0 - blend) + target * blend)
    return tuple(sum(vertex_values[index] for index in triangle) / 3.0
                 for triangle in triangles)


def _detect_sewing_edges(mesh) -> tuple[tuple[tuple[int, int], ...],
                                        tuple[int, ...]]:
    """Return face-less edges and endpoints that carry no surface mass."""
    face_edges: set[tuple[int, int]] = set()
    face_vertices: set[int] = set()
    for polygon in mesh.polygons:
        vertices = tuple(int(index) for index in polygon.vertices)
        face_vertices.update(vertices)
        for index, first in enumerate(vertices):
            second = vertices[(index + 1) % len(vertices)]
            face_edges.add(tuple(sorted((first, second))))
    pairs = tuple(sorted(tuple(sorted((int(edge.vertices[0]),
                                       int(edge.vertices[1]))))
                         for edge in mesh.edges
                         if tuple(sorted((int(edge.vertices[0]),
                                          int(edge.vertices[1]))))
                         not in face_edges))
    hanging = tuple(sorted({index for pair in pairs for index in pair
                            if index not in face_vertices}))
    return pairs, hanging


def _snap_closed_sewing_pairs(positions, pairs, threshold: float):
    """Close solved seams exactly once PPF has pulled a pair into range."""
    if not pairs or threshold <= 0.0:
        return positions
    import numpy as np
    result = np.asarray(positions).copy()
    baseline = np.asarray(positions)
    for first, second in pairs:
        if np.linalg.norm(baseline[first] - baseline[second]) < threshold:
            midpoint = 0.5 * (baseline[first] + baseline[second])
            result[first] = midpoint
            result[second] = midpoint
    return result


def _self_intersection_vertices(vertices, triangles) -> tuple[int, tuple[int, ...]]:
    """Return intersecting triangle-pair count and their source vertices."""
    if len(triangles) < 2:
        return 0, ()
    try:
        from mathutils.bvhtree import BVHTree
    except ImportError:  # Pure-Python test hosts do not ship Blender mathutils.
        return 0, ()
    tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
    pairs = set()
    marked = set()
    for first, second in tree.overlap(tree):
        first, second = int(first), int(second)
        if first == second:
            continue
        pair = (min(first, second), max(first, second))
        if pair in pairs:
            continue
        left, right = triangles[pair[0]], triangles[pair[1]]
        if set(left).intersection(right):
            continue
        pairs.add(pair)
        marked.update(left)
        marked.update(right)
    return len(pairs), tuple(sorted(marked))


def _select_problem_vertices(context, obj, indices) -> bool:
    """Best-effort Edit Mode selection for a failed source-mesh preflight."""
    try:
        active = getattr(context, "object", None)
        if active is not None and getattr(active, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for candidate in getattr(context, "selected_objects", ()):
            candidate.select_set(False)
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
        context.view_layer.objects.active = obj
        for vertex in obj.data.vertices:
            vertex.select = False
        for index in indices:
            obj.data.vertices[index].select = True
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(type="VERT")
        return True
    except (AttributeError, IndexError, RuntimeError, TypeError):
        return False


def _non_manifold_edge_count(mesh) -> int:
    """Count boundary/non-manifold edges without entering Blender Edit Mode."""
    uses: dict[tuple[int, int], int] = {}
    for polygon in mesh.polygons:
        vertices = tuple(int(index) for index in polygon.vertices)
        for offset, first in enumerate(vertices):
            edge = tuple(sorted((first, vertices[(offset + 1) % len(vertices)])))
            uses[edge] = uses.get(edge, 0) + 1
    mesh_edges = {tuple(sorted(map(int, edge.vertices))) for edge in mesh.edges}
    return sum(1 for edge in mesh_edges if uses.get(edge, 0) != 2)


def _cache_directory() -> Path:
    blend_directory = bpy.path.abspath("//")
    if blend_directory:
        return Path(blend_directory) / "cloth_next_test_cache"
    return Path(bpy.app.tempdir) / "cloth_next_test_cache"


def _snapshot_materials(cloth_obj, collider_obj):
    """Freeze all material properties on the main thread (Phase 3B).

    Returns ``(shell, static, contact_enabled, preset_identifier)``; any
    invalid value raises :class:`SceneValidationError` naming the property,
    the value, the accepted range, and the corrective action — before any
    worker or solver process starts.
    """
    try:
        role = str(cloth_obj.cloth_next.role)
        if role == "ROD":
            shell = object_properties.rod_settings_from(cloth_obj.cloth_next)
            preset_identifier = "ROD_DEFAULT"
        elif role == "SOFT_BODY":
            shell = object_properties.soft_body_settings_from(cloth_obj.cloth_next)
            preset_identifier = "SOFT_BODY_DEFAULT"
        elif role == "RIGID_BODY":
            shell = object_properties.rigid_body_settings_from(cloth_obj.cloth_next)
            preset_identifier = "RIGID_BODY_DEFAULT"
        else:
            shell = object_properties.shell_settings_from(cloth_obj.cloth_next)
            preset_identifier = str(cloth_obj.cloth_next.material.preset)
    except (MaterialValidationError, DeformableMaterialError) as exc:
        raise SceneValidationError(
            f"{cloth_obj.name}: invalid material value — {exc}") from exc
    static = None
    if collider_obj is not None:
        try:
            static = object_properties.static_settings_from(
                collider_obj.cloth_next)
        except MaterialValidationError as exc:
            raise SceneValidationError(
                f"{collider_obj.name}: invalid contact value — {exc}") from exc
    contact_enabled = bool(cloth_obj.cloth_next.collision.enabled)
    return shell, static, contact_enabled, preset_identifier


def _snapshot_materials_multi(cloth_obj, collider_objs):
    shell, _first, contact_enabled, preset = _snapshot_materials(
        cloth_obj, collider_objs[0] if collider_objs else None)
    statics = []
    for collider in collider_objs:
        try:
            statics.append(object_properties.static_settings_from(
                collider.cloth_next))
        except MaterialValidationError as exc:
            raise SceneValidationError(
                f"{collider.name}: invalid contact value — {exc}") from exc
    return shell, tuple(statics), contact_enabled, preset


class _EmptyMesh:
    """Stands in for an object with no mesh data, so hashing stays total."""

    vertices = ()
    edges = ()
    polygons = ()
    loops = ()


_EMPTY_MESH = _EmptyMesh()


def mesh_topology_signature(mesh) -> str:
    """Hash the connectivity of a mesh datablock (EXPENSIVE — never from draw).

    Delegates to the allocation-bounded ``foreach_get``/NumPy path in
    :mod:`cloth_next.topology`. The old implementation built lists of tuples
    for every edge and polygon and serialized them as JSON before hashing;
    this one streams four ``uint32`` buffers straight into SHA-256.
    """
    return _hash_mesh_topology(_EMPTY_MESH if mesh is None else mesh)


@dataclass(frozen=True, slots=True)
class _PinSummary:
    """What a Panel.draw may know about pinning without reading the mesh."""

    enabled: bool
    group_name: str
    group_exists: bool
    pin_count: int
    state: validation_state.ValidationState
    message: str = ""
    counted_group: str = ""


def cheap_pin_summary(cloth_obj) -> _PinSummary:
    """UI-safe pinning view model — reads properties and a name, nothing else.

    Deliberately performs no vertex-group membership scan: the pin *count*
    comes from the last full validation recorded in
    :mod:`~cloth_next.blender.validation_state`, and is labelled by that
    record's state so the panel can never present a stale number as current.
    """
    settings = getattr(cloth_obj, "cloth_next", None)
    enabled = bool(getattr(settings, "pinning_enabled", False))
    group_name = str(getattr(settings, "pin_group", "") or "")
    groups = getattr(cloth_obj, "vertex_groups", None)
    # A dict-style name lookup, not a membership scan.
    group_exists = bool(group_name) and (
        groups is not None and groups.get(group_name) is not None)
    record = validation_state.record_for(cloth_obj)
    return _PinSummary(
        enabled=enabled, group_name=group_name, group_exists=group_exists,
        pin_count=record.pin_count, state=record.state,
        message=record.message, counted_group=record.pin_group)


def _scan_pin_indices(cloth_obj, group_index: int) -> tuple[int, ...]:
    """Exact binary membership scan. One pass, hoisted lookups, no copies.

    Blender's Python API exposes no vectorized accessor for vertex-group
    weights (``foreach_get`` covers positions and indices, not deform
    weights), so this stays a per-vertex walk. The fix is that it now runs
    exactly once per full validation instead of several times per redraw.
    """
    threshold = STATIC_PIN_WEIGHT_THRESHOLD
    indices: list[int] = []
    append = indices.append
    for vertex in cloth_obj.data.vertices:
        for membership in vertex.groups:
            if (membership.group == group_index
                    and membership.weight > threshold):
                append(vertex.index)
                break
    return tuple(indices)


def _snapshot_static_pin(cloth_obj, *,
                         topology_signature: str | None = None) -> StaticPinSnapshot:
    """Capture exact vertex-group membership (EXPENSIVE — never from draw).

    Runs only from a full validation: Bake, Rebake, explicit validation, or
    the debounced validation timer. ``topology_signature`` is threaded through
    so a single validation hashes the topology once instead of once per caller.
    """
    settings = cloth_obj.cloth_next
    enabled = bool(getattr(settings, "pinning_enabled", False))
    group_name = str(getattr(settings, "pin_group", "") or "")
    mesh = getattr(cloth_obj, "data", None)
    vertex_count = len(getattr(mesh, "vertices", ()))
    object_id = str(getattr(cloth_obj, "name_full",
                            getattr(cloth_obj, "name", "")))
    if topology_signature is None:
        topology_signature = mesh_topology_signature(mesh)
    advanced_rows = tuple(getattr(settings, "advanced_pin_targets", ()))
    if advanced_rows:
        groups = getattr(cloth_obj, "vertex_groups", None)
        owners: dict[int, tuple[str, float]] = {}
        for row in advanced_rows:
            row_group = str(getattr(row, "vertex_group", "") or "")
            target = getattr(row, "target", None)
            strength = float(getattr(row, "strength", 1.0))
            if not row_group:
                raise SceneValidationError(
                    "Advanced Pin Motion: Select a Pin Group in every row.")
            if target is None:
                raise SceneValidationError(
                    f"Advanced Pin Motion: Select a Target for {row_group}.")
            if target is cloth_obj:
                raise SceneValidationError(
                    f"Advanced Pin Motion: {row_group} cannot target the Cloth itself.")
            if not math.isfinite(strength) or strength <= 0.0:
                raise SceneValidationError(
                    f"Advanced Pin Motion: {row_group} needs positive Strength.")
            group = groups.get(row_group) if groups is not None else None
            if group is None:
                raise SceneValidationError(
                    f"Advanced Pin Group {row_group!r} no longer exists.")
            indices = _scan_pin_indices(cloth_obj, int(group.index))
            if not indices:
                raise SceneValidationError(
                    f"Advanced Pin Group {row_group!r} contains no vertices.")
            for index in indices:
                previous = owners.get(index)
                if previous is not None:
                    raise SceneValidationError(
                        f"Advanced Pin Groups {previous[0]!r} and "
                        f"{row_group!r} overlap at vertex {index}. Use "
                        "disjoint groups for different Targets.")
                owners[index] = (row_group, strength)
        union = tuple(sorted(owners))
        weights = tuple(owners[index][1] for index in union)
        return StaticPinSnapshot(
            True, "Advanced Pin Motion", object_id, vertex_count, union,
            source_topology_signature=topology_signature,
            pull_weights=weights)
    if not enabled:
        return StaticPinSnapshot(False, group_name, object_id, vertex_count, (),
                                 source_topology_signature=topology_signature)
    if not group_name:
        raise SceneValidationError("Select a Pin Group.")
    groups = getattr(cloth_obj, "vertex_groups", None)
    group = groups.get(group_name) if groups is not None else None
    if group is None:
        raise SceneValidationError("The selected Pin Group no longer exists.")
    indices = _scan_pin_indices(cloth_obj, int(group.index))
    try:
        return StaticPinSnapshot(True, group_name, object_id, vertex_count,
                                 indices,
                                 source_topology_signature=topology_signature)
    except StaticPinError as exc:
        raise SceneValidationError(str(exc)) from exc

# ---------------------------------------------------------------------------
# Fingerprints
#
# The fingerprint is split so the UI can answer "did the settings change?"
# honestly and instantly, while "did the mesh change?" stays an expensive
# question that only a full validation is allowed to answer.
#
#   settings fingerprint  — materials, damping, collision, pressure, quality,
#                           bake range, fps, roles, object identities, pin
#                           mode and pin group NAME. No mesh access. Cheap.
#   geometry fingerprint  — topology signature + validated pin indices.
#                           Requires a full mesh scan. Expensive.
#   bake fingerprint      — both halves combined; written into the sidecar.

SETTINGS_FINGERPRINT_VERSION = 3
BAKE_FINGERPRINT_VERSION = 3


def _cheap_pinning_fingerprint(cloth_obj) -> str:
    """Pinning *intent* — enabled, group name, mode. Never the indices."""
    settings = cloth_obj.cloth_next
    record = "\0".join((
        "1" if getattr(settings, "pinning_enabled", False) else "0",
        str(getattr(settings, "pin_group", "") or ""),
        str(getattr(settings, "pin_mode", "STATIC")),
        "1" if getattr(settings, "advanced_pin_motion_enabled", False) else "0",
        str(getattr(getattr(settings, "pin_target", None), "name_full", "")),
        repr(tuple((str(getattr(row, "vertex_group", "")),
                    str(getattr(getattr(row, "target", None),
                                "name_full", "")),
                    float(getattr(row, "strength", 1.0)))
                   for row in getattr(settings,
                                      "advanced_pin_targets", ()))),
        repr(tuple((str(getattr(getattr(row, "target", None),
                               "name_full", "")),
                    str(getattr(row, "constraint_type", "LOCATION")),
                    float(getattr(row, "strength", 1.0)))
                   for row in getattr(settings, "soft_constraints", ()))),
        repr(tuple((int(getattr(row, "frame", 1)),
                    str(getattr(row, "motion_type", "LINEAR")),
                    tuple(float(value) for value in getattr(
                        row, "velocity", (0.0, 0.0, 0.0))),
                    tuple(float(value) for value in getattr(
                        row, "angular_velocity", (0.0, 0.0, 0.0))))
                   for row in getattr(settings, "motion_overrides", ()))),
    ))
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def _scene_fps(context) -> float:
    """Return Blender's effective playback rate, including ``fps_base``."""
    render = getattr(getattr(context, "scene", None), "render", None)
    try:
        fps = float(getattr(render, "fps", 24) or 24)
        fps_base = float(getattr(render, "fps_base", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 24.0
    if (not math.isfinite(fps) or fps <= 0.0
            or not math.isfinite(fps_base) or fps_base <= 0.0):
        return 24.0
    return fps / fps_base


def _blender_version() -> str:
    value = str(getattr(bpy.app, "version_string", "") or "")
    if value:
        return value
    version = getattr(bpy.app, "version", ())
    return ".".join(map(str, version)) if version else "unknown"


def _world_matrix_record(obj) -> tuple[tuple[float, ...], ...]:
    matrix = getattr(obj, "matrix_world", None)
    if matrix is None:
        return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
    return tuple(tuple(float(value) for value in row) for row in matrix)


def _settings_fingerprint(context, cloth_obj, collider_obj, shell, static,
                          contact_enabled, preset_identifier, quality) -> str:
    collider_objs = (collider_obj if isinstance(collider_obj, tuple)
                     else (() if collider_obj is None else (collider_obj,)))
    statics = (static if isinstance(static, tuple)
               else (() if static is None else (static,)))
    if str(cloth_obj.cloth_next.role) == "CLOTH":
        base = material_formatting.settings_fingerprint(
            shell, statics[0] if statics else None,
            contact_enabled, preset_identifier,
            bake_start=int(cloth_obj.cloth_next.bake_start),
            bake_end=int(cloth_obj.cloth_next.bake_end),
            pinning_fingerprint=_cheap_pinning_fingerprint(cloth_obj),
            quality=quality)
    else:
        base = json.dumps({"material": asdict(shell),
            "contact_enabled": contact_enabled,
            "bake_range": [int(cloth_obj.cloth_next.bake_start),
                           int(cloth_obj.cloth_next.bake_end)],
            "quality": asdict(quality)}, sort_keys=True, separators=(",", ":"))
    collider_settings = []
    for obj, material in zip(collider_objs, statics):
        world = _world_matrix_record(obj)
        collider_settings.append(json.dumps({
            "object_key": validation_state.object_key(obj),
            "role": str(obj.cloth_next.role),
            "motion": str(getattr(obj.cloth_next, "collider_motion", "STATIC")),
            "motion_samples_per_frame": int(getattr(
                obj.cloth_next, "collider_samples_per_frame",
                COLLIDER_SAMPLES_PER_FRAME)),
            "world_matrix": world,
            "material": static_wire_params(material),
        }, sort_keys=True, separators=(",", ":")))
    force_settings = [json.dumps({
        "object_key": validation_state.object_key(obj),
        "type": str(obj.cloth_next.force.force_type),
        "strength": float(obj.cloth_next.force.strength),
        "wind_variation": float(getattr(obj.cloth_next.force,
                                         "wind_variation", 0.0)),
        "air_density": float(getattr(obj.cloth_next.force,
                                     "air_density", 0.001)),
        "air_friction": float(getattr(obj.cloth_next.force,
                                      "air_friction", 0.2)),
        "vertex_air_damp": float(getattr(obj.cloth_next.force,
                                         "vertex_air_damp", 0.0)),
        "world_matrix": _world_matrix_record(obj),
        "animation": _animation_signature(obj),
    }, sort_keys=True, separators=(",", ":"))
        for obj in _enabled_force_objects(context)]
    record = "\0".join((
        str(SETTINGS_FINGERPRINT_VERSION), base,
        json.dumps(_friction_region_settings(cloth_obj),
                   separators=(",", ":")),
        validation_state.object_key(cloth_obj),
        str(cloth_obj.cloth_next.role),
        json.dumps(_world_matrix_record(cloth_obj),
                   separators=(",", ":")),
        *collider_settings,
        *force_settings,
        str(_scene_fps(context)),
    ))
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def cheap_settings_fingerprint(context) -> str | None:
    """UI-safe settings fingerprint. Reads properties only — never a mesh.

    Returns ``None`` when the scene is not exactly one cloth plus one
    collider, or a mapped value is invalid.
    """
    try:
        deformables, collider_objs = _enabled_objects_for_solve(context)
        contacts = {bool(obj.cloth_next.collision.enabled)
                    for obj in deformables}
        ranges = {(int(obj.cloth_next.bake_start),
                   int(obj.cloth_next.bake_end)) for obj in deformables}
        if len(contacts) != 1 or len(ranges) != 1:
            return None
        contact_enabled = contacts.pop()
        statics = tuple(object_properties.static_settings_from(obj.cloth_next)
                        for obj in collider_objs)
        quality = object_properties.solver_quality_from(context.scene)
        fingerprints = []
        for obj in deformables:
            material, _static, _contact, preset = _snapshot_materials(
                obj, collider_objs[0] if collider_objs else None)
            fingerprints.append(_settings_fingerprint(
                context, obj, collider_objs, material, statics,
                contact_enabled, preset, quality))
    except (SceneValidationError, MaterialValidationError, ValueError,
            AttributeError):
        return None
    return cache_metadata.deterministic_hash({
        "version": SETTINGS_FINGERPRINT_VERSION,
        "deformables": fingerprints})


def bake_fingerprint(settings_fingerprint: str,
                     geometry_fingerprint: str) -> str:
    return hashlib.sha256(
        f"{BAKE_FINGERPRINT_VERSION}\0{settings_fingerprint}\0"
        f"{geometry_fingerprint}".encode("utf-8")).hexdigest()


def _animation_signature(obj) -> str:
    """Deterministically identify ordinary Object/Curve Action keyframes."""
    animation = getattr(obj, "animation_data", None)
    action = getattr(animation, "action", None)
    records = []
    for curve in getattr(action, "fcurves", ()) if action is not None else ():
        points = []
        for point in getattr(curve, "keyframe_points", ()):
            co = getattr(point, "co", ())
            points.append((float(co[0]), float(co[1]),
                           str(getattr(point, "interpolation", ""))))
        records.append({
            "data_path": str(getattr(curve, "data_path", "")),
            "array_index": int(getattr(curve, "array_index", 0)),
            "points": points,
        })
    return cache_metadata.deterministic_hash({
        "action": str(getattr(action, "name", "")) if action else "",
        "fcurves": sorted(records, key=lambda item: (
            item["data_path"], item["array_index"])),
    })


def _record_cache_inspection(obj, inspection) -> None:
    settings = getattr(obj, "cloth_next", None)
    if settings is None:
        return
    settings.baked_cache_condition = inspection.condition.value
    settings.baked_cache_message = inspection.message
    metadata = inspection.metadata or {}
    settings.baked_metadata_digest = str(
        metadata.get("metadata_digest", ""))


def inspect_attached_cache(obj, *, settings_fingerprint: str | None = None,
                           geometry_fingerprint: str | None = None):
    """Authenticate the active cache during explicit validation/Bake only."""
    path = None
    if getattr(obj, "type", "") == "CURVE":
        recorded = str(getattr(getattr(obj, "data", None), "get",
                               lambda *_: "")(
            "cloth_next_rod_cache", "") or "")
        if recorded:
            path = Path(recorded)
    else:
        modifier = next((item for item in getattr(obj, "modifiers", ())
                         if has_cloth_next_playback_marker(obj, item)), None)
        if modifier is not None and getattr(modifier, "filepath", ""):
            path = Path(bpy.path.abspath(modifier.filepath))
    if path is None:
        return None
    inspection = cache_metadata.inspect_cache(
        path, settings_fingerprint=settings_fingerprint,
        geometry_fingerprint=geometry_fingerprint)
    _record_cache_inspection(obj, inspection)
    return inspection


# ---------------------------------------------------------------------------
# The single authoritative full validation

@dataclass(frozen=True, slots=True)
class DeformableValidation:
    obj: object
    material: object
    preset_identifier: str
    pin_membership: StaticPinSnapshot
    topology_signature: str
    shape_signature: str
    role: str


@dataclass(frozen=True, slots=True)
class ValidationSnapshot:
    """One complete, validated view of the scene. Produced exactly once.

    Holds live ``bpy`` objects and is therefore main-thread-only and
    strictly transient: it is handed straight to :func:`build_run_plan` and
    never stored in a PropertyGroup, a handler, or a thread.
    """

    cloth_obj: object
    collider_obj: object | None
    collider_objs: tuple[object, ...]
    bake_range: BakeFrameRange
    shell: object
    static: object | None
    statics: tuple[object, ...]
    contact_enabled: bool
    preset_identifier: str
    quality: object
    pin_membership: StaticPinSnapshot
    topology_signature: str
    settings_fingerprint: str
    geometry_fingerprint: str
    combined_fingerprint: str
    deformables: tuple[DeformableValidation, ...] = ()
    gravity_blender: tuple[float, float, float] = (0.0, 0.0, -9.81)
    wind_blender: tuple[float, float, float] = (0.0, 0.0, 0.0)
    timings: dict[str, float] = field(default_factory=dict)


def _validate_scene_single(context) -> ValidationSnapshot:
    """Fully validate the scene: topology, materials, pinning, fingerprints.

    EXPENSIVE by design and the *only* place the mesh is scanned. Called from
    Bake, Rebake, the explicit Validate operator, and the debounced validation
    timer — never from ``Panel.draw()`` or ``Panel.poll()``.

    The result is recorded in :mod:`validation_state` (VALID or INVALID with a
    readable message) and returned so the Bake path can reuse it without
    scanning anything a second time.
    """
    cloth_obj, collider_objs = _enabled_objects_for_bake(context)
    collider_obj = collider_objs[0] if collider_objs else None
    validation_state.mark_validating(cloth_obj)
    try:
        try:
            bake_range = BakeFrameRange(int(cloth_obj.cloth_next.bake_start),
                                        int(cloth_obj.cloth_next.bake_end))
        except (BakeRangeError, TypeError, ValueError) as exc:
            raise SceneValidationError(str(exc)) from exc
        shell, statics, contact_enabled, preset_identifier = (
            _snapshot_materials_multi(cloth_obj, collider_objs))
        static = statics[0] if statics else None
        try:
            quality = object_properties.solver_quality_from(context.scene)
        except ValueError as exc:
            raise SceneValidationError(str(exc)) from exc
        role = str(cloth_obj.cloth_next.role)
        if role == "ROD":
            vertices, edges, _splines = sample_curve(cloth_obj)
            action = getattr(getattr(cloth_obj.data, "animation_data", None),
                             "action", None)
            if (action is not None
                    and not bool(action.get("cloth_next_rod_action", False))):
                raise SceneValidationError(
                    f"{cloth_obj.name} already has Curve animation. Remove or "
                    "stash it before baking Cable / Rope playback.")
            topology_signature = hashlib.sha256(json.dumps(
                {"vertices": len(vertices), "edges": edges},
                separators=(",", ":")).encode("utf-8")).hexdigest()
            deformable_shape_signature = cache_metadata.deterministic_hash({
                "vertices": vertices, "edges": edges})
            if bool(cloth_obj.cloth_next.pinning_enabled):
                raise SceneValidationError(
                    "Cable / Rope pinning is not available yet; disable Pinning.")
            pin_membership = StaticPinSnapshot(
                False, "", str(cloth_obj.name), len(vertices), (),
                source_topology_signature=topology_signature)
        else:
            topology_signature = mesh_topology_signature(
                getattr(cloth_obj, "data", None))
            deformable_shape_signature = mesh_geometry_signature(
                getattr(cloth_obj, "data", None),
                topology_signature=topology_signature)
            if role in {"SOFT_BODY", "RIGID_BODY"} and bool(
                    cloth_obj.cloth_next.pinning_enabled):
                raise SceneValidationError(
                    f"{role.replace('_', ' ').title()} pinning is not available "
                    "yet; disable Pinning.")
            pin_membership = _snapshot_static_pin(
                cloth_obj, topology_signature=topology_signature)
        settings_fp = _settings_fingerprint(
            context, cloth_obj, collider_objs, shell, statics, contact_enabled,
            preset_identifier, quality)
        collider_geometry = []
        for collider in collider_objs:
            collider_geometry.append({
                "object_key": validation_state.object_key(collider),
                "shape": mesh_geometry_signature(getattr(collider, "data", None)),
                "animation": _animation_signature(collider),
            })
        scene_geometry_signature = cache_metadata.deterministic_hash({
            "deformable": deformable_shape_signature,
            "deformable_animation": _animation_signature(cloth_obj),
            "colliders": collider_geometry,
        })
        geometry_fp = combine_geometry_fingerprint(
            scene_geometry_signature,
            pin_indices_signature(
                pin_membership.vertex_indices,
                vertex_count=pin_membership.source_vertex_count))
    except (SceneValidationError, ClothNextError, MaterialValidationError,
            DeformableMaterialError, CurveRodError) as exc:
        message = (exc.record.user_message if isinstance(exc, ClothNextError)
                   else str(exc))
        validation_state.store_invalid(cloth_obj, message)
        raise
    validation_state.store_valid(
        cloth_obj, pin_count=len(pin_membership.vertex_indices),
        pin_group=pin_membership.group_name,
        topology_signature=topology_signature,
        geometry_fingerprint=geometry_fp, settings_fingerprint=settings_fp)
    if str(getattr(cloth_obj.cloth_next,
                   "baked_cache_condition", "") or ""):
        inspect_attached_cache(
            cloth_obj, settings_fingerprint=settings_fp,
            geometry_fingerprint=geometry_fp)
    return ValidationSnapshot(
        cloth_obj=cloth_obj, collider_obj=collider_obj,
        collider_objs=collider_objs, bake_range=bake_range,
        shell=shell, static=static, statics=statics,
        contact_enabled=contact_enabled,
        preset_identifier=preset_identifier, quality=quality,
        pin_membership=pin_membership, topology_signature=topology_signature,
        settings_fingerprint=settings_fp, geometry_fingerprint=geometry_fp,
        combined_fingerprint=bake_fingerprint(settings_fp, geometry_fp))


def _validate_scene_impl(context) -> ValidationSnapshot:
    """Validate every enabled deformable as one interacting solver scene."""
    _sync_enabled_proxy_settings(context)
    deformable_objs, collider_objs = _enabled_objects_for_solve(context)
    export_identity.ensure_unique_persistent_ids(
        (*deformable_objs, *collider_objs,
         *_enabled_force_objects(context)))
    # ID creation is an explicit validation mutation. Re-sort immediately so
    # the very first Bake has the same order as every later Bake/reload.
    deformable_objs = tuple(sorted(
        deformable_objs, key=export_identity.export_uuid))
    collider_objs = tuple(sorted(
        collider_objs, key=export_identity.export_uuid))
    for obj in deformable_objs:
        validation_state.mark_validating(obj)
    try:
        ranges = tuple(BakeFrameRange(int(obj.cloth_next.bake_start),
                                      int(obj.cloth_next.bake_end))
                       for obj in deformable_objs)
        if len({(item.start, item.end) for item in ranges}) != 1:
            raise SceneValidationError(
                "All enabled deformables must use the same Bake Start and "
                "Bake End for a shared simulation.")
        contacts = {bool(obj.cloth_next.collision.enabled)
                    for obj in deformable_objs}
        if len(contacts) != 1:
            raise SceneValidationError(
                "All enabled deformables must use the same Enable Contact "
                "setting because contact is scene-wide in PPF.")
        contact_enabled = contacts.pop()
        materials = []
        presets = []
        for obj in deformable_objs:
            material, _static, _contact, preset = _snapshot_materials(
                obj, collider_objs[0] if collider_objs else None)
            materials.append(material)
            presets.append(preset)
        statics = tuple(object_properties.static_settings_from(obj.cloth_next)
                        for obj in collider_objs)
        quality = object_properties.solver_quality_from(context.scene)
        gravity_blender, wind_blender = _force_vectors(context)
        entries = []
        for obj, material, preset in zip(deformable_objs, materials, presets):
            role = str(obj.cloth_next.role)
            if role == "ROD":
                vertices, edges, _splines = sample_curve(obj)
                action = getattr(getattr(obj.data, "animation_data", None),
                                 "action", None)
                if (action is not None
                        and not bool(action.get("cloth_next_rod_action", False))):
                    raise SceneValidationError(
                        f"{obj.name} already has Curve animation. Remove or "
                        "stash it before baking Cable / Rope playback.")
                topology = hashlib.sha256(json.dumps(
                    {"vertices": len(vertices), "edges": edges},
                    separators=(",", ":")).encode("utf-8")).hexdigest()
                shape = cache_metadata.deterministic_hash(
                    {"vertices": vertices, "edges": edges})
                if bool(obj.cloth_next.pinning_enabled):
                    raise SceneValidationError(
                        f"{obj.name}: Cable / Rope pinning is not available yet; "
                        "disable Pinning.")
                pins = StaticPinSnapshot(False, "", str(obj.name),
                    len(vertices), (), source_topology_signature=topology)
            else:
                topology = mesh_topology_signature(getattr(obj, "data", None))
                shape = mesh_geometry_signature(
                    getattr(obj, "data", None), topology_signature=topology)
                if role in {"SOFT_BODY", "RIGID_BODY"} and bool(
                        obj.cloth_next.pinning_enabled):
                    raise SceneValidationError(
                        f"{obj.name}: {role.replace('_', ' ').title()} pinning "
                        "is not available yet; "
                        "disable Pinning.")
                pins = _snapshot_static_pin(obj, topology_signature=topology)
            entries.append(DeformableValidation(
                obj, material, preset, pins, topology, shape, role))
        per_object_settings = [
            _settings_fingerprint(context, entry.obj, collider_objs,
                                  entry.material, statics, contact_enabled,
                                  entry.preset_identifier, quality)
            for entry in entries]
        settings_fp = cache_metadata.deterministic_hash({
            "version": SETTINGS_FINGERPRINT_VERSION,
            "deformables": per_object_settings})
        collider_geometry = [{
            "object_key": validation_state.object_key(obj),
            "shape": mesh_geometry_signature(getattr(obj, "data", None)),
            "animation": _animation_signature(obj),
        } for obj in collider_objs]
        geometry_fp = cache_metadata.deterministic_hash({
            "deformables": [{
                "object_key": validation_state.object_key(entry.obj),
                "shape": entry.shape_signature,
                "animation": _animation_signature(entry.obj),
                "pins": pin_indices_signature(
                    entry.pin_membership.vertex_indices,
                    vertex_count=entry.pin_membership.source_vertex_count),
            } for entry in entries],
            "colliders": collider_geometry})
    except (SceneValidationError, ClothNextError, MaterialValidationError,
            DeformableMaterialError, CurveRodError, BakeRangeError,
            TypeError, ValueError) as exc:
        message = (exc.record.user_message if isinstance(exc, ClothNextError)
                   else str(exc))
        for obj in deformable_objs:
            validation_state.store_invalid(obj, message)
        if isinstance(exc, SceneValidationError):
            raise
        raise SceneValidationError(message) from exc
    for entry in entries:
        validation_state.store_valid(
            entry.obj, pin_count=len(entry.pin_membership.vertex_indices),
            pin_group=entry.pin_membership.group_name,
            topology_signature=entry.topology_signature,
            geometry_fingerprint=geometry_fp,
            settings_fingerprint=settings_fp)
        if str(getattr(entry.obj.cloth_next,
                       "baked_cache_condition", "") or ""):
            inspect_attached_cache(entry.obj,
                settings_fingerprint=settings_fp,
                geometry_fingerprint=geometry_fp)
    first = entries[0]
    preset_identifier = (first.preset_identifier if len(entries) == 1
                         else f"MULTI_OBJECT_{len(entries)}")
    return ValidationSnapshot(
        cloth_obj=first.obj,
        collider_obj=collider_objs[0] if collider_objs else None,
        collider_objs=collider_objs, bake_range=ranges[0],
        shell=first.material, static=statics[0] if statics else None,
        statics=statics,
        contact_enabled=contact_enabled,
        preset_identifier=preset_identifier, quality=quality,
        pin_membership=first.pin_membership,
        topology_signature=first.topology_signature,
        settings_fingerprint=settings_fp, geometry_fingerprint=geometry_fp,
        combined_fingerprint=bake_fingerprint(settings_fp, geometry_fp),
        deformables=tuple(entries), gravity_blender=gravity_blender,
        wind_blender=wind_blender)


def validate_scene(context) -> ValidationSnapshot:
    """Timed authoritative validation snapshot."""
    started = time.monotonic()
    snapshot = _validate_scene_impl(context)
    return replace(snapshot, timings={
        **snapshot.timings,
        "validation": time.monotonic() - started,
    })


def _id_source_identity(data) -> dict:
    library = getattr(data, "library", None)
    library_path = str(getattr(library, "filepath", "") or "")
    library_record = {"path": library_path}
    if library_path:
        try:
            resolved = Path(bpy.path.abspath(library_path)).resolve()
            stat = resolved.stat()
            library_record.update(
                {"resolved": str(resolved), "size": stat.st_size,
                 "mtime_ns": stat.st_mtime_ns})
        except OSError:
            library_record["unresolved"] = True
    return {
        "type": type(data).__name__,
        "name": str(getattr(data, "name_full",
                            getattr(data, "name", ""))),
        "library": library_record,
    }


def _safe_action_identity(owner) -> tuple[bool, dict, str]:
    animation = getattr(owner, "animation_data", None)
    if animation is None:
        return True, {"action": None}, ""
    if getattr(animation, "drivers", ()):
        return False, {}, "Python or scripted Driver"
    nla_tracks = tuple(getattr(animation, "nla_tracks", ()))
    if any(tuple(getattr(track, "strips", ())) for track in nla_tracks):
        return False, {}, "NLA dependency"
    action = getattr(animation, "action", None)
    curves = []
    for curve in getattr(action, "fcurves", ()) if action else ():
        if getattr(curve, "modifiers", ()):
            return False, {}, "FCurve modifier"
        points = []
        for point in getattr(curve, "keyframe_points", ()):
            co = getattr(point, "co", (0.0, 0.0))
            left = getattr(point, "handle_left", co)
            right = getattr(point, "handle_right", co)
            points.append({
                "co": [float(co[0]), float(co[1])],
                "left": [float(left[0]), float(left[1])],
                "right": [float(right[0]), float(right[1])],
                "interpolation": str(
                    getattr(point, "interpolation", "")),
                "easing": str(getattr(point, "easing", "")),
            })
        curves.append({
            "path": str(getattr(curve, "data_path", "")),
            "index": int(getattr(curve, "array_index", 0)),
            "points": points,
        })
    return True, {
        "action": (_id_source_identity(action) if action else None),
        "curves": sorted(curves, key=lambda row: (
            row["path"], row["index"])),
    }, ""


def _safe_object_dependency_identity(obj) -> tuple[bool, dict, str]:
    """Cheap, conservative dependency identity for an early cache lookup."""
    if tuple(getattr(obj, "constraints", ())):
        return False, {}, f"{obj.name}: constraint dependency"
    data = getattr(obj, "data", None)
    if data is None:
        return False, {}, f"{obj.name}: missing data-block"
    source = _id_source_identity(data)
    if source["library"].get("unresolved"):
        return False, {}, f"{obj.name}: unresolved library dependency"
    if getattr(data, "shape_keys", None) is not None:
        return False, {}, f"{obj.name}: Shape Keys require safe capture"
    safe, action, reason = _safe_action_identity(obj)
    if not safe:
        return False, {}, f"{obj.name}: {reason}"
    safe, data_action, reason = _safe_action_identity(data)
    if not safe:
        return False, {}, f"{obj.name} data: {reason}"
    modifiers = []
    for modifier in getattr(obj, "modifiers", ()):
        if not bool(getattr(modifier, "show_viewport", True)):
            continue
        if is_cloth_next_playback_modifier(obj, modifier):
            continue
        kind = str(getattr(modifier, "type", ""))
        if kind != "ARMATURE":
            return False, {}, (
                f"{obj.name}: {kind or 'unknown'} modifier dependency")
        armature = getattr(modifier, "object", None)
        if armature is None:
            return False, {}, f"{obj.name}: unresolved Armature modifier"
        if tuple(getattr(armature, "constraints", ())):
            return False, {}, f"{obj.name}: constrained Armature"
        if getattr(armature, "parent", None) is not None:
            return False, {}, f"{obj.name}: parented Armature dependency"
        if any(tuple(getattr(bone, "constraints", ()))
               for bone in getattr(
                   getattr(armature, "pose", None), "bones", ())):
            return False, {}, f"{obj.name}: constrained pose bone"
        armature_safe, armature_action, reason = (
            _safe_action_identity(armature))
        if not armature_safe:
            return False, {}, f"{armature.name}: {reason}"
        armature_data = getattr(armature, "data", None)
        if armature_data is None:
            return False, {}, f"{obj.name}: missing Armature data"
        data_safe, armature_data_action, reason = _safe_action_identity(
            armature_data)
        if not data_safe:
            return False, {}, f"{armature.name} data: {reason}"
        bones = [{
            "name": str(getattr(bone, "name", "")),
            "parent": str(getattr(
                getattr(bone, "parent", None), "name", "")),
            "matrix_local": [[float(value) for value in row]
                             for row in getattr(bone, "matrix_local", ())],
            "use_deform": bool(getattr(bone, "use_deform", True)),
        } for bone in getattr(armature_data, "bones", ())]
        pose_basis = [{
            "name": str(getattr(bone, "name", "")),
            "matrix_basis": [[float(value) for value in row]
                             for row in getattr(bone, "matrix_basis", ())],
        } for bone in getattr(
            getattr(armature, "pose", None), "bones", ())]
        modifiers.append({
            "type": kind,
            "name": str(getattr(modifier, "name", "")),
            "use_deform_preserve_volume": bool(getattr(
                modifier, "use_deform_preserve_volume", False)),
            "use_vertex_groups": bool(getattr(
                modifier, "use_vertex_groups", True)),
            "use_bone_envelopes": bool(getattr(
                modifier, "use_bone_envelopes", False)),
            "armature": _id_source_identity(armature_data),
            "armature_action": armature_action,
            "armature_data_action": armature_data_action,
            "bones": bones, "pose_basis": pose_basis,
        })
    parents = []
    parent = getattr(obj, "parent", None)
    visited = set()
    while parent is not None:
        marker = id(parent)
        if marker in visited:
            return False, {}, f"{obj.name}: cyclic parenting"
        visited.add(marker)
        if tuple(getattr(parent, "constraints", ())):
            return False, {}, f"{obj.name}: constrained parent"
        parent_safe, parent_action, reason = _safe_action_identity(parent)
        if not parent_safe:
            return False, {}, f"{parent.name}: {reason}"
        parents.append({
            "identity": _id_source_identity(parent),
            "action": parent_action,
            "matrix_world": [[float(value) for value in row]
                             for row in getattr(parent, "matrix_world", ())],
            "matrix_parent_inverse": [
                [float(value) for value in row]
                for row in getattr(obj, "matrix_parent_inverse", ())],
        })
        parent = getattr(parent, "parent", None)
    deform_weights = ""
    if modifiers:
        deform_weights = _vertex_group_signature(
            obj, (str(group.name)
                  for group in getattr(obj, "vertex_groups", ())))
    return True, {
        "data": source, "action": action, "data_action": data_action,
        "modifiers": modifiers, "parents": parents,
        "deform_weights": deform_weights,
        "matrix_world": [[float(value) for value in row]
                         for row in getattr(obj, "matrix_world", ())],
    }, ""


def _mesh_uv_signature(mesh) -> str:
    layer = getattr(getattr(mesh, "uv_layers", None), "active", None)
    data = getattr(layer, "data", None)
    if data is None or not len(data):
        return ""
    values = np.empty(len(data) * 2, dtype="<f4")
    data.foreach_get("uv", values)
    return hashlib.sha256(memoryview(values).cast("B")).hexdigest()


def _vertex_group_signature(obj, group_names) -> str:
    wanted = {
        int(group.index): str(name)
        for name in sorted(set(group_names))
        for group in (getattr(obj, "vertex_groups", None).get(name),)
        if group is not None
    }
    if not wanted:
        return ""
    records = []
    for vertex in obj.data.vertices:
        for membership in vertex.groups:
            name = wanted.get(int(membership.group))
            if name is not None:
                records.append(
                    (int(vertex.index), name, float(membership.weight)))
    return cache_metadata.deterministic_hash(records)


def _scene_source_key(context, snapshot: ValidationSnapshot):
    """Return a fail-closed early Scene key after authoritative validation."""
    if (not getattr(snapshot, "deformables", ())
            or not getattr(snapshot, "geometry_fingerprint", "")):
        return None, "Incomplete validation snapshot"
    handler_owner = getattr(bpy.app, "handlers", None)
    handlers = tuple(getattr(handler_owner, "frame_change_pre", ())) + \
        tuple(getattr(handler_owner, "frame_change_post", ()))
    handler_identity = []
    for handler in handlers:
        function = getattr(handler, "__func__", handler)
        code = getattr(function, "__code__", None)
        module = str(getattr(function, "__module__", "") or "")
        qualified = str(getattr(
            function, "__qualname__",
            getattr(function, "__name__", "")) or "")
        if not module or not qualified or code is None:
            return None, "Unidentifiable frame-change script handler"
        handler_identity.append({
            "module": module,
            "qualified_name": qualified,
            "source": str(getattr(code, "co_filename", "") or ""),
            "first_line": int(getattr(code, "co_firstlineno", 0) or 0),
            "bytecode": hashlib.sha256(
                bytes(getattr(code, "co_code", b""))).hexdigest(),
        })
    objects = []
    ordered = sorted(
        (*snapshot.deformables, *(
            DeformableValidation(
                obj, None, "", StaticPinSnapshot(
                    False, "", obj.name, len(obj.data.vertices), ()),
                "", "", "COLLIDER")
            for obj in snapshot.collider_objs)),
        key=lambda entry: export_identity.export_uuid(entry.obj))
    for entry in ordered:
        obj = entry.obj
        safe, dependencies, reason = _safe_object_dependency_identity(obj)
        if not safe:
            return None, reason
        role = str(obj.cloth_next.role)
        row = {
            "uuid": export_identity.export_uuid(obj),
            "role": role,
            "source": dependencies,
            "motion": str(getattr(
                obj.cloth_next, "collider_motion", "STATIC")),
            "capture_mode": str(getattr(
                obj.cloth_next, "collider_capture_mode", "AUTO")),
            "samples": int(getattr(
                obj.cloth_next, "collider_samples_per_frame", 0)),
            "animation": _animation_signature(obj),
        }
        if role != "COLLIDER":
            pin_target = getattr(obj.cloth_next, "pin_target", None)
            row.update({
                "topology": entry.topology_signature,
                "shape": entry.shape_signature,
                "pins": list(entry.pin_membership.vertex_indices),
                "pin_mode": str(getattr(obj.cloth_next, "pin_mode", "")),
                "advanced_pin_motion": bool(getattr(
                    obj.cloth_next, "advanced_pin_motion_enabled", False))
                    or bool(getattr(
                        obj.cloth_next, "advanced_pin_targets", ())),
                "advanced_pin_targets": [
                    {"group": str(getattr(item, "vertex_group", "")),
                     "target": str(getattr(
                         getattr(item, "target", None), "name_full", "")),
                     "strength": float(getattr(item, "strength", 1.0)),
                     "animation": _animation_signature(item.target)
                     if getattr(item, "target", None) is not None else None}
                    for item in getattr(
                        obj.cloth_next, "advanced_pin_targets", ())],
                "pin_target": (
                    {"name": str(getattr(pin_target, "name_full", "") or
                                 getattr(pin_target, "name", "")),
                     "animation": _animation_signature(pin_target)}
                    if pin_target is not None else None),
                "soft_constraints": [
                    {"target": str(getattr(
                        getattr(item, "target", None), "name_full", "")),
                     "type": str(getattr(
                         item, "constraint_type", "LOCATION")),
                     "strength": float(getattr(item, "strength", 1.0)),
                     "animation": _animation_signature(item.target)
                     if getattr(item, "target", None) is not None else None}
                    for item in getattr(
                        obj.cloth_next, "soft_constraints", ())],
                "sewing": bool(getattr(
                    entry.material, "sewing_enabled", False)),
                "uv": (_mesh_uv_signature(obj.data)
                       if role == "CLOTH" else ""),
                "friction_regions": list(
                    _friction_region_settings(obj)),
                "friction_base": (
                    float(entry.material.surface_grip)
                    if _friction_region_settings(obj) else None),
                "friction_groups": _vertex_group_signature(
                    obj, (name for name, _value
                          in _friction_region_settings(obj))),
            })
        objects.append(row)
    identity = {
        # v2 captures Follow Animation Pins on the dense Collider timeline.
        # Keep pre-v2 Scene plans from silently restoring sparse Pin tracks.
        # v3 invalidates plans written before animated Collider frame
        # digests participated in the Scene payload identity.
        # v4 makes the dense capture timeline explicit and prevents Schema 2
        # from interpreting sub-frame samples as additional logical frames.
        "export_schema": SCENE_EXPORT_CACHE_SCHEMA,
        "solver_installation": _solver_selection_key(context),
        "export_uuid_schema": export_identity.EXPORT_UUID_SCHEMA_VERSION,
        "geometry": snapshot.geometry_fingerprint,
        "objects": objects,
        "frame_range": [
            snapshot.bake_range.start, snapshot.bake_range.end],
        "fps": _scene_fps(context),
        # Blender and other add-ons commonly register stable frame handlers.
        # Their presence alone must not disable the cache. Their executable
        # identity participates in the key so changing the handler set or
        # implementation invalidates the previous export.
        "frame_handlers": handler_identity,
    }
    return deterministic_key("scene", identity), "safe source identity"


def _validate_active_cloth() -> bool:
    """Debounced-timer entry point (Phase 11). Returns True when it validated.

    Skipped entirely while a Bake runs — the Bake owns validation then.
    """
    if run_active() or _pending_plan is not None or _pin_capture is not None:
        return False
    context = bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    try:
        cloth_obj, _collider = _enabled_objects_for_bake(context)
    except SceneValidationError:
        return False
    record = validation_state.record_for(cloth_obj)
    if record.state in (validation_state.ValidationState.VALID,
                        validation_state.ValidationState.VALIDATING):
        return False
    try:
        validate_scene(context)
    except (SceneValidationError, ClothNextError, MaterialValidationError):
        return True  # recorded as INVALID with its message; the panel shows it
    return True


def _depsgraph_update(context):
    view_layer=getattr(context,"view_layer",None)
    if view_layer is not None and hasattr(view_layer,"update"):view_layer.update()


def _force_capture_from_samples(samples, active_scalar_types, bake_range,
                                fps: float) -> ForceCapture:
    """Encode already evaluated Force states without revisiting frames."""
    initial = samples[0]
    tracks = (
        ("gravity", lambda state: state.gravity),
        ("wind", lambda state: state.wind),
        ("air-density", lambda state: (state.air_density,)),
        ("air-friction", lambda state: (state.air_friction,)),
        ("isotropic-air-friction", lambda state: (state.vertex_air_damp,)),
    )
    scalar_keys = {
        "air-density": "AIR_DENSITY",
        "air-friction": "AIR_FRICTION",
        "isotropic-air-friction": "VERTEX_AIR_DAMP",
    }
    dynamic = []
    for key, getter in tracks:
        if key in scalar_keys and scalar_keys[key] not in active_scalar_types:
            continue
        values = tuple(tuple(float(value) for value in getter(state))
                       for state in samples)
        if all(value == values[0] for value in values[1:]):
            continue
        entries = tuple(
            ((frame - bake_range.start) / float(fps), value, False)
            for frame, value in zip(
                range(bake_range.start, bake_range.end + 1), values))
        dynamic.append((key, entries))
    return ForceCapture(initial, frozenset(active_scalar_types), tuple(dynamic))


def _log_gravity_capture(samples, bake_range) -> None:
    """Record the effective Blender gravity and every frame where it changes."""
    logger = get_logger("solver.gravity")
    previous = None
    for frame, state in zip(
            range(bake_range.start, bake_range.end + 1), samples):
        gravity = tuple(float(value) for value in state.gravity)
        if gravity == previous:
            continue
        log_with_context(logger, 20, "Effective gravity captured", {
            "blender_frame": frame,
            "gravity_blender_xyz": gravity,
        })
        previous = gravity


def _capture_force_animation(context, bake_range: BakeFrameRange) -> ForceCapture:
    """Sample native Blender Force keyframes and build PPF dyn_param tracks."""
    direct = _capture_simple_force_fcurves(context, bake_range)
    if direct is not None:
        return direct
    scene = context.scene
    original = int(scene.frame_current)
    fps = _scene_fps(context)
    samples = []
    active_scalar_types = set()
    try:
        for frame in range(bake_range.start, bake_range.end + 1):
            if _cancel_event.is_set():
                raise SessionCancelled()
            # frame_set updates the dependency graph immediately; repeating a
            # view-layer update here needlessly evaluates the full rig again.
            scene.frame_set(frame)
            state, active = _force_state(context, wind_frame=frame)
            samples.append(state)
            active_scalar_types.update(active)
    finally:
        scene.frame_set(original)
        _depsgraph_update(context)
    _log_gravity_capture(samples, bake_range)
    return _force_capture_from_samples(
        samples, active_scalar_types, bake_range, fps)


def _simple_force_fcurves(obj):
    """Return an Action's safe scalar curves, or ``None`` to fail closed."""
    animation = getattr(obj, "animation_data", None)
    action = getattr(animation, "action", None)
    if (getattr(animation, "drivers", ()) or
            getattr(animation, "nla_tracks", ()) or
            getattr(obj, "constraints", ()) or
            getattr(obj, "parent", None) is not None):
        return None
    if action is None:
        return {}
    allowed = {
        "cloth_next.force.strength": "strength",
        "cloth_next.force.air_density": "air_density",
        "cloth_next.force.air_friction": "air_friction",
        "cloth_next.force.vertex_air_damp": "vertex_air_damp",
    }
    curves = {}
    for curve in getattr(action, "fcurves", ()):
        if getattr(curve, "modifiers", ()):
            return None
        path = str(getattr(curve, "data_path", ""))
        # Transform animation requires rotation-mode-specific matrix
        # reconstruction.  It deliberately stays on Blender's evaluated path.
        if path not in allowed or int(getattr(curve, "array_index", 0)) != 0:
            return None
        curves[allowed[path]] = curve
    return curves


def _capture_simple_force_fcurves(
        context, bake_range: BakeFrameRange) -> ForceCapture | None:
    """Evaluate provably independent Force scalar Actions without frame_set."""
    forces = _enabled_force_objects(context)
    curve_maps = {}
    if not forces:
        return None
    for obj in forces:
        force = obj.cloth_next.force
        if float(getattr(force, "wind_variation", 0.0)) != 0.0:
            return None
        curves = _simple_force_fcurves(obj)
        if curves is None:
            return None
        curve_maps[obj.name] = curves
    if not any(curve_maps.values()):
        return None
    samples = []
    active_scalar_types = set()
    for frame in range(bake_range.start, bake_range.end + 1):
        gravity_forces = [
            obj for obj in forces
            if str(obj.cloth_next.force.force_type) == "GRAVITY"]
        gravity = ([0.0, 0.0, 0.0] if gravity_forces else
                   list(context.scene.gravity)
                   if context.scene.use_gravity else [0.0, 0.0, 0.0])
        wind = [0.0, 0.0, 0.0]
        scalars = {
            kind: default for kind, (_field, default)
            in _SCALAR_FORCE_FIELDS.items()}
        for obj in forces:
            force = obj.cloth_next.force
            force_type = str(force.force_type)
            curves = curve_maps[obj.name]
            if force_type in _SCALAR_FORCE_FIELDS:
                field, _default = _SCALAR_FORCE_FIELDS[force_type]
                value = float(curves[field].evaluate(frame)
                              if field in curves else getattr(force, field))
                if not math.isfinite(value) or value < 0.0:
                    raise SceneValidationError(
                        f"{obj.name}: {field.replace('_', ' ')} is invalid.")
                if force_type not in active_scalar_types:
                    scalars[force_type] = 0.0
                    active_scalar_types.add(force_type)
                scalars[force_type] += value
                continue
            if force_type not in {"GRAVITY", "WIND"}:
                return None
            matrix = obj.matrix_world
            axis = [float(matrix[row][2]) for row in range(3)]
            length = math.sqrt(sum(value * value for value in axis))
            if not math.isfinite(length) or length <= 1e-12:
                raise SceneValidationError(
                    f"{obj.name}: Force Empty has an invalid local Z axis.")
            strength = float(
                curves["strength"].evaluate(frame)
                if "strength" in curves else force.strength)
            if not math.isfinite(strength) or strength < 0.0:
                raise SceneValidationError(
                    f"{obj.name}: Force strength is invalid.")
            target = gravity if force_type == "GRAVITY" else wind
            sign = -1.0 if force_type == "GRAVITY" else 1.0
            for index in range(3):
                target[index] += sign * strength * axis[index] / length
        samples.append(ForceState(
            tuple(gravity), tuple(wind),
            scalars["AIR_DENSITY"], scalars["AIR_FRICTION"],
            scalars["VERTEX_AIR_DAMP"]))
    if _export_timing_sink is not None:
        _export_timing_sink["force_fcurve_fast_path"] = 1.0
    _log_gravity_capture(samples, bake_range)
    return _force_capture_from_samples(
        samples, active_scalar_types, bake_range, _scene_fps(context))


def _capture_force_without_timeline(context, bake_range):
    """Return a direct/static capture, or ``None`` when Depsgraph is needed."""
    direct = _capture_simple_force_fcurves(context, bake_range)
    if direct is not None:
        return direct
    scene_animation = getattr(context.scene, "animation_data", None)
    if (getattr(scene_animation, "action", None) is not None
            or getattr(scene_animation, "drivers", ())
            or any(float(getattr(
                obj.cloth_next.force, "wind_variation", 0.0)) != 0.0
                   or _simple_force_fcurves(obj) is None
                   or bool(_simple_force_fcurves(obj))
                   for obj in _enabled_force_objects(context))):
        return None
    try:
        state, active = _force_state(context)
    except AttributeError:
        return None
    samples = [state] * bake_range.output_count
    return _force_capture_from_samples(
        samples, active, bake_range, _scene_fps(context))

def _solver_position(matrix,position):
    x,y,z=position
    return tuple(sum(float(matrix[row][column])*value for column,value in
                     enumerate((x,y,z,1.0))) for row in range(3))

def _capture_animated_pin(context,cloth_obj,bake_range,membership,
                          precomputed=None):
    advanced_rows = tuple(getattr(
        cloth_obj.cloth_next, "advanced_pin_targets", ()))
    advanced = bool(advanced_rows) or bool(getattr(
        cloth_obj.cloth_next, "advanced_pin_motion_enabled", False))
    constraints = tuple(getattr(cloth_obj.cloth_next, "soft_constraints", ()))
    mode=(PinMode.TARGET_OBJECT if advanced or constraints else
          PinMode(str(getattr(cloth_obj.cloth_next,"pin_mode","STATIC"))))
    common=dict(source_topology_signature=membership.source_topology_signature,
                mode=mode,bake_start=bake_range.start,bake_end=bake_range.end,
                fps=_scene_fps(context),
                pull_weights=membership.pull_weights)
    if not membership.enabled or mode is PinMode.STATIC:
        return StaticPinSnapshot(membership.enabled,membership.group_name,
            membership.source_object_id,membership.source_vertex_count,
            membership.vertex_indices,**common)
    if precomputed is not None:
        return StaticPinSnapshot(True,membership.group_name,membership.source_object_id,
            membership.source_vertex_count,membership.vertex_indices,
            samples=tuple(precomputed),**common)
    scene=context.scene; original=int(scene.frame_current); samples=[]
    target_initial = None
    constraint_initial = {}
    base_positions = None
    advanced_offsets = (_advanced_pin_offsets(cloth_obj, membership)
                        if advanced_rows else ())
    try:
        points = build_sample_plan(
            bake_range.start, bake_range.end,
            collider_samples=(COLLIDER_SAMPLES_PER_FRAME,))
        for point in points:
            frame = point.frame
            scene.frame_set(frame, subframe=point.subframe)
            _depsgraph_update(context)
            evaluated=cloth_obj.evaluated_get(context.evaluated_depsgraph_get())
            mesh=evaluated.to_mesh()
            try:
                if len(mesh.vertices)!=membership.source_vertex_count:
                    raise SceneValidationError(
                        f"Animated Pinning changed Cloth topology at frame {frame}: "
                        f"{membership.source_vertex_count} source vertices and {len(mesh.vertices)} evaluated vertices.")
                matrix=solver_world_matrix(tuple(tuple(row) for row in evaluated.matrix_world))
                positions=tuple(_solver_position(matrix,tuple(mesh.vertices[index].co))
                                for index in membership.vertex_indices)
                if advanced_rows:
                    if base_positions is None:
                        base_positions = positions
                    positions = _advanced_pin_positions(
                        base_positions, advanced_offsets,
                        context.evaluated_depsgraph_get(),
                        constraint_initial, cloth_obj.name)
                elif advanced:
                    target = getattr(cloth_obj.cloth_next, "pin_target", None)
                    if target is None:
                        raise SceneValidationError(
                            f"{cloth_obj.name}: Select a Target for Target Object Pinning.")
                    target_eval = target.evaluated_get(
                        context.evaluated_depsgraph_get())
                    target_matrix = np.asarray(solver_world_matrix(
                        tuple(tuple(row) for row in target_eval.matrix_world)),
                        dtype=np.float64)
                    if target_initial is None:
                        target_initial = target_matrix
                        base_positions = positions
                    positions = _transform_pin_positions(
                        base_positions, target_initial, target_matrix)
                if constraints:
                    if base_positions is None:
                        base_positions = positions
                    positions = _soft_constraint_positions(
                        base_positions, constraints,
                        context.evaluated_depsgraph_get(),
                        constraint_initial, cloth_obj.name)
                samples.append(AnimatedPinTargetSample(
                    float(point.position), positions))
            finally:evaluated.to_mesh_clear()
    finally:
        scene.frame_set(original); _depsgraph_update(context)
    return StaticPinSnapshot(True,membership.group_name,membership.source_object_id,
        membership.source_vertex_count,membership.vertex_indices,
        samples=tuple(samples),**common)


def _transform_pin_positions(base_positions, initial_matrix, current_matrix):
    """Drive a Pin group by a target transform while preserving offsets."""
    initial4 = np.eye(4, dtype=np.float64)
    current4 = np.eye(4, dtype=np.float64)
    initial4[:3, :4] = np.asarray(initial_matrix, dtype=np.float64)[:3, :4]
    current4[:3, :4] = np.asarray(current_matrix, dtype=np.float64)[:3, :4]
    try:
        delta = current4 @ np.linalg.inv(initial4)
    except np.linalg.LinAlgError as exc:
        raise SceneValidationError(
            "The Pin Target has a non-invertible transform.") from exc
    points = np.asarray(base_positions, dtype=np.float64)
    homogeneous = np.concatenate(
        (points, np.ones((len(points), 1), dtype=np.float64)), axis=1)
    transformed = homogeneous @ delta[:3, :4].T
    return tuple(tuple(float(value) for value in row) for row in transformed)


def _advanced_pin_offsets(cloth_obj, membership):
    """Resolve validated Advanced Pin groups to offsets in the union track."""
    lookup = {vertex: offset
              for offset, vertex in enumerate(membership.vertex_indices)}
    resolved = []
    for row_index, row in enumerate(getattr(
            cloth_obj.cloth_next, "advanced_pin_targets", ())):
        group = cloth_obj.vertex_groups.get(str(row.vertex_group))
        if group is None:
            raise SceneValidationError(
                f"Advanced Pin Group {row.vertex_group!r} no longer exists.")
        indices = _scan_pin_indices(cloth_obj, int(group.index))
        resolved.append((row_index, row,
                         tuple(lookup[index] for index in indices)))
    return tuple(resolved)


def _advanced_pin_positions(base_positions, resolved_rows, depsgraph,
                            initial_matrices, key_prefix):
    result = [tuple(point) for point in base_positions]
    for row_index, row, offsets in resolved_rows:
        target = getattr(row, "target", None)
        if target is None:
            raise SceneValidationError(
                f"Advanced Pin Motion: Select a Target for {row.vertex_group}.")
        evaluated = target.evaluated_get(depsgraph)
        current = np.asarray(solver_world_matrix(
            tuple(tuple(value) for value in evaluated.matrix_world)),
            dtype=np.float64)
        key = (key_prefix, "advanced", row_index)
        initial = initial_matrices.setdefault(key, current.copy())
        subgroup = tuple(base_positions[offset] for offset in offsets)
        transformed = _transform_pin_positions(subgroup, initial, current)
        for offset, point in zip(offsets, transformed):
            result[offset] = point
    return tuple(result)


def _soft_constraint_positions(base_positions, constraints, depsgraph,
                               initial_matrices, key_prefix):
    """Blend Target constraint candidates by their physical pull strengths."""
    from mathutils import Matrix, Vector

    base = tuple(tuple(float(value) for value in point)
                 for point in base_positions)
    weighted = np.zeros((len(base), 3), dtype=np.float64)
    total = 0.0
    for index, row in enumerate(constraints):
        target = getattr(row, "target", None)
        strength = float(getattr(row, "strength", 1.0))
        if target is None:
            raise SceneValidationError(
                "Soft Constraint: Select a Target Object in every row.")
        if not math.isfinite(strength) or strength <= 0.0:
            continue
        evaluated = target.evaluated_get(depsgraph)
        current = Matrix(solver_world_matrix(
            tuple(tuple(value) for value in evaluated.matrix_world))).to_4x4()
        key = (key_prefix, index)
        initial = initial_matrices.setdefault(key, current.copy())
        loc0, rot0, scale0 = initial.decompose()
        loc1, rot1, scale1 = current.decompose()
        kind = str(getattr(row, "constraint_type", "LOCATION"))
        candidate = []
        for point in base:
            value = Vector(point)
            if kind == "LOCATION":
                value = value + (loc1 - loc0)
            elif kind == "ROTATION":
                value = loc0 + (rot1 @ rot0.inverted()) @ (value - loc0)
            elif kind == "SCALE":
                local = rot0.inverted() @ (value - loc0)
                ratios = Vector(tuple(
                    scale1[i] / scale0[i] if abs(scale0[i]) > 1e-12 else 1.0
                    for i in range(3)))
                value = loc0 + rot0 @ Vector(tuple(
                    local[i] * ratios[i] for i in range(3)))
            candidate.append(tuple(float(component) for component in value))
        weighted += np.asarray(candidate, dtype=np.float64) * strength
        total += strength
    if total <= 0.0:
        raise SceneValidationError(
            "Soft Constraints need at least one row with Strength above zero.")
    return tuple(tuple(float(value) for value in point)
                 for point in weighted / total)


def _matrix_trs(matrix):
    """Decompose one solver-space matrix using Blender's evaluated math."""
    from mathutils import Matrix
    location, rotation, scale = Matrix(matrix).decompose()
    return ([float(value) for value in location],
            [float(rotation.w), float(rotation.x), float(rotation.y),
             float(rotation.z)],
            [float(value) for value in scale])


def _collider_motion_digest(frame_offsets, samples, *, dtype="<f8") -> str:
    """Hash the exact sampled motion without materializing a second full copy."""
    digest = hashlib.sha256()
    offsets = np.ascontiguousarray(frame_offsets, dtype="<f8")
    digest.update(memoryview(offsets).cast("B"))
    values = np.asarray(samples, dtype=dtype)
    if not values.flags.c_contiguous:
        values = np.ascontiguousarray(values)
    digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


COLLIDER_SAMPLES_PER_FRAME = 8
SCENE_EXPORT_CACHE_SCHEMA = 4
ANIMATED_COLLIDER_CAPTURE_LIMIT_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AnimatedColliderCaptureWarning:
    """Non-blocking estimate for an unusually large Collider capture."""

    collider_name: str
    vertex_count: int
    samples_per_frame: int
    total_bytes: int

    @property
    def size_label(self) -> str:
        if self.total_bytes >= 1024 ** 3:
            return f"{self.total_bytes / float(1024 ** 3):.2f} GiB"
        return f"{self.total_bytes / float(1024 ** 2):.0f} MiB"


def _collider_sample_points(bake_range: BakeFrameRange, fps: float,
                            samples_per_frame: int = COLLIDER_SAMPLES_PER_FRAME):
    """Dense evaluated samples, including both Bake endpoints exactly."""
    timeline = build_collider_timeline(
        bake_range.start, bake_range.end,
        samples_per_frame=int(samples_per_frame), fps=float(fps))
    return tuple(
        (point.frame, point.subframe, time)
        for point, time in zip(timeline.points, timeline.times))


def _collider_animation_metadata(bake_range: BakeFrameRange, fps: float,
                                 samples_per_frame: int) -> dict:
    timeline = build_collider_timeline(
        bake_range.start, bake_range.end,
        samples_per_frame=samples_per_frame, fps=fps)
    return {
        "time": list(timeline.times),
        "_sample_frame_offset": list(timeline.frame_offsets),
        "_logical_frame_count": timeline.logical_frame_count,
        "_samples_per_frame": timeline.samples_per_frame,
        "_capture_fps": timeline.fps,
    }


def _animated_collider_capture_bytes(vertex_count: int,
                                     bake_range: BakeFrameRange,
                                     samples_per_frame: int) -> int:
    sample_count = len(_collider_sample_points(
        bake_range, 1, samples_per_frame))
    return int(vertex_count) * sample_count * 3 * 4


def animated_collider_capture_warning(
        collider_objs, bake_range: BakeFrameRange
) -> AnimatedColliderCaptureWarning | None:
    """Estimate raw animated-Collider storage without preventing a Bake."""
    rows = []
    total = 0
    for obj in collider_objs:
        if str(getattr(obj.cloth_next, "collider_motion", "STATIC")) != "ANIMATED":
            continue
        vertex_count = len(getattr(getattr(obj, "data", None), "vertices", ()))
        samples = int(getattr(obj.cloth_next, "collider_samples_per_frame",
                              COLLIDER_SAMPLES_PER_FRAME))
        size = _animated_collider_capture_bytes(
            vertex_count, bake_range, samples)
        total += size
        rows.append((obj.name, vertex_count, samples, size))
    if total <= ANIMATED_COLLIDER_CAPTURE_LIMIT_BYTES:
        return None
    name, vertices, samples, _largest = max(rows, key=lambda row: row[3])
    return AnimatedColliderCaptureWarning(
        collider_name=name, vertex_count=vertices,
        samples_per_frame=samples, total_bytes=total)


def _collider_polygon_topology(mesh) -> tuple[tuple[int, ...], ...]:
    """Evaluated topology independent of Blender's changing tessellation."""
    return tuple(tuple(int(index) for index in polygon.vertices)
                 for polygon in mesh.polygons)


def _collider_topology_arrays(mesh, buffers=None):
    """Read evaluated polygon topology through Blender's bulk API.

    Large animated character colliders used to construct a Python tuple for
    every polygon at every motion sample.  Reusing three compact arrays keeps
    the same exact topology validation while moving the copying into Blender's
    C-level ``foreach_get`` implementation.
    """
    polygon_count = len(mesh.polygons)
    loop_count = len(mesh.loops)
    if (buffers is None or len(buffers[0]) != polygon_count or
            len(buffers[2]) != loop_count):
        starts = np.empty(polygon_count, dtype=np.int32)
        totals = np.empty(polygon_count, dtype=np.int32)
        vertices = np.empty(loop_count, dtype=np.int32)
    else:
        starts, totals, vertices = buffers
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    mesh.loops.foreach_get("vertex_index", vertices)
    return starts, totals, vertices


def _collider_array_topology_change(expected_vertex_count: int,
                                    expected, vertex_count: int,
                                    current) -> str:
    if vertex_count != expected_vertex_count:
        return (f"vertex count changed from {expected_vertex_count} to "
                f"{vertex_count}")
    if any(left.shape != right.shape or not np.array_equal(left, right)
           for left, right in zip(expected, current)):
        return (f"polygon topology changed from {len(expected[0])} to "
                f"{len(current[0])} polygons")
    return ""


def _collider_topology_change(expected_vertex_count: int,
                              expected_polygons: tuple[tuple[int, ...], ...],
                              vertex_count: int,
                              polygons: tuple[tuple[int, ...], ...]) -> str:
    if vertex_count != expected_vertex_count:
        return (f"vertex count changed from {expected_vertex_count} to "
                f"{vertex_count}")
    if polygons != expected_polygons:
        return (f"polygon topology changed from {len(expected_polygons)} to "
                f"{len(polygons)} polygons")
    return ""


def _collider_transform_only_is_safe(collider_obj) -> bool:
    """Conservatively prove that samples cannot alter local mesh positions."""
    data = getattr(collider_obj, "data", None)
    if getattr(data, "shape_keys", None) is not None:
        return False
    if getattr(data, "animation_data", None) is not None:
        return False
    # Topology-preserving modifiers may still move vertices. Unknown and
    # topology-changing modifiers are unsafe as well, so AUTO accepts no
    # enabled modifier at all.
    return not any(bool(getattr(modifier, "show_viewport", True))
                   for modifier in getattr(collider_obj, "modifiers", ()))


_TOPOLOGY_PRESERVING_MODIFIERS = frozenset({
    "ARMATURE", "CORRECTIVE_SMOOTH", "DISPLACE", "LAPLACIANSMOOTH",
    "LATTICE", "MESH_DEFORM", "SMOOTH", "SURFACE_DEFORM",
})


def _collider_topology_check_mode(collider_obj) -> str:
    """Use vertex-count checks only for a completely known-safe stack."""
    modifiers = tuple(
        modifier for modifier in getattr(collider_obj, "modifiers", ())
        if bool(getattr(modifier, "show_viewport", True)))
    return ("VERTEX_COUNT" if all(
        str(getattr(modifier, "type", "")) in
        _TOPOLOGY_PRESERVING_MODIFIERS for modifier in modifiers)
        else "FULL")


def _effective_collider_capture_mode(collider_obj) -> str:
    requested = str(getattr(
        collider_obj.cloth_next, "collider_capture_mode", "AUTO"))
    if requested == "DEFORMING":
        return "DEFORMING"
    safe = _collider_transform_only_is_safe(collider_obj)
    if requested == "TRANSFORM_ONLY" and not safe:
        raise SceneValidationError(
            f'Collider "{collider_obj.name}" cannot use Transform Only because '
            "its mesh, Shape Keys, or modifier stack can deform geometry. "
            "Use Auto or Deforming.")
    return "TRANSFORM_ONLY" if safe else "DEFORMING"


def _capture_transform_only_collider_motion(
        context, collider_obj, bake_range: BakeFrameRange
) -> ColliderMotionCapture:
    """Export one mesh and sample matrices without per-sample ``to_mesh()``."""
    scene = context.scene
    original_frame = int(scene.frame_current)
    original_subframe = float(getattr(scene, "frame_subframe", 0.0))
    sample_points = _collider_sample_points(
        bake_range, _scene_fps(context),
        int(getattr(collider_obj.cloth_next,
                    "collider_samples_per_frame", COLLIDER_SAMPLES_PER_FRAME)))
    samples_per_frame = int(getattr(
        collider_obj.cloth_next, "collider_samples_per_frame",
        COLLIDER_SAMPLES_PER_FRAME))
    metadata = _collider_animation_metadata(
        bake_range, _scene_fps(context), samples_per_frame)
    times = metadata["time"]
    frame_offsets = metadata["_sample_frame_offset"]
    matrices = []
    vertices = triangles = None
    try:
        for offset, (frame, subframe, _time) in enumerate(sample_points):
            if _cancel_event.is_set():
                raise SessionCancelled()
            scene.frame_set(frame, subframe=subframe)
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = collider_obj.evaluated_get(depsgraph)
            world = tuple(tuple(float(value) for value in row)
                          for row in evaluated.matrix_world)
            if not matrix_is_finite_and_invertible(world):
                raise SceneValidationError(
                    f'Collider "{collider_obj.name}" has an invalid transform '
                    f'at frame {frame + subframe:g}.')
            matrices.append(solver_world_matrix(world))
            if offset == 0:
                vertices, triangles = _extract_mesh(
                    collider_obj, depsgraph, needs_edges=False)
            if offset == 0 or offset + 1 == len(sample_points) or subframe == 0:
                shared_controller.update(
                    status_message=(f"Capturing collider transforms · frame "
                                    f"{frame + subframe:g} / {bake_range.end}"),
                    activity_code=BakeActivity.CAPTURING_COLLIDER_MOTION,
                    current_frame=frame, progress_current=offset + 1,
                    progress_total=len(sample_points))
        translations, quaternions, scales = [], [], []
        for matrix in matrices:
            translation, quaternion, scale = _matrix_trs(matrix)
            if (quaternions and sum(a * b for a, b in
                                    zip(quaternions[-1], quaternion)) < 0.0):
                quaternion = [-value for value in quaternion]
            translations.append(translation)
            quaternions.append(quaternion)
            scales.append(scale)
        return ColliderMotionCapture(
            "RIGID_ANIMATED", vertices, triangles, matrices[0],
            {**metadata, "translation": translations,
             "quaternion": quaternions, "scale": scales,
             "segments": [
                 {"interpolation": "LINEAR",
                  "handle_right": [1.0 / 3.0, 0.0],
                  "handle_left": [2.0 / 3.0, 1.0]}
                 for _index in range(len(sample_points) - 1)]},
            content_digest=_collider_motion_digest(frame_offsets, matrices))
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)


def _capture_collider_motion(context, collider_obj,
                             bake_range: BakeFrameRange) -> ColliderMotionCapture:
    """Capture and classify one animated Collider on Blender's main thread."""
    if _effective_collider_capture_mode(collider_obj) == "TRANSFORM_ONLY":
        return _capture_transform_only_collider_motion(
            context, collider_obj, bake_range)
    scene = context.scene
    original_frame = int(scene.frame_current)
    original_subframe = float(getattr(scene, "frame_subframe", 0.0))
    sample_points = _collider_sample_points(
        bake_range, _scene_fps(context),
        int(getattr(collider_obj.cloth_next,
                    "collider_samples_per_frame", COLLIDER_SAMPLES_PER_FRAME)))
    sample_count = len(sample_points)
    samples_per_frame = int(getattr(
        collider_obj.cloth_next, "collider_samples_per_frame",
        COLLIDER_SAMPLES_PER_FRAME))
    metadata = _collider_animation_metadata(
        bake_range, _scene_fps(context), samples_per_frame)
    times = metadata["time"]
    frame_offsets = metadata["_sample_frame_offset"]
    reference_vertices = None
    reference_triangles = None
    reference_topology = None
    topology_buffers = None
    matrices = []
    local_samples = None
    temporary_path = None
    deforming = False
    motion_hasher = hashlib.sha256()
    topology_check_mode = _collider_topology_check_mode(collider_obj)
    try:
        for offset, (frame, subframe, _time) in enumerate(sample_points):
            if _cancel_event.is_set():
                raise SessionCancelled()
            # A frame-level update is sufficient for visible progress. Avoid
            # putting every motion sub-sample ahead of the later job-bound
            # readiness command in the Companion socket.
            if offset == 0 or offset + 1 == sample_count or subframe == 0.0:
                shared_controller.update(
                    status_message=(f"Capturing collider animation · frame "
                                    f"{frame + subframe:g} / {bake_range.end}"),
                    activity_code=BakeActivity.CAPTURING_COLLIDER_MOTION,
                    current_frame=frame, progress_current=offset + 1,
                    progress_total=sample_count)
            scene.frame_set(frame, subframe=subframe)
            # frame_set() already evaluates the dependency graph. Repeating
            # view_layer.update() doubled the dominant cost on long,
            # deforming character Collider captures.
            evaluated = collider_obj.evaluated_get(
                context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                count = len(mesh.vertices)
                topology = (_collider_topology_arrays(mesh, topology_buffers)
                            if (reference_topology is None
                                or topology_check_mode == "FULL") else None)
                if count == 0 or (topology is not None and not len(topology[0])):
                    raise SceneValidationError(
                        f'Collider "{collider_obj.name}" has an empty '
                        f'evaluated mesh at frame {frame + subframe:g}.')
                detail = ""
                if reference_topology is not None:
                    if count != len(reference_vertices):
                        detail = (f"vertex count changed from "
                                  f"{len(reference_vertices)} to {count}")
                    elif topology is not None:
                        detail = _collider_array_topology_change(
                            len(reference_vertices), reference_topology,
                            count, topology)
                if detail:
                    raise SceneValidationError(
                        f'Collider "{collider_obj.name}" changes topology at '
                        f'frame {frame + subframe:g}: {detail}. '
                        f'Animated colliders must keep a '
                        f'consistent mesh structure.')
                local = np.empty((count, 3), dtype=np.float32)
                mesh.vertices.foreach_get("co", local.reshape(-1))
                if not np.isfinite(local).all():
                    raise SceneValidationError(
                        f'Collider "{collider_obj.name}" contains non-finite '
                        f'positions at frame {frame + subframe:g}.')
                world = tuple(tuple(float(value) for value in row)
                              for row in evaluated.matrix_world)
                if not matrix_is_finite_and_invertible(world):
                    raise SceneValidationError(
                        f'Collider "{collider_obj.name}" has an invalid '
                        f'transform at frame {frame + subframe:g}.')
                solver_matrix = solver_world_matrix(world)
                matrices.append(solver_matrix)
                if reference_vertices is None:
                    # Freeze the first tessellation. Blender may flip a quad
                    # diagonal as an Armature makes it non-planar; the polygon
                    # loop is unchanged and remains the authoritative topology.
                    mesh.calc_loop_triangles()
                    triangles = tuple(tuple(int(i) for i in tri.vertices)
                                      for tri in mesh.loop_triangles)
                    if not triangles:
                        raise SceneValidationError(
                            f'Collider "{collider_obj.name}" cannot be '
                            'triangulated for collision.')
                    reference_vertices = local.copy()
                    reference_triangles = triangles
                    reference_topology = tuple(array.copy()
                                               for array in topology)
                    temporary_path = (Path(bpy.app.tempdir)
                        / f"cloth_next_collider_{uuid_module.uuid4().hex}.bin")
                    local_samples = np.memmap(
                        temporary_path, dtype="<f4", mode="w+",
                        shape=(sample_count, count, 3))
                elif not deforming:
                    # Classify while Blender is already handing us this
                    # sample.  A second full memmap scan after N/N made a
                    # completed capture look hung for large character meshes.
                    deforming = not np.allclose(
                        local, reference_vertices, rtol=0.0, atol=1e-6)
                if topology is not None and reference_topology is not None:
                    topology_buffers = topology
                # Store solver-world positions immediately.  This replaces
                # the former second pass over every frame and every vertex.
                transform = np.asarray(solver_matrix, dtype=np.float64)
                world_sample = np.asarray(
                    local @ transform[:3, :3].T + transform[:3, 3],
                    dtype="<f4")
                local_samples[offset] = world_sample
                motion_hasher.update(struct.pack("<d", float(frame_offsets[offset])))
                motion_hasher.update(memoryview(world_sample).cast("B"))
            finally:
                evaluated.to_mesh_clear()

        assert reference_vertices is not None and reference_triangles is not None
        assert local_samples is not None
        if not deforming:
            translations, quaternions, scales = [], [], []
            for matrix in matrices:
                translation, quaternion, scale = _matrix_trs(matrix)
                if (quaternions and sum(a * b for a, b in
                                        zip(quaternions[-1], quaternion)) < 0.0):
                    quaternion = [-value for value in quaternion]
                translations.append(translation)
                quaternions.append(quaternion)
                scales.append(scale)
            result = ColliderMotionCapture(
                "RIGID_ANIMATED",
                tuple(tuple(float(value) for value in row)
                      for row in reference_vertices),
                reference_triangles, matrices[0],
                {**metadata, "translation": translations,
                 "quaternion": quaternions, "scale": scales,
                 "segments": [
                     {"interpolation": "LINEAR",
                      "handle_right": [1.0 / 3.0, 0.0],
                      "handle_left": [2.0 / 3.0, 1.0]}
                     for _index in range(sample_count - 1)]},
                content_digest=motion_hasher.hexdigest())
            local_samples._mmap.close()
            temporary_path.unlink(missing_ok=True)
            return result

        local_samples.flush()
        identity = tuple(tuple(1.0 if row == column else 0.0
                               for column in range(4)) for row in range(4))
        return ColliderMotionCapture(
            "DEFORMING_ANIMATED",
            tuple(tuple(float(value) for value in row)
                  for row in local_samples[0]),
            reference_triangles, identity,
            {**metadata, "vert_frames": local_samples},
            temporary_path, content_digest=motion_hasher.hexdigest())
    except Exception:
        mapping = getattr(local_samples, "_mmap", None)
        if mapping is not None:
            mapping.close()
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)


def _capture_animated_colliders_shared(
        context, collider_objs, bake_range: BakeFrameRange
) -> dict[str, ColliderMotionCapture]:
    """Capture every animated Collider in one deterministic timeline pass.

    This is the production multi-Collider path.  Fractions in
    :func:`build_sample_plan` provide exact de-duplication even when Colliders
    use different sample rates.  Blender is touched only by this main-thread
    function and ``frame_set`` is called once per union point.
    """
    colliders = tuple(collider_objs)
    if not colliders:
        return {}
    fps = _scene_fps(context)
    rates = {
        obj.name: int(getattr(obj.cloth_next, "collider_samples_per_frame",
                              COLLIDER_SAMPLES_PER_FRAME))
        for obj in colliders
    }
    timelines = {
        obj.name: build_collider_timeline(
            bake_range.start, bake_range.end,
            samples_per_frame=rates[obj.name], fps=fps)
        for obj in colliders
    }
    required = {
        obj.name: {
            point.position: index
            for index, point in enumerate(build_sample_plan(
                bake_range.start, bake_range.end,
                collider_samples=(rates[obj.name],),
                include_integer_frames=False))
        } for obj in colliders
    }
    union = build_sample_plan(
        bake_range.start, bake_range.end,
        collider_samples=tuple(rates.values()), include_integer_frames=False)
    states = {}
    original_frame = int(context.scene.frame_current)
    original_subframe = float(getattr(context.scene, "frame_subframe", 0.0))
    captures: dict[str, ColliderMotionCapture] = {}
    try:
        for obj in colliders:
            mode = _effective_collider_capture_mode(obj)
            timeline = timelines[obj.name]
            count = len(timeline.points)
            states[obj.name] = {
                "obj": obj, "mode": mode, "matrices": [],
                "vertices": None, "triangles": None, "topology": None,
                "topology_buffers": None, "samples": None, "path": None,
                "deforming": False,
                "topology_mode": _collider_topology_check_mode(obj),
                "sample_count": count,
                "metadata": {
                    "time": list(timeline.times),
                    "_sample_frame_offset": list(timeline.frame_offsets),
                    "_logical_frame_count": timeline.logical_frame_count,
                    "_samples_per_frame": timeline.samples_per_frame,
                    "_capture_fps": timeline.fps,
                },
            }
        if _export_timing_sink is not None:
            _export_timing_sink["sample_plan_points"] = float(len(union))
        capture_started = time.perf_counter()
        for point_index, point in enumerate(union):
            if _cancel_event.is_set():
                raise SessionCancelled()
            context.scene.frame_set(point.frame, subframe=point.subframe)
            if _export_timing_sink is not None:
                _export_timing_sink["frame_set_count"] = (
                    _export_timing_sink.get("frame_set_count", 0.0) + 1.0)
                _export_timing_sink["depsgraph_evaluation_count"] = (
                    _export_timing_sink.get(
                        "depsgraph_evaluation_count", 0.0) + 1.0)
            depsgraph = context.evaluated_depsgraph_get()
            key = point.position
            for obj in colliders:
                sample_index = required[obj.name].get(key)
                if sample_index is None:
                    continue
                state = states[obj.name]
                evaluated = obj.evaluated_get(depsgraph)
                if _export_timing_sink is not None:
                    _export_timing_sink["evaluated_get_count"] = (
                        _export_timing_sink.get(
                            "evaluated_get_count", 0.0) + 1.0)
                    _export_timing_sink["captured_collider_samples"] = (
                        _export_timing_sink.get(
                            "captured_collider_samples", 0.0) + 1.0)
                world = tuple(tuple(float(value) for value in row)
                              for row in evaluated.matrix_world)
                if not matrix_is_finite_and_invertible(world):
                    raise SceneValidationError(
                        f'Collider "{obj.name}" has an invalid transform at '
                        f'frame {float(point.position):g}.')
                solver_matrix = solver_world_matrix(world)
                state["matrices"].append(solver_matrix)
                if state["mode"] == "TRANSFORM_ONLY":
                    if state["vertices"] is None:
                        state["vertices"], state["triangles"] = _extract_mesh(
                            obj, depsgraph, needs_edges=False)
                    continue
                mesh = evaluated.to_mesh()
                if _export_timing_sink is not None:
                    _export_timing_sink["to_mesh_count"] = (
                        _export_timing_sink.get("to_mesh_count", 0.0) + 1.0)
                try:
                    vertex_count = len(mesh.vertices)
                    topology = (
                        _collider_topology_arrays(
                            mesh, state["topology_buffers"])
                        if (state["topology"] is None or
                            state["topology_mode"] == "FULL") else None)
                    if vertex_count == 0 or (
                            topology is not None and not len(topology[0])):
                        raise SceneValidationError(
                            f'Collider "{obj.name}" has an empty evaluated '
                            f'mesh at frame {float(point.position):g}.')
                    if state["vertices"] is not None:
                        detail = ""
                        if vertex_count != len(state["vertices"]):
                            detail = (
                                f"vertex count changed from "
                                f"{len(state['vertices'])} to {vertex_count}")
                        elif topology is not None:
                            detail = _collider_array_topology_change(
                                len(state["vertices"]), state["topology"],
                                vertex_count, topology)
                        if detail:
                            raise SceneValidationError(
                                f'Collider "{obj.name}" changes topology at '
                                f'frame {float(point.position):g}: {detail}.')
                    local = np.empty((vertex_count, 3), dtype=np.float32)
                    mesh.vertices.foreach_get("co", local.reshape(-1))
                    if not np.isfinite(local).all():
                        raise SceneValidationError(
                            f'Collider "{obj.name}" contains non-finite '
                            f'positions at frame {float(point.position):g}.')
                    if state["vertices"] is None:
                        mesh.calc_loop_triangles()
                        triangles = tuple(
                            tuple(int(i) for i in tri.vertices)
                            for tri in mesh.loop_triangles)
                        if not triangles:
                            raise SceneValidationError(
                                f'Collider "{obj.name}" cannot be triangulated.')
                        state["vertices"] = local.copy()
                        state["triangles"] = triangles
                        state["topology"] = tuple(
                            array.copy() for array in topology)
                        path = (Path(bpy.app.tempdir) /
                                f"cloth_next_collider_"
                                f"{uuid_module.uuid4().hex}.bin")
                        state["path"] = path
                        state["samples"] = np.memmap(
                            path, dtype="<f4", mode="w+",
                            shape=(state["sample_count"], vertex_count, 3))
                    else:
                        state["deforming"] |= not np.allclose(
                            local, state["vertices"], rtol=0.0, atol=1e-6)
                    if topology is not None:
                        state["topology_buffers"] = topology
                    transform = np.asarray(solver_matrix, dtype=np.float64)
                    state["samples"][sample_index] = (
                        local @ transform[:3, :3].T + transform[:3, 3])
                finally:
                    evaluated.to_mesh_clear()
            if point_index == 0 or point_index + 1 == len(union):
                shared_controller.update(
                    status_message=(
                        f"Capturing animated Colliders · frame "
                        f"{float(point.position):g} / {bake_range.end}"),
                    activity_code=BakeActivity.CAPTURING_COLLIDER_MOTION,
                    current_frame=point.frame,
                    progress_current=point_index + 1,
                    progress_total=len(union))
        for obj in colliders:
            state = states[obj.name]
            matrices = state["matrices"]
            metadata = state["metadata"]
            times = metadata["time"]
            frame_offsets = metadata["_sample_frame_offset"]
            if state["mode"] == "TRANSFORM_ONLY" or not state["deforming"]:
                translations, quaternions, scales = [], [], []
                for matrix in matrices:
                    translation, quaternion, scale = _matrix_trs(matrix)
                    if (quaternions and sum(
                            a * b for a, b in
                            zip(quaternions[-1], quaternion)) < 0.0):
                        quaternion = [-value for value in quaternion]
                    translations.append(translation)
                    quaternions.append(quaternion)
                    scales.append(scale)
                captures[obj.name] = ColliderMotionCapture(
                    "RIGID_ANIMATED", state["vertices"], state["triangles"],
                    matrices[0],
                    {**metadata, "translation": translations,
                     "quaternion": quaternions, "scale": scales,
                     "segments": [{"interpolation": "LINEAR",
                                   "handle_right": [1.0 / 3.0, 0.0],
                                   "handle_left": [2.0 / 3.0, 1.0]}
                                  for _ in range(len(times) - 1)]},
                    content_digest=_collider_motion_digest(
                        frame_offsets, matrices))
                samples = state["samples"]
                if samples is not None:
                    samples._mmap.close()
                    state["path"].unlink(missing_ok=True)
            else:
                state["samples"].flush()
                identity = tuple(tuple(
                    1.0 if row == column else 0.0 for column in range(4))
                    for row in range(4))
                captures[obj.name] = ColliderMotionCapture(
                    "DEFORMING_ANIMATED",
                    tuple(tuple(float(value) for value in row)
                          for row in state["samples"][0]),
                    state["triangles"], identity,
                    {**metadata, "vert_frames": state["samples"]},
                    state["path"],
                    content_digest=_collider_motion_digest(
                        frame_offsets, state["samples"], dtype="<f4"))
        if _export_timing_sink is not None:
            _export_timing_sink["capture_seconds"] = (
                _export_timing_sink.get("capture_seconds", 0.0) +
                time.perf_counter() - capture_started)
        return captures
    except Exception:
        for capture in captures.values():
            capture.cleanup()
        for state in states.values():
            samples = state.get("samples")
            mapping = getattr(samples, "_mmap", None)
            if mapping is not None:
                mapping.close()
            path = state.get("path")
            if path is not None:
                path.unlink(missing_ok=True)
        raise
    finally:
        context.scene.frame_set(original_frame, subframe=original_subframe)


def _begin_collider_pump(colliders, bake_range, fps):
    """Mutable main-thread state used by the asynchronous union pump."""
    result = {}
    for obj in colliders:
        rate = int(getattr(
            obj.cloth_next, "collider_samples_per_frame",
            COLLIDER_SAMPLES_PER_FRAME))
        timeline = build_collider_timeline(
            bake_range.start, bake_range.end,
            samples_per_frame=rate, fps=fps)
        points = timeline.points
        result[obj.name] = {
            "obj": obj, "points": {
                point.position: index for index, point in enumerate(points)},
            "metadata": {
                "time": list(timeline.times),
                "_sample_frame_offset": list(timeline.frame_offsets),
                "_logical_frame_count": timeline.logical_frame_count,
                "_samples_per_frame": timeline.samples_per_frame,
                "_capture_fps": timeline.fps,
            },
            "mode": _effective_collider_capture_mode(obj),
            "matrices": [], "vertices": None, "triangles": None,
            "topology": None, "topology_buffers": None,
            "topology_mode": _collider_topology_check_mode(obj),
            "samples": None, "path": None, "deforming": False,
            "sample_count": len(points),
        }
    return result


def _pump_collider_point(depsgraph, point, states):
    """Capture all Colliders needing this exact rational sample point."""
    count = evaluated_count = mesh_count = 0
    for state in states.values():
        sample_index = state["points"].get(point.position)
        if sample_index is None:
            continue
        count += 1
        obj = state["obj"]
        evaluated = obj.evaluated_get(depsgraph)
        evaluated_count += 1
        world = tuple(tuple(float(value) for value in row)
                      for row in evaluated.matrix_world)
        if not matrix_is_finite_and_invertible(world):
            raise SceneValidationError(
                f'Collider "{obj.name}" has an invalid transform at '
                f'frame {float(point.position):g}.')
        solver_matrix = solver_world_matrix(world)
        state["matrices"].append(solver_matrix)
        if state["mode"] == "TRANSFORM_ONLY":
            if state["vertices"] is None:
                state["vertices"], state["triangles"] = _extract_mesh(
                    obj, depsgraph, needs_edges=False)
                evaluated_count += 1
                mesh_count += 1
            continue
        mesh = evaluated.to_mesh()
        mesh_count += 1
        try:
            vertex_count = len(mesh.vertices)
            topology = (
                _collider_topology_arrays(mesh, state["topology_buffers"])
                if (state["topology"] is None
                    or state["topology_mode"] == "FULL") else None)
            if vertex_count == 0 or (
                    topology is not None and not len(topology[0])):
                raise SceneValidationError(
                    f'Collider "{obj.name}" has an empty evaluated mesh.')
            if state["vertices"] is not None:
                detail = ""
                if vertex_count != len(state["vertices"]):
                    detail = (
                        f"vertex count changed from "
                        f"{len(state['vertices'])} to {vertex_count}")
                elif topology is not None:
                    detail = _collider_array_topology_change(
                        len(state["vertices"]), state["topology"],
                        vertex_count, topology)
                if detail:
                    raise SceneValidationError(
                        f'Collider "{obj.name}" changes topology at '
                        f'frame {float(point.position):g}: {detail}.')
            local = np.empty((vertex_count, 3), dtype=np.float32)
            mesh.vertices.foreach_get("co", local.reshape(-1))
            if not np.isfinite(local).all():
                raise SceneValidationError(
                    f'Collider "{obj.name}" contains non-finite positions.')
            if state["vertices"] is None:
                mesh.calc_loop_triangles()
                triangles = tuple(
                    tuple(int(i) for i in tri.vertices)
                    for tri in mesh.loop_triangles)
                if not triangles:
                    raise SceneValidationError(
                        f'Collider "{obj.name}" cannot be triangulated.')
                state["vertices"] = local.copy()
                state["triangles"] = triangles
                state["topology"] = tuple(
                    array.copy() for array in topology)
                path = (Path(bpy.app.tempdir) /
                        f"cloth_next_collider_{uuid_module.uuid4().hex}.bin")
                state["path"] = path
                state["samples"] = np.memmap(
                    path, dtype="<f4", mode="w+",
                    shape=(state["sample_count"], vertex_count, 3))
            else:
                state["deforming"] |= not np.allclose(
                    local, state["vertices"], rtol=0.0, atol=1e-6)
            if topology is not None:
                state["topology_buffers"] = topology
            transform = np.asarray(solver_matrix, dtype=np.float64)
            state["samples"][sample_index] = (
                local @ transform[:3, :3].T + transform[:3, 3])
        finally:
            evaluated.to_mesh_clear()
    return count, evaluated_count, mesh_count


def _finish_collider_pump(states):
    captures = {}
    for name, state in states.items():
        matrices = state["matrices"]
        metadata = state["metadata"]
        frame_offsets = metadata["_sample_frame_offset"]
        if state["mode"] == "TRANSFORM_ONLY" or not state["deforming"]:
            translations, quaternions, scales = [], [], []
            for matrix in matrices:
                translation, quaternion, scale = _matrix_trs(matrix)
                if (quaternions and sum(
                        a * b for a, b in
                        zip(quaternions[-1], quaternion)) < 0.0):
                    quaternion = [-value for value in quaternion]
                translations.append(translation)
                quaternions.append(quaternion)
                scales.append(scale)
            captures[name] = ColliderMotionCapture(
                "RIGID_ANIMATED", state["vertices"], state["triangles"],
                matrices[0],
                {**metadata, "translation": translations,
                 "quaternion": quaternions, "scale": scales,
                 "segments": [{"interpolation": "LINEAR",
                               "handle_right": [1.0 / 3.0, 0.0],
                               "handle_left": [2.0 / 3.0, 1.0]}
                              for _ in range(len(matrices) - 1)]},
                content_digest=_collider_motion_digest(
                    frame_offsets, matrices))
            if state["samples"] is not None:
                state["samples"]._mmap.close()
                state["path"].unlink(missing_ok=True)
        else:
            state["samples"].flush()
            identity = tuple(tuple(
                1.0 if row == column else 0.0 for column in range(4))
                for row in range(4))
            captures[name] = ColliderMotionCapture(
                "DEFORMING_ANIMATED",
                tuple(tuple(float(value) for value in row)
                      for row in state["samples"][0]),
                state["triangles"], identity,
                {**metadata, "vert_frames": state["samples"]}, state["path"],
                content_digest=_collider_motion_digest(
                    frame_offsets, state["samples"], dtype="<f4"))
    return captures


def _cleanup_collider_pump(states):
    for state in states.values():
        samples = state.get("samples")
        mapping = getattr(samples, "_mmap", None)
        if mapping is not None:
            mapping.close()
        path = state.get("path")
        if path is not None:
            path.unlink(missing_ok=True)
        _depsgraph_update(context)


def _timed_export_stage(name, function):
    """Accumulate main-thread export timings without changing call contracts."""
    def timed(*args, **kwargs):
        started = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            if _export_timing_sink is not None:
                _export_timing_sink[name] = (
                    _export_timing_sink.get(name, 0.0)
                    + time.monotonic() - started)
    timed.__name__ = function.__name__
    timed.__doc__ = function.__doc__
    return timed


_extract_mesh = _timed_export_stage("mesh_extraction", _extract_mesh)
_extract_source_mesh = _timed_export_stage(
    "mesh_extraction", _extract_source_mesh)
_capture_force_animation = _timed_export_stage(
    "force_capture", _capture_force_animation)
_capture_animated_pin = _timed_export_stage(
    "pin_capture", _capture_animated_pin)
_capture_collider_motion = _timed_export_stage(
    "collider_capture", _capture_collider_motion)
encode_scene = _timed_export_stage("scene_encoding", encode_scene)
encode_deformable_scene = _timed_export_stage(
    "scene_encoding", encode_deformable_scene)
encode_multi_deformable_scene = _timed_export_stage(
    "scene_encoding", encode_multi_deformable_scene)
encode_multi_deformable_scene_file = _timed_export_stage(
    "scene_encoding", encode_multi_deformable_scene_file)
encode_multi_deformable_param = _timed_export_stage(
    "parameter_encoding", encode_multi_deformable_param)
encode_multi_collider_param = _timed_export_stage(
    "parameter_encoding", encode_multi_collider_param)
encode_deformable_param = _timed_export_stage(
    "parameter_encoding", encode_deformable_param)


def _payload_cache_for(obj) -> ExportPayloadCache:
    configured = str(getattr(
        obj.cloth_next, "cache_directory", "") or "").strip()
    root = (Path(bpy.path.abspath(configured))
            if configured else _cache_directory())
    return ExportPayloadCache(root / ".cloth_next_export")


def _cached_payload(cache, kind, key, producer):
    lookup = cache.lookup(kind, key)
    if _export_timing_sink is not None:
        _export_timing_sink[f"{kind}_cache_hit"] = 1.0 if lookup.hit else 0.0
        _export_timing_sink[f"{kind}_cache_miss"] = 0.0 if lookup.hit else 1.0
    if lookup.hit:
        if kind == "scene":
            shared_controller.update(
                status_message="Reusing verified export",
                activity_code=BakeActivity.ENCODING_SCENE)
        return lookup.path, lookup.digest
    payload, digest = producer()
    try:
        stored = cache.store(kind, key, payload)
    except OSError as exc:
        if _export_timing_sink is not None:
            _export_timing_sink[f"{kind}_cache_write_failed"] = 1.0
        if _export_cache_event_sink is not None:
            _export_cache_event_sink[f"{kind}_cache_write_error"] = (
                f"{type(exc).__name__}: {exc}")
        log_with_context(
            get_logger("export.cache"), 30,
            "Export payload cache write failed", {
                "kind": kind, "key": key,
                "cache_root": str(cache.root),
                "error": f"{type(exc).__name__}: {exc}",
            })
        return payload, digest
    if stored.digest != digest:
        raise SceneValidationError(
            f"{kind.title()} export cache hash verification failed.")
    return stored.path, stored.digest


def _cache_plan_record(plan: RunPlan) -> tuple[dict, dict[str, memoryview]]:
    targets = _plan_deformables(plan)
    artifacts = {}
    rows = []
    pin_configs = tuple(getattr(plan, "pin_configs", ()))
    for index, target in enumerate(targets):
        initial = np.ascontiguousarray(
            np.asarray(target.initial_local, dtype="<f4").reshape((-1, 3)))
        initial_name = f"initial_{index:03d}.f32"
        artifacts[initial_name] = memoryview(initial).cast("B")
        pin = (pin_configs[index]
               if index < len(pin_configs) else None)
        pin_record = None
        if pin is not None:
            positions = np.ascontiguousarray(
                np.asarray(pin.positions, dtype="<f4"))
            pin_name = f"pins_{index:03d}.f32"
            artifacts[pin_name] = memoryview(positions).cast("B")
            pin_record = {
                "artifact": pin_name, "shape": list(positions.shape),
                "indices": list(pin.indices), "operations": list(pin.operations),
                "unpin_time": pin.unpin_time,
                "transition": pin.transition,
                "pull_strength": pin.pull_strength,
                "pull_weights": (list(pin.pull_weights)
                                 if pin.pull_weights is not None else None),
                "pin_stiffness": pin.pin_stiffness,
                "pin_group_id": pin.pin_group_id,
                "rest_shape_track": pin.rest_shape_track,
                "times": list(pin.times),
            }
        rows.append({
            "name": target.object_name, "uuid": target.uuid,
            "role": target.role,
            "world_matrix": [list(row) for row in target.world_matrix],
            "topology_signature": target.topology_signature,
            "material_meta": target.material_meta,
            "stitch_pairs": [list(pair) for pair in target.stitch_pairs],
            "stitch_snap_distance": target.stitch_snap_distance,
            "initial_artifact": initial_name,
            "initial_shape": list(initial.shape),
            "pin": pin_record,
        })
    return {
        "version": 1,
        "frame_count": plan.frame_count,
        "frame_start": plan.frame_start,
        "frame_end": plan.frame_end,
        "fps": plan.fps,
        "geometry_fingerprint": plan.geometry_fingerprint,
        "targets": rows,
    }, artifacts


def _store_scene_plan_cache(context, snapshot, plan, source_key) -> None:
    if not source_key:
        return
    try:
        metadata, artifacts = _cache_plan_record(plan)
        cache = _payload_cache_for(snapshot.cloth_obj)
        stored = cache.store(
            "scene", source_key, plan.scene.data_payload,
            plan=metadata, artifacts=artifacts)
        if stored.digest != plan.scene.data_hash:
            raise ValueError("cached Scene hash differs from encoded payload")
        if _export_timing_sink is not None:
            _export_timing_sink["scene_cache_store"] = 1.0
        if plan.param_cache_key:
            cached_param = cache.store(
                "param", plan.param_cache_key, plan.scene.param_payload)
            if cached_param.digest != plan.scene.param_hash:
                raise ValueError(
                    "cached Param hash differs from encoded payload")
    except (OSError, ValueError, TypeError) as exc:
        if _export_timing_sink is not None:
            _export_timing_sink["scene_cache_write_failed"] = 1.0
        if _export_cache_event_sink is not None:
            _export_cache_event_sink["scene_cache_write_error"] = (
                f"{type(exc).__name__}: {exc}")
        log_with_context(
            get_logger("export.cache"), 30,
            "Verified Scene plan cache write failed", {
                "source_key": source_key,
                "cache_root": str(
                    _payload_cache_for(snapshot.cloth_obj).root),
                "error": f"{type(exc).__name__}: {exc}",
            })


def _load_pin_config(record, artifacts):
    if record is None:
        return None
    path = artifacts.get(record["artifact"])
    if path is None:
        raise ValueError("missing cached Pin artifact")
    shape = tuple(int(value) for value in record["shape"])
    count = math.prod(shape)
    values = np.fromfile(path, dtype="<f4", count=count)
    if values.size != count:
        raise ValueError("cached Pin artifact is truncated")
    positions = tuple(
        tuple(tuple(float(value) for value in point) for point in frame)
        for frame in values.reshape(shape))
    return StaticPinConfig(
        tuple(int(value) for value in record["indices"]),
        tuple(record.get("operations", ())),
        record.get("unpin_time"),
        str(record.get("transition", "linear")),
        float(record.get("pull_strength", 0.0)),
        float(record.get("pin_stiffness", 1.0)),
        str(record.get("pin_group_id", "")),
        (tuple(float(value) for value in record["pull_weights"])
         if record.get("pull_weights") is not None else None),
        bool(record.get("rest_shape_track", False)),
        tuple(float(value) for value in record.get("times", ())),
        positions)


def _current_material_meta(cached, snapshot, entry, resolved, *,
                           vertex_count, frame_count):
    meta = copy.deepcopy(cached)
    object_identity = {
        "object_key": validation_state.object_key(entry.obj),
        "deformable_type": entry.role,
        "topology_signature": entry.topology_signature,
        "geometry_fingerprint": snapshot.geometry_fingerprint,
    }
    fingerprints = meta.setdefault("fingerprints", {})
    fingerprints.update({
        "settings": snapshot.settings_fingerprint,
        "geometry": snapshot.geometry_fingerprint,
        "combined": snapshot.combined_fingerprint,
        "topology": entry.topology_signature,
        "object": cache_metadata.deterministic_hash(object_identity),
    })
    identities = meta.setdefault("identities", {})
    identities.update({
        "cloth_next_version": manifest_version(),
        "blender_version": _blender_version(),
        "object": object_identity,
    })
    identities["solver"] = {
        "mode": resolved.mode.name,
        "installation_id": _resolved_installation_id(resolved) or "unregistered",
        "official_release_tag": _resolved_release_tag(resolved),
        "package_version": resolved.package_version or "unknown",
        "protocol_version": resolved.protocol_version or "unknown",
        "schema_version": resolved.schema_version or "unknown",
        "source_metadata": getattr(resolved, "source_metadata", None) or {},
    }
    meta.setdefault("expected", {}).update({
        "vertex_count": vertex_count, "frame_count": frame_count})
    details = meta.setdefault("details", {})
    details.update({
        "preset": entry.preset_identifier,
        "contact_enabled": snapshot.contact_enabled,
        "deformable_type": entry.role,
        "material": asdict(entry.material),
        "blender_start_frame": snapshot.bake_range.start,
        "blender_end_frame": snapshot.bake_range.end,
    })
    return meta


def _recovery_param_kwargs(scene) -> dict:
    settings = getattr(scene, "cloth_next_recovery", None)
    enabled = bool(settings and getattr(settings, "enabled", False))
    return {
        "auto_save_interval": (
            int(settings.checkpoint_interval)
            if enabled and bool(settings.auto_save) else 0),
        "keep_saved_states": (
            int(settings.keep_saved_states) if enabled else 0),
        "save_state_on_finish": (
            enabled and bool(settings.save_on_finish)),
    }


def _motion_override_dynamic(snapshot, fps: float):
    """Encode per-object Blender-frame velocity replacements for PPF."""
    role_order = ("CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY")
    ordered = [entry for role in role_order
               for entry in snapshot.deformables if entry.role == role]
    tracks = []
    for dmap_index, entry in enumerate(ordered):
        grouped = {"LINEAR": [], "ANGULAR": []}
        seen = set()
        for row in getattr(entry.obj.cloth_next, "motion_overrides", ()):
            frame = int(row.frame)
            kind = str(row.motion_type)
            if frame < snapshot.bake_range.start or frame > snapshot.bake_range.end:
                raise SceneValidationError(
                    f"{entry.obj.name}: Motion Override frame {frame} is outside "
                    f"the Bake range {snapshot.bake_range.start}–{snapshot.bake_range.end}.")
            identity = (kind, frame)
            if identity in seen:
                raise SceneValidationError(
                    f"{entry.obj.name}: only one {kind.title()} Motion Override "
                    f"is allowed on frame {frame}.")
            seen.add(identity)
            vector = (tuple(float(value) for value in row.angular_velocity)
                      if kind == "ANGULAR" else
                      tuple(float(value) for value in row.velocity))
            time_seconds = ((frame - snapshot.bake_range.start) / float(fps))
            grouped[kind].append((time_seconds, vector, True))
        if grouped["LINEAR"]:
            tracks.append((f"velocity:{dmap_index}",
                           tuple(sorted(grouped["LINEAR"]))))
        if grouped["ANGULAR"]:
            tracks.append((f"angular_velocity_world:{dmap_index}",
                           tuple(sorted(grouped["ANGULAR"]))))
    return tuple(tracks)


def _all_dynamic_parameters(snapshot, force_capture, fps: float):
    return (tuple(force_capture.dynamic_parameters)
            + _motion_override_dynamic(snapshot, fps))


def _encode_cached_param(context, snapshot, force_capture, pin_configs,
                         cache, target_uuids, *, schema_version: int = 1):
    entries = snapshot.deformables
    settings = SimulationSettings(
        snapshot.bake_range.output_count, _scene_fps(context),
        force_capture.initial.gravity, snapshot.quality,
        wind_blender=force_capture.initial.wind,
        air_density=(force_capture.initial.air_density
                     if "AIR_DENSITY" in force_capture.active_scalar_types
                     else None),
        air_friction=(force_capture.initial.air_friction
                      if "AIR_FRICTION" in force_capture.active_scalar_types
                      else None),
        vertex_air_damp=(
            force_capture.initial.vertex_air_damp
            if "VERTEX_AIR_DAMP" in force_capture.active_scalar_types
            else None),
        dynamic_parameters=_all_dynamic_parameters(
            snapshot, force_capture, _scene_fps(context)),
        **_recovery_param_kwargs(context.scene))
    group_for_role = {
        "CLOTH": GROUP_SHELL, "ROD": GROUP_ROD,
        "SOFT_BODY": GROUP_SOLID, "RIGID_BODY": GROUP_PDRD}
    dynamics = [
        (entry.obj.name, uuid, group_for_role[entry.role],
         entry.material, pin)
        for entry, uuid, pin in zip(entries, target_uuids, pin_configs)]
    colliders = [
        (obj.name, export_identity.export_uuid(obj), material)
        for obj, material in zip(snapshot.collider_objs, snapshot.statics)]
    _sentinel_objects, colliders = _ensure_solver_static([], colliders)
    key = _param_source_key(
        context, snapshot, force_capture, target_uuids,
        tuple(item[1] for item in colliders), pin_configs)
    return _cached_payload(
        cache, "param", key,
        lambda: encode_multi_deformable_param(
            settings, dynamics, colliders,
            contact_enabled=snapshot.contact_enabled,
            schema_version=schema_version))


def _pin_configs_cache_identity(pin_configs):
    """Compact content identity; Pin motion must never reuse stale params."""
    identities = []
    for config in pin_configs:
        if config is None:
            identities.append(None)
            continue
        digest = hashlib.sha256()
        digest.update(b"cloth-next-pin-track-v3\0")
        digest.update(str(config.pin_group_id).encode("utf-8"))
        digest.update(np.asarray(config.indices, dtype="<i8").tobytes())
        digest.update(np.asarray(config.times, dtype="<f8").tobytes())
        digest.update(np.asarray(config.positions, dtype="<f4").tobytes())
        digest.update(np.asarray(
            config.pull_weights or (), dtype="<f4").tobytes())
        digest.update(np.asarray(
            (config.pull_strength,), dtype="<f4").tobytes())
        identities.append({
            "indices": len(config.indices),
            "samples": len(config.times),
            "sha256": digest.hexdigest(),
        })
    return identities


def _param_source_key(context, snapshot, force_capture, target_uuids,
                      collider_uuids, pin_configs):
    recovery_settings = getattr(context.scene, "cloth_next_recovery", None)
    return deterministic_key("param", {
        "solver_installation": _solver_selection_key(context),
        "settings": snapshot.settings_fingerprint,
        "force_initial": repr(force_capture.initial),
        "force_dynamic": repr(force_capture.dynamic_parameters),
        "frame_range": [
            snapshot.bake_range.start, snapshot.bake_range.end],
        "fps": _scene_fps(context),
        "object_uuids": [
            *target_uuids, *collider_uuids],
        "pin_tracks": _pin_configs_cache_identity(pin_configs),
        "recovery": {
            "enabled": bool(
                recovery_settings
                and getattr(recovery_settings, "enabled", False)),
            "auto_save": bool(
                recovery_settings
                and getattr(recovery_settings, "auto_save", False)),
            "checkpoint_interval": int(getattr(
                recovery_settings, "checkpoint_interval", 0)),
            "keep_saved_states": int(getattr(
                recovery_settings, "keep_saved_states", 0)),
            "save_on_finish": bool(getattr(
                recovery_settings, "save_on_finish", False)),
        },
    })


def _solver_selection_key(context) -> str:
    try:
        preferences = addon_preferences(context, __package__)
        return str(getattr(
            preferences, "selected_solver_installation_id", "") or "")
    except (KeyError, AttributeError):
        return ""


def _load_early_scene_plan(context, snapshot, resolved, source_key,
                           force_capture):
    cache = _payload_cache_for(snapshot.cloth_obj)
    lookup = cache.lookup("scene", source_key)
    if not lookup.hit or not lookup.metadata:
        if _export_timing_sink is not None:
            _export_timing_sink["scene_early_cache_hit"] = 0.0
        if _export_cache_event_sink is not None:
            _export_cache_event_sink["scene_cache_miss_reason"] = lookup.reason
        if _export_timing_sink is not None:
            _export_timing_sink["mesh_export_cache_misses"] = float(
                len(snapshot.deformables) + len(snapshot.collider_objs))
            _export_timing_sink["mesh_export_cache_invalidations"] = float(
                lookup.reason not in {"missing", ""})
        return None
    artifacts = cache.lookup_artifacts("scene", source_key)
    if not artifacts:
        if _export_timing_sink is not None:
            _export_timing_sink["scene_early_cache_hit"] = 0.0
        if _export_cache_event_sink is not None:
            _export_cache_event_sink["scene_cache_miss_reason"] = (
                "missing or corrupt plan artifacts")
        if _export_timing_sink is not None:
            _export_timing_sink["mesh_export_cache_misses"] = float(
                len(snapshot.deformables) + len(snapshot.collider_objs))
            _export_timing_sink["mesh_export_cache_invalidations"] = 1.0
        return None
    try:
        record = lookup.metadata
        if (record.get("version") != 1
                or record.get("geometry_fingerprint")
                != snapshot.geometry_fingerprint
                or len(record.get("targets", ()))
                != len(snapshot.deformables)):
            return None
        target_plans = []
        pin_configs = []
        session_targets = []
        project_name = new_project_name()
        work_directory = (
            Path(bpy.app.tempdir) / f"cloth_next_run_{project_name}")
        for index, (row, entry) in enumerate(
                zip(record["targets"], snapshot.deformables)):
            expected_uuid = export_identity.export_uuid(entry.obj)
            if (row["uuid"] != expected_uuid
                    or row["role"] != entry.role):
                return None
            shape = tuple(int(value) for value in row["initial_shape"])
            path = artifacts.get(row["initial_artifact"])
            if path is None or len(shape) != 2 or shape[1] != 3:
                return None
            values = np.fromfile(path, dtype="<f4")
            if values.size != math.prod(shape):
                return None
            initial = values.reshape(shape)
            world = tuple(tuple(float(value) for value in matrix_row)
                          for matrix_row in row["world_matrix"])
            pin = _load_pin_config(row.get("pin"), artifacts)
            pin_configs.append(pin)
            configured = str(getattr(
                entry.obj.cloth_next, "cache_directory", "") or "").strip()
            cache_dir = (Path(bpy.path.abspath(configured))
                         if configured else _cache_directory())
            pc2_path = cache_dir / (
                f"cn_test_cloth_{project_name[10:]}_{index:02d}.pc2")
            meta = _current_material_meta(
                row["material_meta"], snapshot, entry, resolved,
                vertex_count=shape[0],
                frame_count=snapshot.bake_range.output_count)
            meta["details"]["fps"] = _scene_fps(context)
            target_plans.append(DeformablePlan(
                initial, world, entry.obj.name, expected_uuid, pc2_path,
                entry.topology_signature, meta, entry.role,
                tuple(tuple(int(v) for v in pair)
                      for pair in row.get("stitch_pairs", ())),
                float(row.get("stitch_snap_distance", 0.0))))
            session_targets.append(SessionDeformable(
                entry.obj.name, expected_uuid, shape[0],
                {"CLOTH": GROUP_SHELL, "ROD": GROUP_ROD,
                 "SOFT_BODY": GROUP_SOLID,
                 "RIGID_BODY": GROUP_PDRD}[entry.role],
                solver_world_matrix(world)))
        param_payload, param_hash = _encode_cached_param(
            context, snapshot, force_capture, tuple(pin_configs), cache,
            tuple(target.uuid for target in target_plans),
            schema_version=int(resolved.schema_version or "1"))
        param_key = _param_source_key(
            context, snapshot, force_capture,
            tuple(target.uuid for target in target_plans),
            tuple(export_identity.export_uuid(obj)
                  for obj in snapshot.collider_objs)
            or (INTERNAL_STATIC_UUID,), tuple(pin_configs))
        first = target_plans[0]
        collider_specs = [
            (obj.name, export_identity.export_uuid(obj))
            for obj in snapshot.collider_objs]
        session_scene = SessionScene(
            project_name, first.object_name, first.uuid,
            len(first.initial_local),
            collider_specs[0][0] if collider_specs else "",
            collider_specs[0][1] if collider_specs else "",
            snapshot.bake_range.output_count,
            lookup.path, param_payload, lookup.digest, param_hash,
            deformables=tuple(session_targets))
        if _export_timing_sink is not None:
            _export_timing_sink["scene_early_cache_hit"] = 1.0
            # These counters intentionally count exported object artifacts,
            # not merely one opaque scene-envelope lookup.  The persistent
            # plan keeps each deformable's initial geometry and pin data as
            # independently hash-verified artifacts.
            _export_timing_sink["mesh_export_cache_hits"] = float(
                len(target_plans) + len(snapshot.collider_objs))
            _export_timing_sink["mesh_export_cache_misses"] = 0.0
            _export_timing_sink["mesh_export_cache_invalidations"] = 0.0
            _export_timing_sink["mesh_export_cache_bytes_reused"] = float(
                lookup.size + sum(path.stat().st_size
                                  for path in artifacts.values()))
            # Wall-clock savings are measured by the deterministic developer
            # benchmark; do not manufacture a per-run estimate here.
            _export_timing_sink["mesh_export_cache_time_saved"] = 0.0
        if _export_cache_event_sink is not None:
            _export_cache_event_sink["scene_cache_miss_reason"] = ""
        shared_controller.update(
            status_message="Reusing verified export",
            activity_code=BakeActivity.ENCODING_SCENE)
        return RunPlan(
            session_scene, resolved, first.initial_local,
            first.world_matrix, first.object_name, work_directory,
            first.pc2_path, snapshot.bake_range.output_count,
            snapshot.bake_range.start, snapshot.bake_range.end,
            _scene_fps(context), snapshot.settings_fingerprint,
            snapshot.geometry_fingerprint, first.topology_signature,
            snapshot.preset_identifier, first.material_meta, first.role,
            tuple(target_plans), first.stitch_pairs,
            first.stitch_snap_distance, scene_cache_key=source_key,
            pin_configs=tuple(pin_configs), param_cache_key=param_key)
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        if _export_timing_sink is not None:
            _export_timing_sink["scene_early_cache_hit"] = 0.0
        if _export_cache_event_sink is not None:
            _export_cache_event_sink["scene_cache_miss_reason"] = (
                "corrupt cached plan")
        return None


def _verified_early_scene_available(snapshot, source_key) -> bool:
    if not source_key:
        return False
    cache = _payload_cache_for(snapshot.cloth_obj)
    lookup = cache.lookup("scene", source_key)
    return bool(
        lookup.hit and lookup.metadata
        and cache.lookup_artifacts("scene", source_key))


def _validate_deformable_modifier_path(obj, pin_membership) -> None:
    """Artist modifiers are accepted; Armature precedes Cloth NeXt playback.

    Export evaluates through the last enabled Armature at Bake Start and ignores
    later modifiers. Without an Armature it reads ``obj.data`` directly.
    Playback is attached after the last Armature modifier. Topology changes in
    the evaluated rig prefix are rejected by the extraction vertex-count check.
    ``pin_membership`` remains accepted for call-site stability.
    """
    del obj, pin_membership


def _build_multi_run_plan(context, snapshot: ValidationSnapshot,
                          animated_pin_samples=None,
                          force_capture: ForceCapture | None = None,
                          collider_captures=None) -> RunPlan:
    scene = context.scene
    resolved = resolve_solver(context)
    wire_schema = int(resolved.schema_version or "1")
    if (any(_friction_region_settings(entry.obj)
            for entry in snapshot.deformables)
            and resolved.mode is not SolverMode.MANAGED_INSTALLATION):
        raise SceneValidationError(
            "Friction Vertex Groups require the managed Cloth NeXt solver. "
            "Disable the external/development solver or remove the regions.")
    bake_range = snapshot.bake_range
    force_capture = (force_capture or
                     _capture_force_animation(context, bake_range))
    original_frame = int(scene.frame_current)
    original_subframe = float(getattr(scene, "frame_subframe", 0.0))
    dynamic_records = []
    collider_records = []
    try:
        scene.frame_set(bake_range.start)
        for entry in snapshot.deformables:
            obj = entry.obj
            precomputed = (animated_pin_samples.get(obj.name)
                           if isinstance(animated_pin_samples, dict) else None)
            pin_snapshot = _capture_animated_pin(
                context, obj, bake_range, entry.pin_membership, precomputed)
            _validate_deformable_modifier_path(obj, pin_snapshot)
            with without_owned_playback(obj,
                                        lambda: _depsgraph_update(context)):
                if entry.role == "ROD":
                    vertices, edges, _splines = sample_curve(obj)
                    triangles = ()
                    uv_faces = ()
                    face_friction = ()
                else:
                    if entry.role in {"SOFT_BODY", "RIGID_BODY"}:
                        open_edges = _non_manifold_edge_count(obj.data)
                        if open_edges:
                            raise SceneValidationError(
                                f"{obj.name} is not a closed manifold surface "
                                f"({open_edges} boundary/non-manifold edges). "
                                "Seal the mesh and make its normals face outward.")
                    vertices, triangles = _extract_deformable_mesh(
                        context, obj, needs_edges=True)
                    edges = ()
                    uv_faces = (_extract_source_uv_faces(obj)
                                if entry.role == "CLOTH" else ())
                    face_friction = (_extract_face_friction(
                        obj, triangles, entry.material.surface_grip)
                        if entry.role == "CLOTH" else ())
            world = tuple(tuple(row) for row in obj.matrix_world)
            if not matrix_is_finite_and_invertible(world):
                raise SceneValidationError(
                    f"{obj.name} has a non-finite or non-invertible world matrix.")
            degenerate = zero_area_triangles(vertices, triangles)
            if degenerate:
                raise SceneValidationError(
                    f"{obj.name} has {len(degenerate)} zero-area triangle(s) "
                    f"(first index {degenerate[0]}).")
            if entry.role == "CLOTH":
                stitch_pairs = ()
                if entry.material.sewing_enabled:
                    stitch_pairs, hanging = _detect_sewing_edges(obj.data)
                    if hanging:
                        selected = _select_problem_vertices(context, obj, hanging)
                        selection_note = ("The invalid vertices are selected in Edit Mode."
                                          if selected else
                                          "Select and repair the loose Sewing vertices.")
                        raise SceneValidationError(
                            f"{obj.name} has {len(hanging)} Sewing vertex/vertices "
                            "that are not part of any face and therefore carry no "
                            f"surface mass. {selection_note}")
                pair_count, problem_vertices = _self_intersection_vertices(
                    vertices, triangles)
                if pair_count:
                    selected = _select_problem_vertices(
                        context, obj, problem_vertices)
                    selection_note = ("The involved vertices are selected in Edit Mode."
                                      if selected else
                                      "Select and repair the intersecting region in Edit Mode.")
                    raise SceneValidationError(
                        f"{obj.name} has {pair_count} self-intersecting triangle "
                        f"pair(s) involving {len(problem_vertices)} vertices. "
                        f"{selection_note}")
            else:
                stitch_pairs = ()
            if (pin_snapshot.enabled and
                    len(vertices) != pin_snapshot.source_vertex_count):
                raise SceneValidationError(
                    f"{obj.name}: pinning source/evaluated vertex counts differ.")
            dynamic_records.append(
                (entry, pin_snapshot, vertices, triangles, edges, world,
                 stitch_pairs, uv_faces, face_friction))
        animated = tuple(
            obj for obj in snapshot.collider_objs
            if str(getattr(obj.cloth_next, "collider_motion", "STATIC"))
            == "ANIMATED")
        animated_captures = (collider_captures or
            _capture_animated_colliders_shared(
                context, animated, bake_range))
        static_colliders = tuple(
            obj for obj in snapshot.collider_objs if obj not in animated)
        static_depsgraph = None
        if static_colliders:
            scene.frame_set(bake_range.start)
            static_depsgraph = context.evaluated_depsgraph_get()
        for obj in snapshot.collider_objs:
            if str(getattr(obj.cloth_next, "collider_motion", "STATIC")) == "ANIMATED":
                capture = animated_captures[obj.name]
                collider_records.append((obj, capture.vertices,
                    capture.triangles, None, capture))
            else:
                vertices, triangles = _extract_mesh(
                    obj, static_depsgraph, needs_edges=False)
                world = tuple(tuple(row) for row in obj.matrix_world)
                if not matrix_is_finite_and_invertible(world):
                    raise SceneValidationError(
                        f"{obj.name} has a non-finite or non-invertible world matrix.")
                collider_records.append((obj, vertices, triangles, world, None))
    except Exception:
        for _obj, _vertices, _triangles, _world, capture in collider_records:
            if capture is not None:
                capture.cleanup()
        raise
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
        _depsgraph_update(context)

    project_name = new_project_name()
    work_directory = Path(bpy.app.tempdir) / f"cloth_next_run_{project_name}"
    scene_dynamics = []
    param_dynamics = []
    session_dynamics = []
    uuids = []
    group_for_role = {"CLOTH": GROUP_SHELL, "ROD": GROUP_ROD,
                      "SOFT_BODY": GROUP_SOLID, "RIGID_BODY": GROUP_PDRD}
    for (entry, pin_snapshot, vertices, triangles, edges, world,
         stitch_pairs, uv_faces, face_friction) in dynamic_records:
        dynamic_uuid = export_identity.export_uuid(entry.obj)
        uuids.append(dynamic_uuid)
        group = group_for_role[entry.role]
        scene_dynamics.append((SceneObject(
            entry.obj.name, dynamic_uuid, vertices, triangles,
            solver_world_matrix(world), pin_snapshot.vertex_indices,
            edges=edges, stitch_pairs=stitch_pairs,
            uv_faces=uv_faces, face_friction=face_friction), group))
        param_dynamics.append((entry.obj.name, dynamic_uuid, group,
                               entry.material,
                               static_pin_config(pin_snapshot, schema_version=wire_schema)))
        session_dynamics.append(SessionDeformable(
            entry.obj.name, dynamic_uuid, len(vertices), group,
            solver_world_matrix(world)))
    scene_colliders = []
    collider_specs = []
    motion_meta = []
    try:
        for (obj, vertices, triangles, world, capture), material in zip(
                collider_records, snapshot.statics):
            collider_uuid = export_identity.export_uuid(obj)
            collider_specs.append((obj.name, collider_uuid, material))
            if capture is None:
                exported = SceneObject(obj.name, collider_uuid, vertices,
                    triangles, solver_world_matrix(world))
                motion_type = "STATIC"
            elif capture.motion_type == "RIGID_ANIMATED":
                exported = SceneObject(obj.name, collider_uuid, vertices,
                    triangles, capture.transform,
                    transform_animation=capture.animation)
                motion_type = capture.motion_type
            else:
                exported = SceneObject(obj.name, collider_uuid, vertices,
                    triangles, capture.transform,
                    static_deform_animation=capture.animation)
                motion_type = capture.motion_type
            scene_colliders.append(exported)
            motion_meta.append({"name": obj.name, "motion_type": motion_type,
                                "animation_digest": (
                                    capture.content_digest
                                    if capture is not None else ""),
                                "samples_per_frame": (int(getattr(
                                    obj.cloth_next,
                                    "collider_samples_per_frame",
                                    COLLIDER_SAMPLES_PER_FRAME))
                                    if capture is not None else 0),
                                "vertex_count": len(vertices),
                                "triangle_count": len(triangles)})
        scene_colliders, collider_specs = _ensure_solver_static(
            scene_colliders, collider_specs)
        role_for_group = {
            GROUP_SHELL: "CLOTH", GROUP_ROD: "ROD",
            GROUP_SOLID: "SOFT_BODY", GROUP_PDRD: "RIGID_BODY"}
        generated_by_uuid = {
            export_identity.export_uuid(obj):
                collider_proxy.is_generated_proxy(obj)
            for obj, _vertices, _triangles, _world, _capture
            in collider_records}
        source_faces_by_uuid = {
            export_identity.export_uuid(entry.obj):
                _source_polygon_indices(entry.obj, len(triangles))
            for (entry, _pin_snapshot, _vertices, triangles, _edges, _world,
                 _stitch_pairs, _uv_faces, _face_friction)
            in dynamic_records}
        source_faces_by_uuid.update({
            export_identity.export_uuid(obj):
                _source_polygon_indices(obj, len(triangles))
            for obj, _vertices, triangles, _world, _capture
            in collider_records})
        ordered_input = []
        for group in (GROUP_SHELL, GROUP_ROD, GROUP_SOLID, GROUP_PDRD):
            ordered_input.extend(
                (item, role_for_group[group],
                 source_faces_by_uuid.get(item.uuid), False)
                for item, item_group in scene_dynamics
                if item_group == group)
        ordered_input.extend(
            (item, "COLLIDER", source_faces_by_uuid.get(item.uuid),
             generated_by_uuid.get(item.uuid, False))
            for item in scene_colliders)
        solver_input = intersection_diagnostics.build_solver_input_snapshot(
            ordered_input, bake_start_frame=bake_range.start)
        deforming_capture = any(
            capture is not None and
            capture.motion_type == "DEFORMING_ANIMATED"
            for _obj, _vertices, _triangles, _world, capture
            in collider_records)
        if deforming_capture:
            encoding_total = max(
                int(capture.animation["vert_frames"].shape[0])
                for _obj, _vertices, _triangles, _world, capture
                in collider_records
                if capture is not None and
                capture.motion_type == "DEFORMING_ANIMATED")
            shared_controller.update(
                status_message="Encoding animated Collider data",
                activity_code=BakeActivity.ENCODING_SCENE,
                progress_current=0, progress_total=encoding_total)
            def encoding_progress(current, total):
                if _cancel_event.is_set():
                    raise SessionCancelled()
                shared_controller.update(
                    status_message=(f"Encoding animated Colliders · "
                                    f"{current} / {total}"),
                    activity_code=BakeActivity.ENCODING_SCENE,
                    progress_current=current, progress_total=total)
            data_payload, data_hash = encode_multi_deformable_scene_file(
                scene_dynamics, scene_colliders,
                work_directory / "scene.cbor",
                progress=encoding_progress, schema_version=wire_schema)
        else:
            data_payload, data_hash = encode_multi_deformable_scene(
                scene_dynamics, scene_colliders,
                schema_version=wire_schema)
    finally:
        for _obj, _vertices, _triangles, _world, capture in collider_records:
            if capture is not None:
                capture.cleanup()
    frame_count = bake_range.output_count
    settings = SimulationSettings(
        frame_count, _scene_fps(context),
        force_capture.initial.gravity, snapshot.quality,
        wind_blender=force_capture.initial.wind,
        air_density=(force_capture.initial.air_density
                     if "AIR_DENSITY" in force_capture.active_scalar_types else None),
        air_friction=(force_capture.initial.air_friction
                      if "AIR_FRICTION" in force_capture.active_scalar_types else None),
        vertex_air_damp=(force_capture.initial.vertex_air_damp
                         if "VERTEX_AIR_DAMP" in force_capture.active_scalar_types else None),
        dynamic_parameters=_all_dynamic_parameters(
            snapshot, force_capture, _scene_fps(context)),
        **_recovery_param_kwargs(scene))
    param_payload, param_hash = encode_multi_deformable_param(
        settings, param_dynamics, collider_specs,
        contact_enabled=snapshot.contact_enabled,
        schema_version=wire_schema)
    param_cache_key = _param_source_key(
        context, snapshot, force_capture, tuple(uuids),
        tuple(item[1] for item in collider_specs),
        tuple(item[4] for item in param_dynamics))
    session_scene = SessionScene(
        project_name, session_dynamics[0].name, session_dynamics[0].uuid,
        session_dynamics[0].vertex_count,
        collider_specs[0][0] if collider_specs else "",
        collider_specs[0][1] if collider_specs else "",
        frame_count, data_payload, param_payload,
        data_hash, param_hash, deformables=tuple(session_dynamics))
    scene_identity = {
        "settings_fingerprint": snapshot.settings_fingerprint,
        "geometry_fingerprint": snapshot.geometry_fingerprint,
        "fps": _scene_fps(context),
        "frame_start": bake_range.start,
        "frame_end": bake_range.end,
        "deformables": sorted([{
            "object_key": validation_state.object_key(entry.obj),
            "uuid": export_identity.export_uuid(entry.obj),
            "deformable_type": entry.role,
            "topology_signature": entry.topology_signature,
        } for entry in snapshot.deformables],
            key=lambda row: row["uuid"]),
        "colliders": motion_meta,
    }
    scene_fingerprint = cache_metadata.deterministic_hash(scene_identity)
    target_plans = []
    for index, ((entry, pin_snapshot, vertices, _triangles, _edges, world,
                 stitch_pairs, _uv_faces, _face_friction), dynamic_uuid) in enumerate(
            zip(dynamic_records, uuids)):
        configured = str(getattr(entry.obj.cloth_next,
                                 "cache_directory", "") or "").strip()
        cache_dir = (Path(bpy.path.abspath(configured))
                     if configured else _cache_directory())
        cache_path = cache_dir / (
            f"cn_test_cloth_{project_name[10:]}_{index:02d}.pc2")
        object_identity = {
            "object_key": validation_state.object_key(entry.obj),
            "deformable_type": entry.role,
            "topology_signature": entry.topology_signature,
            "geometry_fingerprint": snapshot.geometry_fingerprint}
        fingerprints = {
            "settings": snapshot.settings_fingerprint,
            "geometry": snapshot.geometry_fingerprint,
            "combined": snapshot.combined_fingerprint,
            "topology": entry.topology_signature,
            "object": cache_metadata.deterministic_hash(object_identity),
            "scene": scene_fingerprint}
        meta = {
            "fingerprints": fingerprints,
            "identities": {"cloth_next_version": manifest_version(),
                "blender_version": _blender_version(), "object": object_identity,
                "solver": {"mode": resolved.mode.name,
                    "installation_id": (
                        _resolved_installation_id(resolved) or "unregistered"),
                    "official_release_tag": _resolved_release_tag(resolved),
                    "package_version": resolved.package_version or "unknown",
                    "protocol_version": resolved.protocol_version or "unknown",
                    "schema_version": resolved.schema_version or "unknown"}},
            "expected": {"vertex_count": len(vertices),
                "frame_count": frame_count,
                "start_frame": import_result.PC2_START_FRAME,
                "sample_rate": import_result.PC2_SAMPLE_RATE},
            "details": {"preset": entry.preset_identifier,
                "contact_enabled": snapshot.contact_enabled,
                "deformable_type": entry.role, "material": asdict(entry.material),
                "colliders": motion_meta,
                "blender_start_frame": bake_range.start,
                "blender_end_frame": bake_range.end,
                "pinning": {"enabled": pin_snapshot.enabled,
                    "mode": pin_snapshot.mode.value,
                    "group": pin_snapshot.group_name,
                    "count": len(pin_snapshot.vertex_indices)}}}
        target_plans.append(DeformablePlan(
            vertices, world, entry.obj.name, dynamic_uuid, cache_path,
            entry.topology_signature, meta, entry.role, stitch_pairs,
            (max(1e-6, 2.0 * entry.material.collision_gap
                 + entry.material.surface_offset)
             if entry.role == "CLOTH" and stitch_pairs else 0.0)))
    first = target_plans[0]
    return RunPlan(session_scene, resolved, first.initial_local,
        first.world_matrix, first.object_name, work_directory, first.pc2_path,
        frame_count, bake_range.start, bake_range.end, _scene_fps(context),
        snapshot.settings_fingerprint, snapshot.geometry_fingerprint,
        first.topology_signature, snapshot.preset_identifier,
        first.material_meta, first.role, tuple(target_plans),
        first.stitch_pairs, first.stitch_snap_distance,
        pin_configs=tuple(
            static_pin_config(record[1], schema_version=wire_schema)
            for record in dynamic_records),
        param_cache_key=param_cache_key,
        solver_input=solver_input)


def _build_run_plan_impl(context, *, animated_pin_samples=None,
                         force_capture: ForceCapture | None = None,
                         collider_captures=None,
                         snapshot: ValidationSnapshot | None = None) -> RunPlan:
    """Freeze the run inputs from one authoritative validation.

    ``snapshot`` is the :class:`ValidationSnapshot` the Bake start already
    produced. Passing it in is what guarantees a Bake performs exactly one
    topology hash and exactly one pin scan; when it is omitted (developer
    test run, direct call) this validates once, here.
    """
    scene = context.scene
    if snapshot is None:
        snapshot = validate_scene(context)
    source_key, source_reason = _scene_source_key(context, snapshot)
    if _export_timing_sink is not None:
        _export_timing_sink["scene_source_key_safe"] = (
            1.0 if source_key else 0.0)
    if _export_cache_event_sink is not None:
        _export_cache_event_sink["scene_source_key_reason"] = source_reason
    # Resolve and capture only Param-side Forces before the Scene lookup.
    # A verified hit therefore occurs before evaluated meshes, Pins,
    # Colliders, and Scene CBOR encoding.
    if source_key:
        resolved_for_cache = resolve_solver(context)
        force_capture = (force_capture or
                         _capture_force_animation(
                             context, snapshot.bake_range))
        cached = _load_early_scene_plan(
            context, snapshot, resolved_for_cache, source_key,
            force_capture)
        if cached is not None:
            return cached
    if len(snapshot.deformables) > 1:
        plan = _build_multi_run_plan(
            context, snapshot, animated_pin_samples=animated_pin_samples,
            force_capture=force_capture,
            collider_captures=collider_captures)
        return replace(plan, scene_cache_key=source_key or "")
    cloth_obj = snapshot.cloth_obj
    deformable_role = str(cloth_obj.cloth_next.role)
    collider_objs = snapshot.collider_objs
    bake_range = snapshot.bake_range
    # Material validation is deliberately first after role/scope validation:
    # even the solver version probe is a subprocess, so invalid mapped values
    # must fail before resolution can launch it. validate_scene() already did
    # exactly that, along with the topology hash and the pin scan.
    shell = snapshot.shell
    statics = snapshot.statics
    static = statics[0] if statics else None
    contact_enabled = snapshot.contact_enabled
    preset_identifier = snapshot.preset_identifier
    pin_membership = snapshot.pin_membership
    _validate_deformable_modifier_path(cloth_obj, pin_membership)
    # Compatibility probing happens before animation capture so a missing
    # solver cannot leave behind a large temporary Collider buffer.
    resolved = resolve_solver(context)
    wire_schema = int(resolved.schema_version or "1")
    if (_friction_region_settings(cloth_obj)
            and resolved.mode is not SolverMode.MANAGED_INSTALLATION):
        raise SceneValidationError(
            "Friction Vertex Groups require the managed Cloth NeXt solver. "
            "Disable the external/development solver or remove the regions.")
    force_capture = (force_capture or
                     _capture_force_animation(context, bake_range))
    original_frame = int(scene.frame_current)
    original_subframe = float(getattr(scene, "frame_subframe", 0.0))
    collider_records = []
    try:
        with without_owned_playback(cloth_obj,lambda:_depsgraph_update(context)):
            scene.frame_set(bake_range.start)
            if deformable_role == "ROD":
                cloth_vertices, cloth_edges, _curve_splines = sample_curve(cloth_obj)
                cloth_triangles = ()
                cloth_uv_faces = ()
                cloth_face_friction = ()
            else:
                if deformable_role in {"SOFT_BODY", "RIGID_BODY"}:
                    mesh = cloth_obj.data
                    open_edges = _non_manifold_edge_count(mesh)
                    if open_edges:
                        raise SceneValidationError(
                            f"{cloth_obj.name} is not a closed manifold surface "
                            f"({open_edges} boundary/non-manifold edges). Seal the "
                            "mesh and make its normals face outward before Bake.")
                cloth_vertices, cloth_triangles = _extract_deformable_mesh(
                    context, cloth_obj, needs_edges=True)
                cloth_edges = ()
                cloth_uv_faces = (_extract_source_uv_faces(cloth_obj)
                                  if deformable_role == "CLOTH" else ())
                cloth_face_friction = (_extract_face_friction(
                    cloth_obj, cloth_triangles, shell.surface_grip)
                    if deformable_role == "CLOTH" else ())
            stitch_pairs = ()
            if deformable_role == "CLOTH" and shell.sewing_enabled:
                stitch_pairs, hanging = _detect_sewing_edges(cloth_obj.data)
                if hanging:
                    selected = _select_problem_vertices(
                        context, cloth_obj, hanging)
                    selection_note = ("The invalid vertices are selected in Edit Mode."
                                      if selected else
                                      "Select and repair the loose Sewing vertices.")
                    raise SceneValidationError(
                        f"{cloth_obj.name} has {len(hanging)} Sewing "
                        "vertex/vertices that are not part of any face and "
                        f"therefore carry no surface mass. {selection_note}")
            pin_snapshot=_capture_animated_pin(context,cloth_obj,bake_range,
                                               pin_membership,animated_pin_samples)
        animated = tuple(
            current for current in collider_objs
            if str(getattr(current.cloth_next, "collider_motion", "STATIC"))
            == "ANIMATED")
        animated_captures = (collider_captures or
            _capture_animated_colliders_shared(
                context, animated, bake_range))
        static_colliders = tuple(
            current for current in collider_objs if current not in animated)
        static_depsgraph = None
        if static_colliders:
            scene.frame_set(bake_range.start)
            static_depsgraph = context.evaluated_depsgraph_get()
        for current in collider_objs:
            if str(getattr(current.cloth_next, "collider_motion",
                           "STATIC")) == "ANIMATED":
                capture = animated_captures[current.name]
                collider_records.append((current, capture.vertices,
                                         capture.triangles, None, capture))
            else:
                vertices, triangles = _extract_mesh(
                    current, static_depsgraph, needs_edges=False)
                world = tuple(tuple(row) for row in current.matrix_world)
                collider_records.append(
                    (current, vertices, triangles, world, None))
        cloth_world = tuple(tuple(row) for row in cloth_obj.matrix_world)
    except Exception:
        for _obj, _vertices, _triangles, _world, capture in collider_records:
            if capture is not None:
                capture.cleanup()
        raise
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
    try:
        degenerate = zero_area_triangles(cloth_vertices, cloth_triangles)
        if degenerate:
            raise SceneValidationError(
                f"{cloth_obj.name} has {len(degenerate)} zero-area triangle(s) "
                f"(first index {degenerate[0]}); clean the mesh before running.")
        matrix_records = [(cloth_obj, cloth_world)] + [
            (obj, world if world is not None else capture.transform)
            for obj, _vertices, _triangles, world, capture in collider_records]
        for obj, world in matrix_records:
            if not matrix_is_finite_and_invertible(world):
                raise SceneValidationError(
                    f"{obj.name} has a non-finite or non-invertible world matrix.")
        if (pin_snapshot.enabled
                and len(cloth_vertices) != pin_snapshot.source_vertex_count):
            raise SceneValidationError(
                f"Pinning found {pin_snapshot.source_vertex_count} source vertices "
                f"and {len(cloth_vertices)} evaluated vertices.")
        if pin_snapshot.samples:
            matrix=solver_world_matrix(cloth_world)
            initial=tuple(_solver_position(matrix,cloth_vertices[index])
                          for index in pin_snapshot.vertex_indices)
            if any(any(abs(a-b)>1e-6 for a,b in zip(expected,captured))
                   for expected,captured in zip(initial,pin_snapshot.samples[0].positions)):
                raise SceneValidationError(
                    "Animated Pin targets at Bake Start do not match the exported Cloth positions.")
    except Exception:
        for _obj, _vertices, _triangles, _world, capture in collider_records:
            if capture is not None:
                capture.cleanup()
        raise
    pin_config = static_pin_config(
        pin_snapshot, schema_version=wire_schema)

    cloth_uuid = export_identity.export_uuid(cloth_obj)
    scene_cloth = SceneObject(cloth_obj.name, cloth_uuid, cloth_vertices,
                              cloth_triangles, solver_world_matrix(cloth_world),
                              pin_snapshot.vertex_indices, edges=cloth_edges,
                              stitch_pairs=stitch_pairs,
                              uv_faces=cloth_uv_faces,
                              face_friction=cloth_face_friction)
    scene_colliders = []
    collider_specs = []
    motion_meta = []
    for (current, vertices, triangles, world, capture), material in zip(
            collider_records, statics):
        collider_uuid = export_identity.export_uuid(current)
        collider_specs.append((current.name, collider_uuid, material))
        if capture is None:
            exported = SceneObject(current.name, collider_uuid, vertices,
                                   triangles, solver_world_matrix(world))
            motion_type = "STATIC"
        elif capture.motion_type == "RIGID_ANIMATED":
            exported = SceneObject(
                current.name, collider_uuid, vertices, triangles,
                capture.transform, transform_animation=capture.animation)
            motion_type = capture.motion_type
        else:
            exported = SceneObject(
                current.name, collider_uuid, vertices, triangles,
                capture.transform, static_deform_animation=capture.animation)
            motion_type = capture.motion_type
        scene_colliders.append(exported)
        motion_meta.append({"name": current.name, "uuid": collider_uuid,
                            "motion_type": motion_type,
                            "animation_digest": (
                                capture.content_digest
                                if capture is not None else ""),
                            "samples_per_frame": (int(getattr(
                                current.cloth_next,
                                "collider_samples_per_frame",
                                COLLIDER_SAMPLES_PER_FRAME))
                                if capture is not None else 0),
                            "vertex_count": len(vertices),
                            "triangle_count": len(triangles)})
    scene_colliders, collider_specs = _ensure_solver_static(
        scene_colliders, collider_specs)
    generated_by_uuid = {
        export_identity.export_uuid(obj): collider_proxy.is_generated_proxy(obj)
        for obj, _vertices, _triangles, _world, _capture in collider_records}
    source_faces_by_uuid = {
        export_identity.export_uuid(obj):
            _source_polygon_indices(obj, len(triangles))
        for obj, _vertices, triangles, _world, _capture in collider_records}
    solver_input = intersection_diagnostics.build_solver_input_snapshot(
        ((scene_cloth, deformable_role,
          _source_polygon_indices(cloth_obj, len(cloth_triangles)), False),)
        + tuple(
            (item, "COLLIDER", source_faces_by_uuid.get(item.uuid),
             generated_by_uuid.get(item.uuid, False))
            for item in scene_colliders),
        bake_start_frame=bake_range.start)
    project_name = new_project_name()
    work_directory = Path(bpy.app.tempdir) / f"cloth_next_run_{project_name}"
    payload_cache = _payload_cache_for(cloth_obj)
    scene_cache_key = deterministic_key("scene", {
        "solver_installation": _resolved_installation_id(resolved),
        "protocol_version": resolved.protocol_version or "",
        "schema_version": resolved.schema_version or "",
        "geometry": snapshot.geometry_fingerprint,
        "topology": snapshot.topology_signature,
        "objects": [(scene_cloth.uuid, scene_cloth.name, deformable_role)],
        "colliders": motion_meta,
        "pin": pin_snapshot.fingerprint,
        "sewing": stitch_pairs,
        "friction_regions": _friction_region_settings(cloth_obj),
        "frame_range": [bake_range.start, bake_range.end],
        "fps": _scene_fps(context),
    })
    try:
        deforming_capture = any(
            capture is not None and
            capture.motion_type == "DEFORMING_ANIMATED"
            for _obj, _vertices, _triangles, _world, capture
            in collider_records)
        def encode_scene_payload():
          if deforming_capture:
            encoding_total = max(
                int(capture.animation["vert_frames"].shape[0])
                for _obj, _vertices, _triangles, _world, capture
                in collider_records
                if capture is not None and
                capture.motion_type == "DEFORMING_ANIMATED")
            shared_controller.update(
                status_message="Encoding animated Collider data",
                activity_code=BakeActivity.ENCODING_SCENE,
                progress_current=0, progress_total=encoding_total)
            def encoding_progress(current, total):
                if _cancel_event.is_set():
                    raise SessionCancelled()
                shared_controller.update(
                    status_message=(f"Encoding animated Colliders · "
                                    f"{current} / {total}"),
                    activity_code=BakeActivity.ENCODING_SCENE,
                    progress_current=current, progress_total=total)
            group = ({"CLOTH": GROUP_SHELL, "ROD": GROUP_ROD,
                      "SOFT_BODY": GROUP_SOLID,
                      "RIGID_BODY": GROUP_PDRD}[deformable_role])
            return encode_multi_deformable_scene_file(
                ((scene_cloth, group),), scene_colliders,
                work_directory / "scene.cbor",
                progress=encoding_progress, schema_version=wire_schema)
          if deformable_role == "CLOTH":
            return encode_scene(
                scene_cloth, scene_colliders, schema_version=wire_schema)
          return encode_deformable_scene(
                scene_cloth, scene_colliders,
                group_type=("ROD" if deformable_role == "ROD" else
                            "PDRD" if deformable_role == "RIGID_BODY" else
                            "SOLID"),
                schema_version=wire_schema)
        data_payload, data_hash = _cached_payload(
            payload_cache, "scene", scene_cache_key, encode_scene_payload)
    finally:
        for _obj, _vertices, _triangles, _world, capture in collider_records:
            if capture is not None:
                capture.cleanup()
    frame_count = bake_range.output_count
    quality = snapshot.quality
    settings = SimulationSettings(
        frame_count=frame_count, fps=_scene_fps(context),
        gravity_blender=force_capture.initial.gravity, quality=quality,
        wind_blender=force_capture.initial.wind,
        air_density=(force_capture.initial.air_density
                     if "AIR_DENSITY" in force_capture.active_scalar_types else None),
        air_friction=(force_capture.initial.air_friction
                      if "AIR_FRICTION" in force_capture.active_scalar_types else None),
        vertex_air_damp=(force_capture.initial.vertex_air_damp
                         if "VERTEX_AIR_DAMP" in force_capture.active_scalar_types else None),
        dynamic_parameters=_all_dynamic_parameters(
            snapshot, force_capture, _scene_fps(context)),
        **_recovery_param_kwargs(scene))
    def encode_param_payload():
      if deformable_role == "CLOTH":
        return encode_multi_collider_param(
            settings, cloth_obj.name, cloth_uuid, collider_specs, shell=shell,
            contact_enabled=contact_enabled, static_pin=pin_config,
            schema_version=wire_schema)
      return encode_deformable_param(
            settings, cloth_obj.name, cloth_uuid, collider_specs,
            group_type=("ROD" if deformable_role == "ROD" else
                        "PDRD" if deformable_role == "RIGID_BODY" else
                        "SOLID"),
            material=shell, contact_enabled=contact_enabled,
            schema_version=wire_schema)
    param_cache_key = _param_source_key(
        context, snapshot, force_capture, (cloth_uuid,),
        tuple(item[1] for item in collider_specs), (pin_config,))
    param_payload, param_hash = _cached_payload(
        payload_cache, "param", param_cache_key, encode_param_payload)
    # Reused from the single authoritative validation — the topology is not
    # hashed and the pin group is not scanned a second time here.
    settings_fp = snapshot.settings_fingerprint
    geometry_fp = snapshot.geometry_fingerprint
    fingerprint = bake_fingerprint(settings_fp, geometry_fp)
    object_identity = {
        "object_key": validation_state.object_key(cloth_obj),
        "deformable_type": deformable_role,
        "topology_signature": snapshot.topology_signature,
        "geometry_fingerprint": geometry_fp,
    }
    scene_identity = {
        "settings_fingerprint": settings_fp,
        "geometry_fingerprint": geometry_fp,
        "fps": _scene_fps(context),
        "frame_start": bake_range.start,
        "frame_end": bake_range.end,
        "colliders": [{key: value for key, value in item.items()
                       if key != "uuid"} for item in motion_meta],
    }
    material_meta = {
        "fingerprints": {
            "settings": settings_fp,
            "geometry": geometry_fp,
            "combined": fingerprint,
            "topology": snapshot.topology_signature,
            "object": cache_metadata.deterministic_hash(object_identity),
            "scene": cache_metadata.deterministic_hash(scene_identity),
        },
        "identities": {
            "cloth_next_version": manifest_version(),
            "blender_version": _blender_version(),
            "object": object_identity,
            "solver": {
                "mode": resolved.mode.name,
                "installation_id": (
                    _resolved_installation_id(resolved) or "unregistered"),
                "official_release_tag": _resolved_release_tag(resolved),
                "package_version": resolved.package_version or "unknown",
                "protocol_version": resolved.protocol_version or "unknown",
                "schema_version": resolved.schema_version or "unknown",
                "source_metadata": getattr(resolved, "source_metadata", None) or {},
            },
        },
        "expected": {
            "vertex_count": len(cloth_vertices),
            "frame_count": frame_count,
            "start_frame": import_result.PC2_START_FRAME,
            "sample_rate": import_result.PC2_SAMPLE_RATE,
        },
        "details": {
        "preset": preset_identifier,
        "contact_enabled": contact_enabled,
        "deformable_type": deformable_role,
        "material": asdict(shell),
        "static": static_wire_params(static) if static is not None else None,
        "colliders": motion_meta,
        "quality": {
            "dt": settings.quality.time_step,
            "min-newton-steps": settings.quality.min_newton_steps,
            "cg-max-iter": settings.quality.cg_max_iter,
            "cg-tol": settings.quality.cg_tol,
            "target-toi": settings.quality.target_toi,
            "line-search-max-t": settings.quality.line_search_max_t,
            "constraint-ghat": settings.quality.constraint_ghat,
            "constraint-tol": settings.quality.constraint_tol,
            "ccd-reduction": settings.quality.ccd_reduction,
            "ccd-max-iter": settings.quality.ccd_max_iter,
            "max-newton-steps": settings.quality.max_newton_steps,
            "max-dx": settings.quality.max_dx,
            "eiganalysis-eps": settings.quality.eigenanalysis_eps,
            "friction-eps": settings.quality.friction_eps,
            "csrmat-max-nnz": settings.quality.csrmat_max_nnz,
            "barrier": settings.quality.contact_barrier,
        },
        "blender_start_frame": bake_range.start,
        "blender_end_frame": bake_range.end,
        "output_frame_count": frame_count,
        "solver_step_count": bake_range.solver_steps,
        "fps": _scene_fps(context),
        "pinning": {
            "enabled": pin_snapshot.enabled,
            "mode": pin_snapshot.mode.value,
            "group": pin_snapshot.group_name,
            "count": len(pin_snapshot.vertex_indices),
            "threshold": pin_snapshot.threshold,
            "fingerprint": pin_snapshot.fingerprint,
        },
        },
    }

    session_scene = SessionScene(
        project_name=project_name,
        cloth_name=cloth_obj.name, cloth_uuid=cloth_uuid,
        cloth_vertex_count=len(cloth_vertices),
        collider_name=collider_specs[0][0] if collider_specs else "",
        collider_uuid=collider_specs[0][1] if collider_specs else "",
        frame_count=frame_count,
        data_payload=data_payload, param_payload=param_payload,
        data_hash=data_hash, param_hash=param_hash,
        deformable_type=("ROD" if deformable_role == "ROD" else
                         "SOLID" if deformable_role == "SOFT_BODY" else
                         "PDRD" if deformable_role == "RIGID_BODY" else
                         "SHELL"),
        deformable_world_matrix=solver_world_matrix(cloth_world))

    configured_cache = str(getattr(cloth_obj.cloth_next,
                                   "cache_directory", "") or "").strip()
    cache_directory = (Path(bpy.path.abspath(configured_cache))
                       if configured_cache else _cache_directory())
    pc2_path = cache_directory / f"cn_test_cloth_{project_name[10:]}.pc2"
    return RunPlan(scene=session_scene, resolved=resolved,
                   initial_local=cloth_vertices, world_matrix=cloth_world,
                   cloth_object_name=cloth_obj.name,
                   work_directory=work_directory, pc2_path=pc2_path,
                   frame_count=frame_count,
                   frame_start=bake_range.start, frame_end=bake_range.end,
                   fps=_scene_fps(context),
                   settings_fingerprint=settings_fp,
                   geometry_fingerprint=geometry_fp,
                   topology_signature=snapshot.topology_signature,
                   preset_identifier=preset_identifier,
                   material_meta=material_meta,
                   deformable_role=deformable_role,
                   stitch_pairs=stitch_pairs,
                   stitch_snap_distance=(
                       max(1e-6, 2.0 * shell.collision_gap
                           + shell.surface_offset)
                       if stitch_pairs else 0.0),
                   scene_cache_key=source_key or "",
                   pin_configs=(pin_config,),
                   param_cache_key=param_cache_key,
                   solver_input=solver_input)


def build_run_plan(context, *, animated_pin_samples=None,
                   force_capture: ForceCapture | None = None,
                   collider_captures=None,
                   snapshot: ValidationSnapshot | None = None) -> RunPlan:
    """Build and time a pure worker plan from one validation snapshot."""
    global _export_timing_sink, _export_cache_event_sink
    started = time.monotonic()
    timings = dict(getattr(snapshot, "timings", {}) if snapshot else {})
    cache_events: dict[str, str] = {}
    previous = _export_timing_sink
    previous_cache_events = _export_cache_event_sink
    _export_timing_sink = timings
    _export_cache_event_sink = cache_events
    try:
        plan = _build_run_plan_impl(
            context, animated_pin_samples=animated_pin_samples,
            force_capture=force_capture,
            collider_captures=collider_captures, snapshot=snapshot)
        if (snapshot is not None and plan.scene_cache_key
                and not timings.get("scene_early_cache_hit")):
            _store_scene_plan_cache(
                context, snapshot, plan, plan.scene_cache_key)
    finally:
        _export_timing_sink = previous
        _export_cache_event_sink = previous_cache_events
    timings["export_preparation"] = time.monotonic() - started
    plan = replace(
        plan, export_timings=timings,
        export_cache_events=cache_events)
    return _configure_recovery(context, snapshot, plan)


def _configure_recovery(context, snapshot, plan: RunPlan) -> RunPlan:
    settings = getattr(context.scene, "cloth_next_recovery", None)
    if (snapshot is None or settings is None
            or not bool(getattr(settings, "enabled", False))
            or not plan.scene_cache_key or not plan.param_cache_key):
        return plan
    targets = _plan_deformables(plan)
    cache_root = targets[0].pc2_path.parent
    root = recovery.recovery_root(cache_root, plan.scene_cache_key)
    metadata = root / recovery.METADATA_NAME
    collider_sampling = tuple(sorted(
        (export_identity.export_uuid(obj),
         int(getattr(obj.cloth_next, "collider_samples_per_frame", 1)))
        for obj in snapshot.collider_objs))
    identity = recovery.RecoveryIdentity(
        scene_key=plan.scene_cache_key,
        param_key=plan.param_cache_key,
        export_uuids=tuple(sorted(
            [target.uuid for target in targets]
            + [export_identity.export_uuid(obj)
               for obj in snapshot.collider_objs])),
        geometry_fingerprint=plan.geometry_fingerprint,
        topology_fingerprint=hashlib.sha256(
            json.dumps(sorted(
                (target.uuid, target.topology_signature)
                for target in targets),
                separators=(",", ":")).encode("utf-8")).hexdigest(),
        frame_start=plan.frame_start, frame_end=plan.frame_end,
        fps=float(plan.fps), collider_sampling=collider_sampling,
        solver_version=plan.resolved.package_version or "unknown",
        protocol_version=plan.resolved.protocol_version or "unknown",
        solver_schema_version=plan.resolved.schema_version or "unknown",
        solver_installation_id=(
            _resolved_installation_id(plan.resolved) or "unregistered"),
        solver_release_tag=_resolved_release_tag(plan.resolved))
    resume_requested = bool(getattr(settings, "resume_requested", False))
    if resume_requested:
        # load_post selected a specific durable project.  The Scene export
        # cache key is deliberately conservative and can change when runtime
        # handler identity changes across a Blender restart, even though all
        # authoritative Recovery inputs are identical.  Resume the selected
        # project only: preserve its opaque scene key, then compare every
        # semantic identity field against the freshly built Bake identity.
        selected_root = Path(str(getattr(
            settings, "recovery_directory", "") or ""))
        selected_metadata = selected_root / recovery.METADATA_NAME
        selected_record = recovery.load_project(selected_metadata)
        if selected_record is None:
            settings.resume_requested = False
            eligibility = recovery.evaluate_resumable(selected_metadata)
            _apply_eligibility_to_settings(settings, eligibility)
            raise SceneValidationError(
                "The selected recovery metadata is missing, corrupt, or no "
                "longer readable. Resume was stopped without starting a new "
                "Bake.")
        root = selected_root
        metadata = selected_metadata
        identity = replace(
            identity, scene_key=selected_record.identity.scene_key)
    partials = tuple(
        (target.uuid, str(
            root / "partials" / f"{target.uuid}.pc2.partial"))
        for target in targets)
    # One authoritative eligibility decision for both the snapshot and the
    # resume gate; no caller maintains its own copy of the state set.
    eligibility = recovery.evaluate_resumable(metadata, identity)
    if resume_requested and not eligibility.resumable:
        # A verified checkpoint survives but no longer matches this scene or
        # solver. Refuse to silently start fresh and overwrite it. Leave the
        # panel honest and the gate un-stuck: the failure is permanent until
        # the user discards the checkpoint or fixes the mismatch.
        settings.resume_requested = False
        _apply_eligibility_to_settings(settings, eligibility)
        raise SceneValidationError(
            "The saved recovery checkpoint cannot be resumed: "
            f"{eligibility.reason}. Start Fresh or Clear Checkpoints to "
            "discard it, or fix the mismatch.")
    resume = False
    completed = ()
    server_root = root / "server-data"
    project_name = plan.scene.project_name
    record: recovery.ProjectRecord | None = None
    if resume_requested and eligibility.resumable:
        eligibility, _promoted = recovery.reconcile_resumable(metadata, identity)
        record = recovery.load_project(metadata)
        if not eligibility.resumable or record is None:
            # The checkpoint vanished or became unreadable between the two
            # evaluator passes (another process cleared it). Refuse the
            # resume instead of falling back to a silent fresh start.
            settings.resume_requested = False
            _apply_eligibility_to_settings(settings, eligibility)
            raise SceneValidationError(
                "The saved recovery checkpoint can no longer be resumed. "
                "Start Fresh or Clear Checkpoints to discard it, or run a "
                "new Bake.")
        resume = True
        server_root = Path(record.server_data_root)
        project_name = record.project_id
        counts = []
        for target in targets:
            partial_path = dict(record.partial_pc2).get(target.uuid)
            if not partial_path:
                counts = []
                break
            try:
                counts.append(pc2.partial_frame_count(
                    Path(partial_path), pc2.Pc2Header(
                        len(target.initial_local),
                        import_result.PC2_START_FRAME,
                        import_result.PC2_SAMPLE_RATE,
                        plan.frame_count)))
            except (OSError, pc2.Pc2Error):
                counts = []
                break
        if counts and len(set(counts)) == 1:
            # PC2 frame zero is the initial pose; solver frames start at 1.
            completed = tuple(range(1, max(1, counts[0])))
        else:
            # Without an authenticated partial, fetch every output frame
            # again. The solver project is still safely reusable.
            completed = ()
    settings.recovery_directory = str(root)
    _apply_eligibility_to_settings(settings, eligibility)
    options = RecoveryOptions(
        enabled=True, metadata_path=metadata, identity=identity,
        server_data_root=server_root, resume=resume,
        keep_saved_states=int(settings.keep_saved_states),
        save_on_cancel=bool(settings.save_on_cancel),
        keep_on_finish=bool(settings.save_on_finish),
        auto_save_interval=(int(getattr(settings, "checkpoint_interval", 0))
                            if bool(getattr(settings, "auto_save", False))
                            else 0),
        completed_solver_frames=completed, partial_pc2=partials)
    settings.resume_requested = False
    if resume:
        return replace(
            plan, scene=replace(plan.scene, project_name=project_name),
            recovery_options=options)
    return replace(plan, recovery_options=options)


def _apply_eligibility_to_settings(settings, eligibility) -> None:
    """Cache one ResumeEligibility verdict on the artist-facing properties."""
    settings.compatible = bool(eligibility.compatible)
    settings.resumable = bool(eligibility.resumable)
    settings.latest_checkpoint_frame = eligibility.latest_checkpoint_frame
    settings.checkpoint_count = eligibility.checkpoint_count
    settings.older_checkpoint_preserved = bool(
        eligibility.resumable and eligibility.error)
    state = eligibility.state
    if state is None:
        settings.status = "No Recovery Checkpoint"
        settings.status_detail = eligibility.reason
    elif eligibility.compatible is None:
        # load_post has only durable metadata.  It may show a verified state,
        # but calling it compatible before Bake preparation recomputes the
        # current identity would be misleading and could invite unsafe reuse.
        settings.status = "Checkpoint Found"
        settings.status_detail = (
            "Verified checkpoint · Compatibility will be checked before "
            "Resume")
    elif eligibility.resumable:
        settings.status = "Resume Available"
        settings.status_detail = (
            eligibility.error or "Verified checkpoint · Resume available")
    elif state is recovery.ProjectState.FAILED and eligibility.error:
        settings.status = state.value.title()
        settings.status_detail = eligibility.error
    else:
        settings.status = state.value.title()
        settings.status_detail = eligibility.reason


# ---------------------------------------------------------------------------
# Worker (never touches bpy) and main-thread pump

def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def prepare_cache_for_new_run(plan: RunPlan) -> None:
    """Validate old ownership; preserve the old result until attach succeeds."""
    if getattr(plan, "deformables", ()):
        for target in plan.deformables:
            prepare_cache_for_new_run(_plan_for_target(plan, target))
        return
    obj = bpy.data.objects.get(plan.cloth_object_name)
    if obj is None:
        raise SceneValidationError("The Cloth object no longer exists.")
    owned = [mod for mod in obj.modifiers
             if is_cloth_next_playback_modifier(obj,mod)]
    targets: list[Path] = []
    cache_root = plan.pc2_path.parent.resolve()
    if getattr(obj, "type", "") == "CURVE":
        recorded = str(getattr(getattr(obj, "data", None), "get",
                               lambda *_: "")("cloth_next_rod_cache", "") or "")
        if recorded:
            path = Path(recorded).resolve()
            if (not _is_within(path, cache_root)
                    or not path.name.startswith("cn_test_cloth_")
                    or path.suffix.lower() != ".pc2"):
                raise SceneValidationError(
                    "The previous Cable / Rope cache could not be replaced. "
                    "Rebake was not started.")
            targets.extend((path, path.with_suffix(".meta.json")))
    for mod in owned:
        value = str(getattr(mod, "filepath", "") or "")
        if not value:
            continue
        path = Path(bpy.path.abspath(value)).resolve()
        if (not _is_within(path, cache_root)
                or not path.name.startswith("cn_test_cloth_")
                or path.suffix.lower() != ".pc2"):
            raise SceneValidationError(
                "The previous Cloth NeXt cache could not be removed. "
                "Rebake was not started.")
        targets.extend((path, path.with_suffix(".meta.json")))
    # Validate every target without mutating Blender or disk. The old cache
    # remains active until the new transactional cache is attached.
    for target in targets:
        if not _is_within(target, cache_root):
            raise SceneValidationError(
                "The previous Cloth NeXt cache could not be removed. "
                "Rebake was not started.")


def _discard_incomplete(plan: RunPlan | None, *, state: str = "failed",
                        reason: str = "") -> None:
    if plan is None:
        return
    if getattr(plan, "deformables", ()):
        for target in plan.deformables:
            _discard_incomplete(_plan_for_target(plan, target), state=state,
                                reason=reason)
        return
    if _is_within(plan.pc2_path, plan.pc2_path.parent):
        try:
            plan.pc2_path.unlink(missing_ok=True)
        except OSError:
            pass
    sidecar = cache_metadata.sidecar_path(plan.pc2_path)
    if not _is_within(sidecar, plan.pc2_path.parent):
        return
    try:
        existing = (json.loads(sidecar.read_text(encoding="utf-8"))
                    if sidecar.is_file() else {})
        if not isinstance(existing, dict):
            existing = {}
        existing.update({
            "schema_version": cache_metadata.CACHE_METADATA_SCHEMA_VERSION,
            "completion_state": state,
            "cache_format": "POINTCACHE2",
            "cache_file": plan.pc2_path.name,
            "failure_reason": reason,
        })
        cache_metadata.write_atomic(sidecar, existing)
    except (OSError, ValueError, TypeError):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass


def _record_worker_failure(plan: RunPlan, summary: str, details: str,
                           error_code: str = "CNX-E199", *,
                           technical_details: str = "") -> str:
    """Persist and print worker diagnostics without masking the real error."""
    failure_path = plan.work_directory / "failure.log"
    project_name = str(getattr(plan.scene, "project_name", "unknown"))
    technical_section = (
        f"\n\nTechnical diagnostics\n{technical_details.rstrip()}\n"
        if technical_details and technical_details.strip() != details.strip()
        else "")
    report = (f"Cloth NeXt Bake failure\n"
              f"Error code: {error_code}\n"
              f"Job: {project_name}\n"
              f"Summary: {summary}\n\n{details.rstrip()}"
              f"{technical_section}\n")
    temporary = failure_path.with_name(
        f".{failure_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(report)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, failure_path)
        location = str(failure_path.resolve())
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        location = f"unavailable ({type(exc).__name__}: {exc})"
    visible = f"{details}\nDiagnostic log: {location}"
    print(f"Cloth NeXt Bake failed: {summary}\n{visible}", flush=True)
    log_with_context(get_logger("solver.worker"), 40, summary, {
        "error_code": error_code, "details": details,
        "diagnostic_log": location,
    })
    return visible


def _preserve_failed_partial(writer) -> tuple[Path, int] | None:
    """Keep only a frame-aligned playback prefix after a solver failure."""
    if writer is None:
        return None
    # Frame zero is the input pose, not completed solver work.
    if int(getattr(writer, "frames_written", 0)) <= 1:
        writer.abort()
        return None
    try:
        path = writer.preserve()
        frames = pc2.partial_frame_count(path, writer.header)
        if frames != writer.frames_written:
            raise pc2.Pc2Error(
                f"partial PC2 has {frames} frames, expected "
                f"{writer.frames_written}")
        return path, frames
    except (OSError, pc2.Pc2Error, ValueError):
        writer.abort()
        return None


def _with_preserved_partial_error(
        exc: ClothNextError,
        preserved: tuple[tuple[str, Path, int], ...]) -> ClothNextError:
    if not preserved:
        return exc
    source = exc.record
    frame_counts = tuple(sorted({item[2] for item in preserved}))
    paths = tuple(str(item[1]) for item in preserved)
    return ClothNextError(ErrorRecord.create(
        category=source.category,
        user_message=(
            f"{source.user_message} Completed frames were preserved."),
        technical_message=(
            f"{source.technical_message}; "
            f"validated_partial_pc2={paths}; "
            f"partial_frame_counts={frame_counts}"),
        recommended_action=source.recommended_action,
        recoverable=True,
        context={
            **{key: value for key, value in source.context},
            "validated_partial_pc2": paths,
            "partial_frame_counts": frame_counts,
        },
        exception=exc), violations=exc.violations)


def _present_worker_error(plan: RunPlan, exc: ClothNextError, *,
                          enriched=None) -> tuple[str, str]:
    """Translate technical solver failures into actionable Blender language."""
    record = exc.record
    technical = record.technical_message
    enriched = (_convert_solver_violations(plan, exc)
                if enriched is None else enriched)
    if enriched:
        summary, action = intersection_diagnostics.artist_message(enriched[0])
        detail_lines = [
            "Stage: initial solver-pose intersection validation",
            f"Cause: {summary}",
            f"What to do: {action}",
            f"Reported violations: {enriched[0].total_count}",
        ]
        for number, violation in enumerate(enriched[:10], 1):
            names = " / ".join(item.object_name
                               for item in violation.elements)
            detail_lines.append(
                f"{number}. {violation.classification}: {names}; "
                f"combined pair={violation.combined_pair}")
        return summary, "\n".join(detail_lines)
    intersections = re.search(
        r"(\d+)\s+self[- ]intersections?\s*\((\d+)\s+tri-tri\)",
        technical, re.IGNORECASE)
    if intersections:
        count = int(intersections.group(1))
        summary = f"Intersections detected ({count})."
        details = (
            "Stage: scene validation\n"
            f"Cause: The solver found {count} self-intersecting triangle "
            f"pair{'s' if count != 1 else ''}.\n"
            "What to do: Run Validate, then repair the marked intersecting "
            "geometry before baking again.")
        return summary, details
    collision_detection = re.search(
        r"(?:Continuous collision detection failed:\s*)?"
        r"advance failed at frame (\d+).*?ccd\s*=\s*false",
        technical, re.IGNORECASE | re.DOTALL)
    if collision_detection:
        solver_frame = int(collision_detection.group(1))
        blender_frame = plan.frame_start + solver_frame
        contacts = re.search(r"num_contact:\s*(\d+)", technical)
        contact_line = (
            f"\nActive contacts: {int(contacts.group(1))}"
            if contacts else "")
        summary = (
            f"Simulation could not advance at Blender frame {blender_frame}.")
        details = (
            "Stage: continuous collision detection\n"
            f"Solver frame: {solver_frame}\n"
            f"Blender frame: {blender_frame}\n"
            "Cause: Collision detection could not find a safe movement "
            f"step.{contact_line}\n"
            "What to do: Increase Solver Quality for a smaller Time Step. "
            "If it still fails, reduce Collision Gap and Friction, increase "
            "animated Collider sampling, and check for large movement into "
            "contact between the surrounding frames.")
        return summary, details
    convergence = re.search(
        r"Linear solver failed to converge: advance failed at frame (\d+)",
        technical, re.IGNORECASE)
    if convergence:
        solver_frame = int(convergence.group(1))
        blender_frame = plan.frame_start + solver_frame
        summary = ("Simulation could not converge at Blender frame "
                   f"{blender_frame}.")
        action = ("Lower Friction first. If it still fails, reduce Collision "
                  "Gap, increase animated Collider sampling, then try a "
                  "smaller Time Step.")
        details = (f"Stage: collision and constraint solve\n"
                   f"Solver frame: {solver_frame}\n"
                   f"Blender frame: {blender_frame}\n"
                   f"Cause: {technical}\n"
                   f"What to do: {action}")
        return summary, details
    concise = re.split(
        r"(?:\n--- Solver Log|;\s*owned_process_id=|;\s*stdout_tail=|"
        r";\s*stderr_tail=|;\s*progress_tail=)",
        technical, maxsplit=1)[0].strip()
    if len(concise) > 600:
        concise = concise[:597].rstrip() + "..."
    details = (f"Cause: {concise}\n"
               f"What to do: {record.recommended_action}")
    return record.user_message, details


def _convert_solver_violations(plan: RunPlan, exc: ClothNextError):
    snapshot = getattr(plan, "solver_input", None)
    raw = getattr(exc, "violations", ())
    if snapshot is None:
        return ()
    if not raw:
        mirror = (
            plan.work_directory / "server-data"
            / f"{plan.scene.project_name}.build_violations.json")
        try:
            if 0 < mirror.stat().st_size <= 4 * 1024 * 1024:
                payload = json.loads(mirror.read_text(encoding="utf-8"))
                values = (
                    payload.get("violations", ())
                    if isinstance(payload, dict) else ())
                if isinstance(values, list):
                    raw = tuple(
                        item for item in values if isinstance(item, dict))
        except (FileNotFoundError, OSError, UnicodeError,
                json.JSONDecodeError):
            raw = ()
    if not raw and re.search(
            r"\d+\s+self[- ]intersections?", exc.record.technical_message,
            re.IGNORECASE):
        raw = _locate_confirmed_intersection_pairs(
            snapshot, plan.work_directory / "intersection_locator.json")
    if not raw:
        return ()
    total = len(raw)
    converted = tuple(
        result for result in (
            intersection_diagnostics.convert_violation(
                item, snapshot, total_count=total) for item in raw)
        if result is not None)
    for violation in converted[:10]:
        average, minimum = intersection_diagnostics.triangle_metrics(
            tuple(item.vertices for item in violation.elements
                  if item.kind == "TRIANGLE"))
        log_with_context(
            get_logger("solver.intersections"), 40,
            "Initial solver-pose intersection", {
                "classification": violation.classification,
                "detection_method": violation.detection_method,
                "combined_pair": violation.combined_pair,
                "objects": tuple(item.object_name
                                 for item in violation.elements),
                "object_uuids": tuple(item.object_uuid
                                      for item in violation.elements),
                "roles": tuple(item.role for item in violation.elements),
                "local_triangle_indices": tuple(
                    item.local_triangle_index for item in violation.elements),
                "source_polygon_indices": tuple(
                    item.source_polygon_index for item in violation.elements),
                "triangle_positions": tuple(
                    item.vertices for item in violation.elements),
                "average_edge_length": average,
                "minimum_local_edge_length": minimum,
                "bake_start_frame": snapshot.bake_start_frame,
                "generated_proxy": tuple(
                    item.generated_proxy for item in violation.elements),
            })
    return converted


def _locate_confirmed_intersection_pairs(snapshot, diagnostic_path=None):
    """Locate faces only after the solver confirmed an intersection."""
    from mathutils.bvhtree import BVHTree

    vertices = []
    polygons = []
    for triangle in snapshot.triangles:
        start = len(vertices)
        vertices.extend(triangle.vertices)
        polygons.append((start, start + 1, start + 2))
    if not polygons:
        return ()
    first_tree = BVHTree.FromPolygons(
        vertices, polygons, all_triangles=True, epsilon=0.0)
    second_tree = BVHTree.FromPolygons(
        vertices, polygons, all_triangles=True, epsilon=0.0)
    found = []
    overlap_count = 0
    tested_count = 0
    for first, second in first_tree.overlap(second_tree):
        overlap_count += 1
        if first >= second:
            continue
        left = snapshot.triangles[first]
        right = snapshot.triangles[second]
        if left.owner.internal or right.owner.internal:
            continue
        if left.owner.role == "COLLIDER" and right.owner.role == "COLLIDER":
            continue
        tested_count += 1
        strict = intersection_diagnostics.triangles_strictly_cross(
            left.vertices, right.vertices)
        coplanar = (not strict
                    and intersection_diagnostics.triangles_coplanar_overlap(
                        left.vertices, right.vertices))
        if strict or coplanar:
            found.append({"type": "self_intersection",
                          "combined_pair": [first, second],
                          "detection_method": (
                              "STRICT_CROSSING" if strict
                              else "COPLANAR_OVERLAP")})
            if len(found) >= 100:
                break
    if diagnostic_path is not None:
        payload = {
            "snapshot_triangles": len(snapshot.triangles),
            "bvh_overlaps": overlap_count,
            "tested_candidates": tested_count,
            "located_intersections": len(found),
        }
        try:
            Path(diagnostic_path).write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            log_with_context(
                get_logger("solver.intersections"), 30,
                "Could not persist intersection locator diagnostics", {
                    "path": str(diagnostic_path), "reason": str(exc)})
    return tuple(found)

def _worker_main_multi(plan: RunPlan) -> None:
    def emit(event) -> None:
        _queue.put(("event", event))

    targets = _plan_deformables(plan)
    writers = {}
    partials = {}
    recovery_partials = dict(
        plan.recovery_options.partial_pc2
        if plan.recovery_options is not None else ())
    failure_stage = "CACHE_SETUP"
    try:
        for target in targets:
            if target.material_meta:
                partial = cache_metadata.partial_metadata(
                    cache_path=target.pc2_path,
                    fingerprints=target.material_meta["fingerprints"],
                    identities=target.material_meta["identities"],
                    expected=target.material_meta["expected"],
                    details=target.material_meta["details"])
                cache_metadata.write_atomic(
                    cache_metadata.sidecar_path(target.pc2_path), partial)
                partials[target.uuid] = partial
            writer = pc2.StreamingPc2Writer(
                target.pc2_path, vertex_count=len(target.initial_local),
                frame_count=plan.frame_count,
                start_frame=import_result.PC2_START_FRAME,
                sample_rate=import_result.PC2_SAMPLE_RATE,
                resume_path=(
                    Path(recovery_partials[target.uuid])
                    if target.uuid in recovery_partials else None))
            if writer.frames_written == 0:
                writer.write_frame(target.initial_local)
            writers[target.uuid] = writer
        to_local = {target.uuid: solver_world_to_object_local(
                    target.world_matrix) for target in targets}
        transform_seconds = 0.0
        write_seconds = 0.0

        def consume(frame: SolverFrame) -> None:
            nonlocal transform_seconds, write_seconds
            if _cancel_event.is_set():
                raise SessionCancelled()
            emit(type("CacheEvent", (), {
                "phase": "TRANSFORMING_FRAME",
                "message": (f"Creating {len(targets)} playback caches Â· frame "
                            f"{frame.solver_frame + 1} / {plan.frame_count}"),
                "frame_current": frame.solver_frame,
                "frame_total": plan.frame_count,
                "indeterminate": False})())
            for target in targets:
                positions = frame.positions_by_uuid.get(target.uuid)
                if positions is None:
                    raise ValueError(
                        f"solver frame has no result for {target.object_name}")
                positions = _snap_closed_sewing_pairs(
                    positions, target.stitch_pairs,
                    target.stitch_snap_distance)
                step = time.monotonic()
                local = transform_points_numpy(to_local[target.uuid], positions)
                transform_seconds += time.monotonic() - step
                step = time.monotonic()
                writers[target.uuid].write_frame(local)
                write_seconds += time.monotonic() - step

        session = SolverSession(
            resolved=plan.resolved, scene=plan.scene,
            work_directory=plan.work_directory, emit=emit,
            cancel_event=_cancel_event, frame_sink=consume,
            recovery_options=plan.recovery_options)
        failure_stage = "SIMULATION"
        diagnostics = session.run()
        _merge_export_diagnostics(diagnostics, plan)
        diagnostics.timings["coordinate_transform"] = transform_seconds
        diagnostics.timings["pc2_write"] = write_seconds
        emit(type("CacheEvent", (), {
            "phase": "FINALIZING_CACHE",
            "message": f"Finalizing {len(targets)} playback caches",
            "frame_current": None, "frame_total": plan.frame_count,
            "indeterminate": True})())
        failure_stage = "CACHE_FINALIZE"
        headers = {}
        for target in targets:
            writer = writers[target.uuid]
            headers[target.uuid] = writer.finalize()
            partial = partials.get(target.uuid)
            if partial is not None:
                identities = dict(partial["identities"])
                solver_identity = dict(identities.get("solver", {}))
                solver_identity.update({
                    "mode": diagnostics.solver_mode,
                    "package_version": diagnostics.package_version or "unknown",
                    "protocol_version": diagnostics.protocol_version or "unknown",
                    "schema_version": diagnostics.schema_version or "unknown"})
                identities["solver"] = solver_identity
                partial["identities"] = identities
                if getattr(diagnostics, "contact_samples", 0):
                    details = dict(partial.get("details", {}))
                    details["contacts"] = {
                        "last": diagnostics.contact_last,
                        "peak": diagnostics.contact_peak,
                        "samples": diagnostics.contact_samples}
                    partial["details"] = details
                metadata = cache_metadata.completed_metadata(
                    partial, cache_path=target.pc2_path,
                    timings=diagnostics.timings)
                cache_metadata.write_atomic(
                    cache_metadata.sidecar_path(target.pc2_path), metadata)
        diagnostics.timings["total"] = time.monotonic() - _run_started_at
        _queue.put(("finished", headers, diagnostics))
    except SessionCancelled as exc:
        for writer in writers.values():
            writer.preserve() if exc.resumable else writer.abort()
        _discard_incomplete(plan, state="cancelled",
                            reason="Bake cancelled before publication")
        _queue.put(("cancelled", exc.resumable, exc.recovery_outcome))
    except ClothNextError as exc:
        preserved = tuple(
            (uuid, result[0], result[1])
            for uuid, writer in writers.items()
            if (result := _preserve_failed_partial(writer)) is not None)
        exc = _with_preserved_partial_error(exc, preserved)
        _discard_incomplete(plan, state="failed", reason=str(exc))
        violations = _convert_solver_violations(plan, exc)
        summary, details = _present_worker_error(
            plan, exc, enriched=violations)
        code = classify_error("SIMULATING", summary, details, exc.record)
        _queue.put(("error", summary,
                    _record_worker_failure(
                        plan, summary, details, code,
                        technical_details=exc.record.technical_message), code,
                    violations))
    except Exception:
        for writer in writers.values():
            writer.abort()
        details = traceback.format_exc()
        _discard_incomplete(plan, state="failed", reason=details[-2000:])
        if failure_stage in {"CACHE_SETUP", "CACHE_FINALIZE"}:
            summary = "Creating the multi-object playback caches failed."
            code = classify_error("IMPORTING", summary, details)
        else:
            summary = "The solver session failed unexpectedly."
            code = classify_error("SIMULATING", summary, details)
        _queue.put(("error", summary,
                    _record_worker_failure(plan, summary, details, code), code))


def _worker_main(plan: RunPlan) -> None:
    if len(_plan_deformables(plan)) > 1:
        _worker_main_multi(plan)
        return
    def emit(event) -> None:
        _queue.put(("event", event))

    writer = None
    try:
        if plan.material_meta:
            partial = cache_metadata.partial_metadata(
                cache_path=plan.pc2_path,
                fingerprints=plan.material_meta["fingerprints"],
                identities=plan.material_meta["identities"],
                expected=plan.material_meta["expected"],
                details=plan.material_meta["details"])
            cache_metadata.write_atomic(
                cache_metadata.sidecar_path(plan.pc2_path), partial)
        writer = pc2.StreamingPc2Writer(
            plan.pc2_path, vertex_count=len(plan.initial_local),
            frame_count=plan.frame_count,
            start_frame=import_result.PC2_START_FRAME,
            sample_rate=import_result.PC2_SAMPLE_RATE,
            resume_path=(
                Path(dict(plan.recovery_options.partial_pc2).get(
                    str(getattr(plan.scene, "cloth_uuid", ""))))
                if plan.recovery_options is not None
                and dict(plan.recovery_options.partial_pc2).get(
                    str(getattr(plan.scene, "cloth_uuid", "")))
                else None))
        step = time.monotonic()
        if writer.frames_written == 0:
            writer.write_frame(plan.initial_local)
        write_seconds = time.monotonic() - step
        transform_seconds = 0.0
        to_local = solver_world_to_object_local(plan.world_matrix)

        def consume(frame: SolverFrame) -> None:
            nonlocal transform_seconds, write_seconds
            if _cancel_event.is_set():
                raise SessionCancelled()
            emit(type("CacheEvent", (), {
                "phase": "TRANSFORMING_FRAME",
                "message": (f"Creating playback cache · frame "
                            f"{frame.solver_frame + 1} / {plan.frame_count}"),
                "frame_current": frame.solver_frame,
                "frame_total": plan.frame_count,
                "indeterminate": False,
            })())
            step = time.monotonic()
            positions = _snap_closed_sewing_pairs(
                frame.positions_solver_world, plan.stitch_pairs,
                plan.stitch_snap_distance)
            local = transform_points_numpy(to_local, positions)
            transform_seconds += time.monotonic() - step
            if _cancel_event.is_set():
                raise SessionCancelled()
            step = time.monotonic()
            writer.write_frame(local)
            write_seconds += time.monotonic() - step

        session = SolverSession(resolved=plan.resolved, scene=plan.scene,
                                work_directory=plan.work_directory,
                                emit=emit, cancel_event=_cancel_event,
                                frame_sink=consume,
                                recovery_options=plan.recovery_options)
        diagnostics = session.run()
        if not hasattr(diagnostics, "timings"):
            diagnostics.timings = {}
        _merge_export_diagnostics(diagnostics, plan)
        diagnostics.timings["coordinate_transform"] = transform_seconds
        diagnostics.timings["pc2_write"] = write_seconds
        emit(type("CacheEvent", (), {
            "phase": "FINALIZING_CACHE", "message": "Finalizing playback cache",
            "frame_current": None, "frame_total": plan.frame_count,
            "indeterminate": True,
        })())
        step = time.monotonic()
        header = writer.finalize()
        diagnostics.timings["pc2_finalize"] = time.monotonic() - step
        diagnostics.timings["pc2_flush"] = writer.flush_seconds
        diagnostics.timings["pc2_validate"] = writer.validation_seconds
        diagnostics.timings["total"] = time.monotonic() - _run_started_at
        if plan.material_meta:
            identities = dict(partial["identities"])
            solver_identity = dict(identities.get("solver", {}))
            solver_identity.update({
                "mode": diagnostics.solver_mode,
                "package_version": diagnostics.package_version or "unknown",
                "protocol_version": diagnostics.protocol_version or "unknown",
                "schema_version": diagnostics.schema_version or "unknown",
            })
            identities["solver"] = solver_identity
            partial["identities"] = identities
            if getattr(diagnostics, "contact_samples", 0):
                details = dict(partial.get("details", {}))
                details["contacts"] = {
                    "last": diagnostics.contact_last,
                    "peak": diagnostics.contact_peak,
                    "samples": diagnostics.contact_samples}
                partial["details"] = details
            metadata = cache_metadata.completed_metadata(
                partial, cache_path=plan.pc2_path,
                timings=diagnostics.timings)
            cache_metadata.write_atomic(
                cache_metadata.sidecar_path(plan.pc2_path), metadata)
        log_with_context(get_logger("playback.cache"), 20,
                         "streaming PC2 completed", {
            "vertices": header.vertex_count, "frames": header.frame_count,
            "expected_bytes": writer.expected_size,
            "bytes_transferred": getattr(diagnostics, "bytes_transferred", 0),
            "bytes_written": writer.bytes_written,
            "timings": diagnostics.timings,
        })
        _queue.put(("finished", header, diagnostics))
    except SessionCancelled as exc:
        if writer is not None:
            writer.preserve() if exc.resumable else writer.abort()
        _discard_incomplete(plan, state="cancelled",
                            reason="Bake cancelled before publication")
        _queue.put(("cancelled", exc.resumable, exc.recovery_outcome))
    except ClothNextError as exc:
        result = _preserve_failed_partial(writer)
        preserved = (
            ((str(getattr(plan.scene, "cloth_uuid", "")),
              result[0], result[1]),)
            if result is not None else ())
        exc = _with_preserved_partial_error(exc, preserved)
        _discard_incomplete(plan, state="failed", reason=str(exc))
        violations = _convert_solver_violations(plan, exc)
        summary, details = _present_worker_error(
            plan, exc, enriched=violations)
        code = classify_error("SIMULATING", summary, details, exc.record)
        _queue.put(("error", summary,
                    _record_worker_failure(
                        plan, summary, details, code,
                        technical_details=exc.record.technical_message), code,
                    violations))
    except Exception:  # noqa: BLE001 — surfaced as a visible ERROR state
        if writer is not None:
            writer.abort()
        _discard_incomplete(plan, state="failed",
                            reason="Unexpected Bake worker failure")
        summary = "The solver test failed unexpectedly."
        details = traceback.format_exc()
        code = classify_error("SIMULATING", summary, details)
        _queue.put(("error", summary,
                    _record_worker_failure(plan, summary, details, code), code))


def _configure_playback_modifier(modifier, frame_start: int) -> None:
    """Configure the modifier before switching it to the new cache."""
    modifier.cache_format = "PC2"
    modifier.frame_start = float(frame_start)
    modifier.interpolation = "LINEAR"
    modifier.deform_mode = "OVERWRITE"
    modifier.play_mode = "SCENE"
    modifier.forward_axis = "POS_Y"
    modifier.up_axis = "POS_Z"


def _same_modifier(left, right) -> bool:
    """Compare Blender RNA modifiers without relying on wrapper identity."""
    if left is right:
        return True
    try:
        left_pointer = int(left.as_pointer())
        right_pointer = int(right.as_pointer())
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False
    return left_pointer != 0 and left_pointer == right_pointer


def _modifier_index(obj, modifier) -> int:
    """Return the index of an RNA modifier even if Blender rewrapped it."""
    return next((index for index, item in enumerate(obj.modifiers)
                 if _same_modifier(item, modifier)), -1)


def _playback_stack_index(obj, playback_modifier) -> int:
    """Return the slot immediately after every Armature modifier.

    The cache must never precede the rig: doing so lets the Armature deform the
    simulated result a second time. Other modifier types keep their relative
    ordering.
    """
    stack_without_playback = [modifier for modifier in obj.modifiers
                              if not _same_modifier(modifier,
                                                    playback_modifier)]
    armature_indices = [index for index, modifier in enumerate(stack_without_playback)
                        if getattr(modifier, "type", "") == "ARMATURE"]
    return armature_indices[-1] + 1 if armature_indices else 0


_ROD_FCURVE_GROUP = "Cloth NeXt Rod Cache"


def _attach_curve_rod_playback(obj, plan: RunPlan,
                               header: pc2.Pc2Header) -> None:
    """Attach a Curve rod result without converting the artist's Curve.

    Blender's Mesh Cache modifier and shape keys cannot deform Curve
    datablocks. Control points and Bezier handles are therefore keyframed
    directly while preserving Curve bevel/material setup.
    """
    if obj.type != "CURVE":
        raise ValueError(
            "Cable / Rope playback requires the original Curve object")
    if header.vertex_count != len(plan.initial_local):
        raise ValueError(
            "Cable / Rope cache point count no longer matches the Curve")
    animation = getattr(obj.data, "animation_data", None)
    action = getattr(animation, "action", None)
    if action is not None:
        if not bool(action.get("cloth_next_rod_action", False)):
            raise ValueError(
                "Curve has user animation; Cable / Rope playback was not attached")
        obj.data.animation_data_clear()
        bpy.data.actions.remove(action)
    for offset, positions in enumerate(pc2.iter_frames(plan.pc2_path)):
        blender_frame = plan.frame_start + offset
        cursor = 0
        for spline in obj.data.splines:
            points = (spline.bezier_points if spline.type == "BEZIER"
                      else spline.points)
            count = len(points)
            values = positions[cursor:cursor + count]
            cursor += count
            if spline.type == "BEZIER":
                cyclic = bool(spline.use_cyclic_u)
                for index, (point, position) in enumerate(zip(points, values)):
                    previous = values[(index - 1) % count]
                    following = values[(index + 1) % count]
                    if not cyclic and index == 0:
                        tangent = (following - position) / 3.0
                    elif not cyclic and index == count - 1:
                        tangent = (position - previous) / 3.0
                    else:
                        tangent = (following - previous) / 6.0
                    point.handle_left_type = "FREE"
                    point.handle_right_type = "FREE"
                    point.co = tuple(map(float, position))
                    point.handle_left = tuple(map(float, position - tangent))
                    point.handle_right = tuple(map(float, position + tangent))
                    for path in ("co", "handle_left", "handle_right"):
                        point.keyframe_insert(path, frame=blender_frame,
                                              group=_ROD_FCURVE_GROUP)
            else:
                for point, position in zip(points, values):
                    point.co = (*map(float, position), float(point.co[3]))
                    point.keyframe_insert("co", frame=blender_frame,
                                          group=_ROD_FCURVE_GROUP)
        if cursor != header.vertex_count:
            raise ValueError(
                "Curve topology changed before Cable / Rope import")
    action = obj.data.animation_data.action
    action["cloth_next_rod_action"] = True
    previous = ""
    try:
        previous = str(obj.data.get("cloth_next_rod_cache", "") or "")
        obj.data["cloth_next_rod_cache"] = str(plan.pc2_path)
    except TypeError:
        setattr(obj.data, "cloth_next_rod_cache", str(plan.pc2_path))
    settings = getattr(obj, "cloth_next", None)
    if settings is not None and plan.settings_fingerprint:
        settings.baked_settings_fingerprint = plan.settings_fingerprint
        settings.baked_geometry_fingerprint = plan.geometry_fingerprint
        settings.baked_fingerprint_version = BAKE_FINGERPRINT_VERSION
        settings.baked_solver_backend = str(getattr(plan, "backend_id", "PPF"))
        validation_state.store_valid(
            obj, pin_count=0, pin_group="",
            topology_signature=plan.topology_signature,
            geometry_fingerprint=plan.geometry_fingerprint,
            settings_fingerprint=plan.settings_fingerprint)
    if previous and Path(previous) != plan.pc2_path:
        old = Path(previous)
        if (_is_within(old, plan.pc2_path.parent)
                and old.name.startswith("cn_test_cloth_")
                and old.suffix.lower() == ".pc2"):
            for target in (old, old.with_suffix(".meta.json")):
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass


_PLAYBACK_MODIFIER_FIELDS = (
    "name", "filepath", "cache_format", "frame_start", "interpolation",
    "deform_mode", "play_mode", "forward_axis", "up_axis")
_PLAYBACK_OBJECT_FIELDS = (OBJECT_OWNERSHIP_KEY, "cloth_next_cache_path")
_PLAYBACK_SETTINGS_FIELDS = (
    "baked_settings_fingerprint", "baked_geometry_fingerprint",
    "baked_fingerprint_version", "baked_cache_condition",
    "baked_cache_message", "baked_metadata_digest")


def _snapshot_value(owner, name):
    try:
        marker = object()
        value = owner.get(name, marker)
        if value is not marker:
            return True, value
    except (AttributeError, TypeError):
        pass
    return ((True, getattr(owner, name)) if hasattr(owner, name)
            else (False, None))


def _restore_value(owner, name, snapshot) -> None:
    existed, value = snapshot
    if existed:
        try:
            owner[name] = value
            return
        except (AttributeError, TypeError):
            setattr(owner, name, value)
            return
    try:
        del owner[name]
    except (AttributeError, KeyError, TypeError):
        try:
            delattr(owner, name)
        except AttributeError:
            pass


@dataclass(slots=True)
class _PlaybackRecord:
    obj: object
    modifier: object
    created: bool
    original_index: int
    modifier_fields: dict
    extras: tuple
    previous_paths: set
    new_path: Path
    object_fields: dict
    settings: object | None
    settings_fields: dict


def _rollback_playback(records) -> None:
    """Best-effort rollback for a failed multi-object playback commit."""
    for record in reversed(records):
        obj, modifier = record.obj, record.modifier
        try:
            if record.created:
                obj.modifiers.remove(modifier)
            else:
                for name, value in record.modifier_fields.items():
                    setattr(modifier, name, value)
                current_index = _modifier_index(obj, modifier)
                if current_index >= 0 and current_index != record.original_index:
                    obj.modifiers.move(current_index, record.original_index)
            for name, snapshot in record.object_fields.items():
                _restore_value(obj, name, snapshot)
            if record.settings is not None:
                for name, snapshot in record.settings_fields.items():
                    _restore_value(record.settings, name, snapshot)
        except Exception as exc:  # noqa: BLE001 -- retain the original error
            log_with_context(get_logger("playback.cache"), 40,
                "multi-object playback rollback failed", {
                    "object": getattr(obj, "name", ""),
                    "error": f"{type(exc).__name__}: {exc}"})


def _commit_playback_cleanup(records) -> None:
    """Remove stale modifiers/files only after every target is attached."""
    for record in records:
        for extra in record.extras:
            try:
                record.obj.modifiers.remove(extra)
            except Exception as exc:  # noqa: BLE001 -- all new caches are live
                log_with_context(get_logger("playback.cache"), 30,
                    "stale playback modifier cleanup failed", {
                        "object": getattr(record.obj, "name", ""),
                        "error": f"{type(exc).__name__}: {exc}"})
        for old_path in record.previous_paths:
            if (old_path != record.new_path
                    and old_path.name.startswith("cn_test_cloth_")):
                for target in (old_path, cache_metadata.sidecar_path(old_path)):
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass


def _attach_playback(plan: RunPlan, header, *, _transaction=None) -> None:
    targets = _plan_deformables(plan)
    if plan.deformables and len(targets) == 1:
        # The worker deliberately uses the bounded single-cache path for one
        # deformable and therefore returns one Pc2Header, not a UUID mapping.
        # A RunPlan built by the modern scene exporter still carries a
        # one-element ``deformables`` tuple; normalize it before preflight.
        return _attach_playback(
            _plan_for_target(plan, targets[0]), header,
            _transaction=_transaction)
    if plan.deformables:
        # Preflight every cache and object before changing a single modifier.
        for target in plan.deformables:
            expected = header.get(target.uuid) if isinstance(header, dict) else None
            if expected is None:
                raise ValueError("Multi-object playback cache is missing for "
                                 f"{target.object_name}")
            verified = pc2.read_header(target.pc2_path)
            if verified != expected:
                raise ValueError("Multi-object playback cache changed before "
                                 f"attach for {target.object_name}")
            if (verified.vertex_count != len(target.initial_local)
                    or verified.frame_count != plan.frame_count):
                raise ValueError("Multi-object playback cache topology or frame "
                                 f"count mismatch for {target.object_name}")
            if bpy.data.objects.get(target.object_name) is None:
                raise ValueError(
                    f"deformable object {target.object_name!r} no longer exists")
            inspection = cache_metadata.inspect_cache(
                target.pc2_path,
                settings_fingerprint=plan.settings_fingerprint,
                geometry_fingerprint=plan.geometry_fingerprint)
            if not inspection.usable:
                raise ValueError("Multi-object playback cache is invalid for "
                                 f"{target.object_name}: {inspection.message}")
        transaction = []
        try:
            for target in plan.deformables:
                _attach_playback(_plan_for_target(plan, target),
                                 header[target.uuid],
                                 _transaction=transaction)
            _commit_playback_cleanup(transaction)
        except Exception:
            _rollback_playback(transaction)
            raise
        return
    verified = pc2.read_header(plan.pc2_path)
    if verified != header:
        raise ValueError("PC2 file changed between write and attach")
    if verified.vertex_count != len(plan.initial_local):
        raise ValueError("PC2 vertex count does not match the cloth")
    if verified.frame_count != plan.frame_count:
        raise ValueError("PC2 frame count is not the requested range")
    inspection = None
    if plan.material_meta:
        inspection = cache_metadata.inspect_cache(
            plan.pc2_path,
            settings_fingerprint=plan.settings_fingerprint,
            geometry_fingerprint=plan.geometry_fingerprint)
        if not inspection.usable:
            raise ValueError(inspection.message)
    obj = bpy.data.objects.get(plan.cloth_object_name)
    if obj is None:
        raise ValueError(f"cloth object {plan.cloth_object_name!r} no longer "
                         "exists")
    settings = getattr(obj, "cloth_next", None)
    if inspection is not None and settings is not None:
        settings.baked_cache_condition = inspection.condition.value
        settings.baked_cache_message = inspection.message
        settings.baked_metadata_digest = str(
            inspection.metadata.get("metadata_digest", ""))
    if getattr(obj, "type", "") == "CURVE" or plan.deformable_role == "ROD":
        _attach_curve_rod_playback(obj, plan, verified)
        return
    # Modifier ownership is established by the marker itself. The stricter
    # path equality check is for destructive file operations; it cannot be
    # used here because the object stores only the newest cache path. After
    # two bakes that would make every older, still-marked modifier invisible
    # and a fresh modifier would be added on each subsequent bake.
    stale = [mod for mod in obj.modifiers
             if has_cloth_next_playback_marker(obj, mod)]
    previous_paths = {Path(bpy.path.abspath(mod.filepath)) for mod in stale
                      if getattr(mod, "filepath", "")}
    # Reuse the active modifier. Removing and recreating it forces Blender to
    # rebuild the dependency graph and can block the main thread for large
    # production scenes. Configure first and change the filepath last: that
    # single assignment is the handoff from the old valid cache.
    if stale:
        modifier, extras = stale[0], stale[1:]
        created = False
    else:
        modifier = getattr(obj.modifiers, "new")(
            name=import_result.MODIFIER_NAME, type="MESH_CACHE")
        extras = []
        created = True
    original_index = _modifier_index(obj, modifier)
    if original_index < 0:
        raise RuntimeError("Playback modifier disappeared before cache import")
    fields = {name: getattr(modifier, name) for name in
              _PLAYBACK_MODIFIER_FIELDS if hasattr(modifier, name)}
    settings = getattr(obj, "cloth_next", None)
    record = _PlaybackRecord(
        obj, modifier, created, original_index, fields, tuple(extras), previous_paths,
        plan.pc2_path,
        {name: _snapshot_value(obj, name) for name in
         _PLAYBACK_OBJECT_FIELDS},
        settings,
        ({name: _snapshot_value(settings, name) for name in
          _PLAYBACK_SETTINGS_FIELDS} if settings is not None else {}))
    if _transaction is not None:
        _transaction.append(record)
    modifier.name = import_result.MODIFIER_NAME
    _configure_playback_modifier(modifier, plan.frame_start)
    current_index = _modifier_index(obj, modifier)
    if current_index < 0:
        raise RuntimeError("Playback modifier disappeared during cache import")
    target_index = _playback_stack_index(obj, modifier)
    if current_index != target_index:
        obj.modifiers.move(current_index, target_index)
    modifier.filepath = str(plan.pc2_path)
    # Assigning filepath above is the import commit point. Ownership metadata,
    # validation hints, and stale-cache cleanup improve later UX but must not
    # turn a working playback cache into a reported import failure.
    try:
        mark_owned_playback(obj, modifier, str(plan.pc2_path))
        settings = getattr(obj, "cloth_next", None)
        if settings is not None and plan.settings_fingerprint:
            settings.baked_settings_fingerprint = plan.settings_fingerprint
            settings.baked_geometry_fingerprint = plan.geometry_fingerprint
            settings.baked_fingerprint_version = BAKE_FINGERPRINT_VERSION
            settings.baked_solver_backend = str(getattr(plan, "backend_id", "PPF"))
            if inspection is not None:
                settings.baked_cache_condition = inspection.condition.value
                settings.baked_cache_message = inspection.message
                settings.baked_metadata_digest = str(
                    inspection.metadata.get("metadata_digest", ""))
            # The bake just validated this mesh; record it so the Cache panel
            # can honestly say "ready" instead of "needs validation".
            validation_state.store_valid(
                obj,
                pin_count=plan.material_meta.get("details", {}).get(
                    "pinning", {}).get("count", 0),
                pin_group=plan.material_meta.get("details", {}).get(
                    "pinning", {}).get("group", ""),
                topology_signature=plan.topology_signature,
                geometry_fingerprint=plan.geometry_fingerprint,
                settings_fingerprint=plan.settings_fingerprint)
        # Multi-object runs defer destructive cleanup until every target has
        # crossed its filepath commit point, so an attach failure can roll all
        # earlier modifiers back to their previous valid caches.
        if _transaction is None:
            _commit_playback_cleanup((record,))
    except Exception as exc:  # noqa: BLE001 -- playback is already attached
        log_with_context(get_logger("playback.cache"), 30,
                         "playback attached; post-import housekeeping failed", {
            "cache_path": str(plan.pc2_path),
            "error": f"{type(exc).__name__}: {exc}",
        })


def _safe_transition(state: BakeState, **changes) -> None:
    try:
        shared_controller.transition(state, **changes)
    except InvalidTransition:
        pass  # e.g. events arriving after a cancel request


def _show_baked_timeline(plan: RunPlan) -> None:
    """Expose the freshly attached cache immediately in viewport/timeline."""
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    if scene is None:
        return
    try:
        scene.use_preview_range = True
        scene.frame_preview_start = int(plan.frame_start)
        scene.frame_preview_end = int(plan.frame_end)
        scene.frame_set(int(plan.frame_start))
        for window in getattr(bpy.context.window_manager, "windows", ()):
            for area in getattr(window.screen, "areas", ()):
                if area.type in {"VIEW_3D", "TIMELINE", "DOPESHEET_EDITOR"}:
                    area.tag_redraw()
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        pass


def _advance_bake_timeline(plan: RunPlan, blender_frame: int) -> None:
    """Move Blender's blocked UI to the newest solver frame on main thread."""
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    if scene is None:
        return
    frame = min(int(plan.frame_end), max(int(plan.frame_start),
                                        int(blender_frame)))
    try:
        scene.use_preview_range = True
        scene.frame_preview_start = int(plan.frame_start)
        scene.frame_preview_end = frame
        if int(getattr(scene, "frame_current", plan.frame_start)) != frame:
            scene.frame_set(frame)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        pass


def _refresh_recovery_ui(plan: RunPlan) -> None:
    """Verify metadata on the main thread and cache artist-facing UI fields."""
    options = getattr(plan, "recovery_options", None)
    settings = getattr(
        getattr(bpy.context, "scene", None), "cloth_next_recovery", None)
    if options is None or settings is None:
        return
    eligibility = recovery.evaluate_resumable(
        options.metadata_path, options.identity)
    _apply_eligibility_to_settings(settings, eligibility)


class _RecoveryRefreshDeferred(RuntimeError):
    """The file loaded, but its RNA/path state is not ready for inspection."""


_RECOVERY_REFRESH_MAX_ATTEMPTS = 3
_recovery_refresh_generation = 0
_recovery_refresh_attempt = 0
_recovery_log = get_logger("recovery.ui")


def _set_no_recovery(settings, detail="No verified checkpoint belongs to this scene"):
    settings.compatible = False
    settings.resumable = False
    settings.latest_checkpoint_frame = 0
    settings.checkpoint_count = 0
    settings.older_checkpoint_preserved = False
    settings.resume_requested = False
    settings.status = "No Recovery Checkpoint"
    settings.status_detail = detail


def _recovery_discovery_context():
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    if scene is None:
        raise _RecoveryRefreshDeferred("active scene is not available yet")
    settings = getattr(scene, "cloth_next_recovery", None)
    if settings is None:
        raise _RecoveryRefreshDeferred("Recovery scene properties are not available yet")

    expected_uuids = []
    cache_roots = []
    for obj in getattr(scene, "objects", ()):
        props = getattr(obj, "cloth_next", None)
        role = str(getattr(props, "role", "") or "")
        if (props is None or not bool(getattr(props, "enabled", False))
                or role not in {"CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY",
                                "COLLIDER"}):
            continue
        persistent_id = str(
            getattr(props, "persistent_export_id", "") or "").strip()
        if not persistent_id:
            raise _RecoveryRefreshDeferred(
                f"object identity for {getattr(obj, 'name', '?')} is not ready")
        expected_uuids.append(
            export_identity.export_uuid_from_identity(persistent_id, role))
        cache_value = str(getattr(props, "cache_directory", "") or "").strip()
        if cache_value:
            cache_roots.append(Path(bpy.path.abspath(cache_value)).resolve())

    stored_root = str(
        getattr(settings, "recovery_directory", "") or "").strip()
    candidates = []
    stored_metadata = None
    if stored_root:
        stored_metadata = Path(stored_root) / recovery.METADATA_NAME
        candidates.append(stored_metadata)
    for cache_root in dict.fromkeys(cache_roots):
        try:
            candidates.extend(sorted(
                (cache_root / ".cloth_next_recovery").glob(
                    f"*/{recovery.METADATA_NAME}"), key=str))
        except OSError:
            continue
    return (scene, settings, tuple(sorted(expected_uuids)),
            tuple(dict.fromkeys(cache_roots)),
            tuple(dict.fromkeys(Path(path) for path in candidates)),
            stored_metadata)


def _recovery_refresh_diagnostics(*, scene, cache_roots, candidates,
                                  selected=None, metadata_exists=False,
                                  metadata_parsed=False, checkpoint_count=0,
                                  result="") -> dict:
    return {
        "stage": "LOAD_RECOVERY_REFRESH",
        "blend_path": str(getattr(getattr(bpy, "data", None), "filepath", "")),
        "scene": str(getattr(scene, "name", "")),
        "candidate_cache_roots": tuple(map(str, cache_roots)),
        "candidate_metadata_paths": tuple(map(str, candidates)),
        "selected_metadata_path": str(selected or ""),
        "metadata_exists": bool(metadata_exists),
        "metadata_parsed": bool(metadata_parsed),
        "checkpoint_count": int(checkpoint_count),
        "resulting_ui_state": str(result),
    }


def _record_recovery_refresh_failure(settings, exc, diagnostics) -> None:
    settings.compatible = False
    settings.resumable = False
    settings.latest_checkpoint_frame = 0
    settings.checkpoint_count = 0
    settings.older_checkpoint_preserved = False
    settings.resume_requested = False
    settings.status = "Recovery Check Failed"
    settings.status_detail = "Recovery check failed \u00b7 Open diagnostics"
    context = dict(diagnostics)
    context.update(exception_type=type(exc).__name__, message=str(exc),
                   resulting_ui_state=settings.status)
    log_with_context(_recovery_log, logging.ERROR,
                     "Recovery refresh failed", context)


def _current_recovery_diagnostics(scene=None) -> dict:
    """Best-effort context for failures after discovery has partially run."""
    scene = scene or getattr(getattr(bpy, "context", None), "scene", None)
    try:
        (found_scene, _settings, _uuids, cache_roots, candidates,
         _stored) = _recovery_discovery_context()
        return _recovery_refresh_diagnostics(
            scene=found_scene, cache_roots=cache_roots,
            candidates=candidates, result="")
    except Exception:  # noqa: BLE001 -- diagnostics must never mask the cause
        return _recovery_refresh_diagnostics(
            scene=scene, cache_roots=(), candidates=(), result="")


def _refresh_recovery_ui_from_disk() -> dict:
    """Rebuild display state from verified metadata owned by this scene.

    The exact geometry/solver compatibility remains provisional until Bake
    preparation recomputes the full RecoveryIdentity.  Project ownership is
    not provisional: an exact saved directory is preferred and every scanned
    record must match the enabled objects' durable export UUIDs.
    """
    (scene, settings, expected_uuids, cache_roots, candidates,
     stored_metadata) = _recovery_discovery_context()
    base = _recovery_refresh_diagnostics(
        scene=scene, cache_roots=cache_roots, candidates=candidates)
    if not candidates:
        _set_no_recovery(settings)
        base["resulting_ui_state"] = settings.status
        log_with_context(_recovery_log, logging.INFO,
                         "Recovery refresh found no candidates", base)
        return base

    inspected = []
    for metadata in candidates:
        eligibility = recovery.evaluate_resumable(metadata)
        record = recovery.load_project(metadata)
        owned = bool(record is not None and expected_uuids
                     and tuple(sorted(record.identity.export_uuids))
                     == expected_uuids)
        inspected.append((metadata, eligibility, record, owned))

    # The directory stored inside this .blend is authoritative for ownership,
    # but only when its metadata still names precisely the enabled objects.
    selected = next((item for item in inspected
                     if stored_metadata is not None
                     and item[0] == stored_metadata and item[3]), None)
    owned_available = [item for item in inspected
                       if item[3] and item[1].available]
    if selected is None:
        identities = {(item[2].identity.scene_key, item[2].project_id)
                      for item in owned_available if item[2] is not None}
        if len(identities) > 1:
            exc = ValueError(
                "Ambiguous recovery projects belong to the enabled objects")
            diagnostics = dict(base)
            diagnostics["checkpoint_count"] = sum(
                item[1].checkpoint_count for item in owned_available)
            _record_recovery_refresh_failure(settings, exc, diagnostics)
            return diagnostics
        if owned_available:
            selected = max(
                owned_available,
                key=lambda item: (item[2].generation, item[2].updated_at,
                                  str(item[0])))

    if selected is None:
        # A saved exact path that no longer parses or verifies is an explicit
        # invalid-metadata state. Unrelated scan results are never advertised.
        exact = next((item for item in inspected
                      if stored_metadata is not None
                      and item[0] == stored_metadata), None)
        if exact is not None and exact[0].exists():
            settings.compatible = False
            settings.resumable = False
            settings.latest_checkpoint_frame = 0
            settings.checkpoint_count = 0
            settings.older_checkpoint_preserved = False
            settings.resume_requested = False
            settings.status = "Recovery Metadata Invalid"
            settings.status_detail = exact[1].reason
            selected = exact
        else:
            _set_no_recovery(settings)
            base["resulting_ui_state"] = settings.status
            return base

    metadata, eligibility, record, _owned = selected
    settings.recovery_directory = str(metadata.parent)
    if record is not None and eligibility.available:
        _apply_eligibility_to_settings(settings, eligibility)
    elif record is not None and record.state is recovery.ProjectState.ABANDONED:
        settings.compatible = False
        settings.resumable = False
        settings.latest_checkpoint_frame = 0
        settings.checkpoint_count = 0
        settings.older_checkpoint_preserved = False
        settings.status = "Recovery Project Missing"
        settings.status_detail = eligibility.reason
    else:
        settings.compatible = False
        settings.resumable = False
        settings.latest_checkpoint_frame = 0
        settings.checkpoint_count = 0
        settings.older_checkpoint_preserved = False
        settings.status = "Recovery Metadata Invalid"
        settings.status_detail = eligibility.reason
    diagnostics = _recovery_refresh_diagnostics(
        scene=scene, cache_roots=cache_roots, candidates=candidates,
        selected=metadata, metadata_exists=metadata.is_file(),
        metadata_parsed=record is not None,
        checkpoint_count=eligibility.checkpoint_count,
        result=settings.status)
    log_with_context(_recovery_log, logging.INFO,
                     "Recovery refresh completed", diagnostics)
    return diagnostics


def _cancel_delayed_recovery_refresh() -> None:
    timer = getattr(getattr(bpy, "app", None), "timers", None)
    if timer is not None and timer.is_registered(_delayed_recovery_refresh):
        timer.unregister(_delayed_recovery_refresh)


def _delayed_recovery_refresh() -> float | None:
    global _recovery_refresh_attempt
    _recovery_refresh_attempt += 1
    try:
        _refresh_recovery_ui_from_disk()
        return None
    except _RecoveryRefreshDeferred as exc:
        if _recovery_refresh_attempt < _RECOVERY_REFRESH_MAX_ATTEMPTS:
            return 0.25
        scene = getattr(getattr(bpy, "context", None), "scene", None)
        settings = getattr(scene, "cloth_next_recovery", None)
        if settings is not None:
            diagnostics = _recovery_refresh_diagnostics(
                scene=scene, cache_roots=(), candidates=(), result="")
            _record_recovery_refresh_failure(settings, exc, diagnostics)
        return None
    except Exception as exc:  # noqa: BLE001 -- timer must not break Blender
        scene = getattr(getattr(bpy, "context", None), "scene", None)
        settings = getattr(scene, "cloth_next_recovery", None)
        if settings is not None:
            diagnostics = _current_recovery_diagnostics(scene)
            _record_recovery_refresh_failure(settings, exc, diagnostics)
        return None


@persistent
def _on_load_post_refresh_recovery(*_args) -> None:
    """Persistent, non-blocking file-load hook; durable verification is delayed."""
    global _recovery_refresh_generation, _recovery_refresh_attempt
    _recovery_refresh_generation += 1
    _recovery_refresh_attempt = 0
    _cancel_delayed_recovery_refresh()
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    settings = getattr(scene, "cloth_next_recovery", None)
    if settings is not None:
        settings.compatible = False
        settings.resumable = False
        settings.status = "Checking for Recovery"
        settings.status_detail = "Inspecting durable recovery metadata"
    try:
        # Lightweight discovery catches a temporarily unavailable RNA/path
        # state without hashing or opening checkpoint payloads in load_post.
        _recovery_discovery_context()
    except _RecoveryRefreshDeferred:
        pass
    except Exception as exc:  # noqa: BLE001 -- never break Blender file load
        if settings is not None:
            diagnostics = _recovery_refresh_diagnostics(
                scene=scene, cache_roots=(), candidates=(), result="")
            _record_recovery_refresh_failure(settings, exc, diagnostics)
    timer = getattr(getattr(bpy, "app", None), "timers", None)
    if timer is not None and not timer.is_registered(_delayed_recovery_refresh):
        timer.register(_delayed_recovery_refresh, first_interval=0.1)


_on_load_post_refresh_recovery._clothnext_recovery_handler = True


def _purge_stale_recovery_handlers(container) -> None:
    """Remove callbacks left behind by a previous module instance (reload)."""
    live = {_on_load_post_refresh_recovery}
    for func in list(container):
        if (getattr(func, "_clothnext_recovery_handler", False)
                and func not in live):
            container.remove(func)


_RECOVERY_HANDLER_SLOTS = (("load_post", "_on_load_post_refresh_recovery"),)

_recovery_ui_handler_registered = False


def install_recovery_ui_handler() -> None:
    """Attach the recovery snapshot refresh exactly once per module instance."""
    global _recovery_ui_handler_registered
    if _recovery_ui_handler_registered:
        return
    handlers = getattr(bpy.app, "handlers", None)
    if handlers is None:  # pragma: no cover - defensive
        return
    for slot, attribute in _RECOVERY_HANDLER_SLOTS:
        container = getattr(handlers, slot, None)
        if container is None:
            continue
        _purge_stale_recovery_handlers(container)
        callback = globals()[attribute]
        if callback not in container:
            container.append(callback)
    _recovery_ui_handler_registered = True


def uninstall_recovery_ui_handler() -> None:
    global _recovery_ui_handler_registered
    _cancel_delayed_recovery_refresh()
    handlers = getattr(bpy.app, "handlers", None)
    if handlers is not None:
        for slot, attribute in _RECOVERY_HANDLER_SLOTS:
            container = getattr(handlers, slot, None)
            if container is None:
                continue
            callback = globals()[attribute]
            while callback in container:
                container.remove(callback)
            _purge_stale_recovery_handlers(container)
    _recovery_ui_handler_registered = False


def _pump_once() -> float | None:
    global _worker, _active_plan, _ram_auto_cancel_triggered
    global _intersection_violations, _intersection_violation_index
    plan = _active_plan
    if plan is None:
        return None
    cancellable_ram_states = {
        BakeState.EXPORTING, BakeState.STARTING_SOLVER, BakeState.UPLOADING,
        BakeState.BUILDING, BakeState.SIMULATING, BakeState.FETCHING}
    if (_ram_auto_cancel_enabled and _worker is not None
            and _worker.is_alive()
            and shared_controller.snapshot().state in cancellable_ram_states
            and _ram_auto_cancel.observe(shared_telemetry.snapshot())):
        _ram_auto_cancel_triggered = True
        _cancel_event.set()
        snapshot = shared_controller.snapshot()
        if snapshot.active and snapshot.state is not BakeState.CANCELLING:
            shared_controller.request_cancel()
            shared_controller.update(
                status_message="Auto-cancelling: system RAM safety limit reached")
    import time as _time
    drained = 0
    while drained < 64:
        try:
            message = _queue.get_nowait()
        except queue.Empty:
            break
        drained += 1
        kind = message[0]
        if kind == "event":
            event = message[1]
            if event.phase == "RUNTIME_METADATA":
                shared_telemetry.set_solver_pid(event.process_id)
                shared_controller.update(solver_mode=event.solver_mode,
                    solver_version=event.package_version or "",
                    solver_process_id=event.process_id)
                continue
            if event.phase == "RECOVERY_SAVED":
                # Periodic solver checkpoints are verified on the worker
                # thread. Reflect their durable metadata on Blender's main
                # thread immediately instead of leaving the panel stale until
                # Cancel or terminal cleanup.
                _refresh_recovery_ui(plan)
                shared_controller.update(
                    status_message=event.message,
                    activity_code=BakeActivity.RECOVERY)
                continue
            if event.phase == "RECOVERY_WARNING":
                shared_controller.update(
                    status_message=event.message,
                    activity_code=BakeActivity.RECOVERY)
                continue
            if event.phase == "CANCELLING":
                _safe_transition(
                    BakeState.CANCELLING,
                    status_message="Saving recovery checkpoint",
                    activity_code=BakeActivity.RECOVERY,
                    activity_label="Saving recovery checkpoint")
                continue
            state = _EVENT_STATE.get(event.phase)
            if event.phase == "TRANSFORMING_FRAME":
                state = BakeState.FETCHING
            elif event.phase == "FINALIZING_CACHE":
                state = BakeState.IMPORTING
            if state is not None:
                current, total = event.frame_current, event.frame_total
                if event.phase in {"SIMULATING", "FETCHING", "TRANSFORMING_FRAME"} and current is not None:
                    solver_step = min(plan.frame_count - 1, int(current))
                    current = plan.frame_start + solver_step
                    total = plan.frame_count
                    if event.phase == "TRANSFORMING_FRAME":
                        _advance_bake_timeline(plan, current)
                activity_code = None
                if getattr(event, "activity_code", ""):
                    try:
                        activity_code = BakeActivity(event.activity_code)
                    except ValueError:
                        activity_code = BakeActivity.UNKNOWN
                activity_changes = ({"activity_code": activity_code,
                                     "activity_label": event.message}
                                    if activity_code is not None else
                                    {"activity_label": ""})
                _safe_transition(
                    state, status_message=event.message,
                    current_frame=current,
                    progress_current=(current - plan.frame_start + 1
                                      if current is not None else 0),
                    progress_total=(None if event.indeterminate
                                    else total), **activity_changes)
        elif kind == "finished":
            header, diagnostics = message[1], message[2]
            try:
                _safe_transition(BakeState.IMPORTING,
                                 status_message="Creating Blender playback cache")
                attach_step = _time.monotonic()
                _attach_playback(plan, header)
                _show_baked_timeline(plan)
                diagnostics.timings["modifier_attach"] = (
                    _time.monotonic() - attach_step)
                shared_controller.transition(
                    BakeState.FINISHED,
                    status_message=(f"Finished — {plan.frame_count} frames "
                        f"cached for {len(_plan_deformables(plan))} object(s)"),
                    progress_current=plan.frame_count,
                    progress_total=plan.frame_count,
                    current_frame=plan.frame_end,
                    frame_start=plan.frame_start,
                    frame_end=plan.frame_end,
                    estimated_remaining_seconds=None)
            except (ValueError, RuntimeError, InvalidTransition) as exc:
                details = str(exc)
                shared_controller.fail("Importing the solver result failed.",
                                       details,
                                       error_code=classify_error(
                                           "IMPORTING",
                                           "Importing the solver result failed.",
                                           details))
            _worker, _active_plan = None, None
            _refresh_recovery_ui(plan)
            modal_lock.release()
            shared_telemetry.set_solver_pid(None)
            return None
        elif kind == "cancelled":
            resumable = message[1] if len(message) > 1 else False
            recovery_outcome = message[2] if len(message) > 2 else None
            if _ram_auto_cancel_triggered:
                shared_controller.fail(
                    "Bake stopped at the RAM safety limit.",
                    "Cause: System RAM remained above the configured Auto "
                    "Cancel threshold.\nWhat to do: Lower scene complexity "
                    "or raise the threshold cautiously.",
                    error_code="CNX-E166")
                _ram_auto_cancel_triggered = False
            else:
                if (recovery_outcome is not None and
                        recovery_outcome.kind is RecoveryOutcomeKind.SAVED):
                    status_msg = "Bake cancelled · Recovery checkpoint saved"
                elif (recovery_outcome is not None and recovery_outcome.kind
                      is RecoveryOutcomeKind.EXISTING_PRESERVED):
                    status_msg = (
                        "Bake cancelled · Existing recovery checkpoint preserved")
                elif (recovery_outcome is not None and recovery_outcome.kind
                      is RecoveryOutcomeKind.NOT_ENABLED):
                    status_msg = "Bake cancelled"
                elif (recovery_outcome is not None and recovery_outcome.kind
                      is RecoveryOutcomeKind.NOT_AVAILABLE_YET):
                    status_msg = (
                        "Bake cancelled before a recovery checkpoint was available")
                elif recovery_outcome is not None:
                    status_msg = (
                        "Bake cancelled · Recovery checkpoint could not be saved")
                else:
                    status_msg = "Solver test cancelled"
                _safe_transition(BakeState.CANCELLED,
                                 status_message=status_msg,
                                 estimated_remaining_seconds=None)
            if not resumable:
                _discard_incomplete(plan)
            _refresh_recovery_ui(plan)
            modal_lock.release()
            _worker, _active_plan = None, None
            shared_telemetry.set_solver_pid(None)
            return None
        elif kind == "error":
            code = message[3] if len(message) > 3 else ""
            _intersection_violations = (
                tuple(message[4]) if len(message) > 4 else ())
            _intersection_violation_index = 0
            if _intersection_violations:
                from . import intersection_overlay
                intersection_overlay.set_violations(
                    _intersection_violations,
                    plan.solver_input if plan is not None else None)
            shared_controller.fail(message[1], message[2], error_code=code)
            _discard_incomplete(plan)
            _refresh_recovery_ui(plan)
            modal_lock.release()
            _worker, _active_plan = None, None
            shared_telemetry.set_solver_pid(None)
            return None
    if _worker is not None and not _worker.is_alive() and _queue.empty():
        # The worker died without posting a terminal message.
        shared_controller.fail("The solver test worker stopped unexpectedly.",
                               "no terminal message from the worker thread")
        _discard_incomplete(plan)
        modal_lock.release()
        _worker, _active_plan = None, None
        return None
    now = _time.monotonic()
    snapshot = shared_controller.snapshot()
    eta = (_eta_estimator.observe(
        snapshot.current_frame, snapshot.frame_end, now)
        if snapshot.state in {BakeState.SIMULATING, BakeState.FETCHING}
        else None)
    shared_controller.update(
        elapsed_seconds=now - _run_started_at,
        estimated_remaining_seconds=eta)
    return 0.2


def _abort_failed_pump(details: str) -> None:
    """Turn a Blender timer exception into a terminal, visible failure."""
    global _worker, _active_plan
    try:
        shared_controller.fail("Importing the solver result failed.", details)
    except Exception:
        pass
    modal_lock.release()
    shared_telemetry.set_solver_pid(None)
    _worker, _active_plan = None, None


def _pump() -> float | None:
    """Keep timer exceptions from leaving every UI stuck on IMPORTING."""
    try:
        return _pump_once()
    except Exception:  # noqa: BLE001 -- preserve the real Blender traceback
        _abort_failed_pump(traceback.format_exc())
        return None


def _pump_watchdog() -> float | None:
    """Restore the result pump if Blender unexpectedly unregistered it."""
    if _active_plan is None:
        return None
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.0)
    return 0.5


def run_active() -> bool:
    return _worker is not None and _worker.is_alive()


def _start_prepared_run(plan: RunPlan) -> None:
    """Create runtime files and worker only after startup prerequisites."""
    global _worker, _active_plan, _run_started_at, _last_work_directory
    global _unsubscribe, _ram_auto_cancel_triggered
    import time as _time
    for target in _plan_deformables(plan):
        target.pc2_path.parent.mkdir(parents=True, exist_ok=True)
    plan.work_directory.mkdir(parents=True, exist_ok=True)
    _last_work_directory = plan.work_directory
    shared_controller.transition(
        BakeState.EXPORTING,
        status_message=(f"Exporting {len(_plan_deformables(plan))} "
                        "deformable object(s)"),
        active_object_name=plan.cloth_object_name,
        frame_start=plan.frame_start, frame_end=plan.frame_end,
        current_frame=plan.frame_start, progress_current=1,
        progress_total=plan.frame_count)
    _cancel_event.clear()
    _ram_auto_cancel_triggered = False
    while not _queue.empty():
        try: _queue.get_nowait()
        except queue.Empty: break
    _active_plan = plan
    _run_started_at = _time.monotonic()
    _eta_estimator.reset()
    if _unsubscribe is None:
        _unsubscribe = shared_controller.subscribe(_on_controller_snapshot)
    _worker = threading.Thread(target=_worker_main, args=(plan,),
                               name="clothnext-bake-worker", daemon=True)
    try:
        _worker.start()
    except Exception as exc:
        _worker = None; _active_plan = None; modal_lock.release()
        shared_controller.fail("The Bake worker could not be started.", str(exc))
        raise SceneValidationError(
            "The Bake worker could not be started; no solver process was launched.") from exc
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=.1)
    if not bpy.app.timers.is_registered(_pump_watchdog):
        bpy.app.timers.register(_pump_watchdog, first_interval=.5)


def _begin_controller(job_kind: BakeJobKind) -> str:
    if shared_controller.snapshot().state is not BakeState.IDLE:
        shared_controller.reset()
    return shared_controller.transition(
        BakeState.PREPARING, preview=False, job_kind=job_kind,
        status_message="Validating Blender scene", frame_start=None,
        frame_end=None).job_id


def start_run(context, *, job_kind: BakeJobKind = BakeJobKind.SOLVER_TEST) -> str:
    """Immediate developer diagnostic; production has a readiness gate."""
    global _ram_auto_cancel_enabled
    _clear_intersection_diagnostics()
    _ram_auto_cancel_enabled = False
    _begin_controller(job_kind)
    try:
        plan = build_run_plan(context)
        shared_controller.transition(BakeState.STARTING_RUN,
                                     status_message="Starting diagnostic run")
        prepare_cache_for_new_run(plan)
        _start_prepared_run(plan)
    except (SceneValidationError, ClothNextError) as exc:
        message = exc.record.user_message if isinstance(exc, ClothNextError) else str(exc)
        shared_controller.fail(message)
        raise
    return ""


def _continue_production_bake(context,job_id,plan) -> tuple[str,bool]:
    global _pending_plan,_pending_job_id,_ram_auto_cancel_enabled
    owner = shared_controller.snapshot()
    if owner.job_id != job_id or owner.state is not BakeState.PREPARING:
        raise SessionCancelled()
    try:
        prefs = addon_preferences(context, __package__)
        auto_launch=bool(prefs.auto_launch_bake_window)
        shared_telemetry.configure(prefs.telemetry_refresh_seconds)
        _ram_auto_cancel_enabled=bool(getattr(prefs,"auto_cancel_high_ram",True))
        _ram_auto_cancel.configure(
            getattr(prefs,"auto_cancel_ram_percent",90),2)
    except (KeyError,AttributeError):
        auto_launch=True; _ram_auto_cancel_enabled=True
        _ram_auto_cancel.configure(90,2)
    _pending_plan=plan; _pending_job_id=job_id
    if not auto_launch:
        shared_controller.transition(BakeState.STARTING_RUN,status_message="Starting Bake in Blender")
        try:prepare_cache_for_new_run(plan); _start_prepared_run(plan)
        finally:_pending_plan=None; _pending_job_id=""
        return job_id,False
    shared_controller.transition(BakeState.STARTING_COMPANION,status_message="Starting Bake window")
    request=EnterBakeMode(job_id=job_id,blender_process_id=os.getpid(),
        frame_start=plan.frame_start,frame_end=plan.frame_end,preset_label=plan.preset_identifier)
    ok,message=companion_manager.begin_bake_mode(request)
    if not ok:
        _pending_plan=None; _pending_job_id=""; shared_controller.fail(message)
        raise SceneValidationError(message)
    shared_controller.transition(BakeState.WAITING_FOR_COMPANION,
        status_message="Opening Bake window…",frame_start=plan.frame_start,frame_end=plan.frame_end)
    if not bpy.app.timers.is_registered(_startup_pump):bpy.app.timers.register(_startup_pump,first_interval=.05)
    return job_id,True

def _require_cache_directories(deformables) -> None:
    """Production bakes must write to a user-chosen folder.

    Without one, the cache falls back to Blender's temporary directory and is
    wiped on the next launch. The developer "Run Real Solver Test" path keeps
    that fallback on purpose; this requirement is enforced only for the artist
    Bake so a finished result is never silently lost on restart.
    """
    missing = [str(getattr(obj, "name", "?")) for obj in deformables
               if not str(getattr(getattr(obj, "cloth_next", None),
                                  "cache_directory", "") or "").strip()]
    if missing:
        names = ", ".join(missing)
        raise SceneValidationError(
            f"Set a Cache Directory for {names} before baking. Use the folder "
            "button next to Bake — otherwise the result is written to a "
            "temporary folder and lost when Blender restarts.")


def begin_production_bake(context) -> tuple[str, bool]:
    """Validate and reserve production Bake without worker or modal lock."""
    global _pending_plan, _pending_job_id, _pin_capture
    global _ram_auto_cancel_triggered
    if run_active() or _pending_plan is not None or _pin_capture is not None:
        raise SceneValidationError("A Cloth NeXt bake is already active.")
    _clear_intersection_diagnostics()
    # Cancellation belongs to one Bake attempt. It may still be set after a
    # previous Cancel or add-on shutdown, while animated Collider capture runs
    # before _start_prepared_run() gets a chance to clear it.
    _cancel_event.clear()
    _ram_auto_cancel_triggered = False
    job_id = _begin_controller(BakeJobKind.BAKE)
    if not modal_lock.reserve(job_id):
        shared_controller.fail(
            "Another Cloth NeXt Bake generation already owns startup.",
            "Close the older Bake window or restart Blender, then retry.")
        raise SceneValidationError(
            "Another Cloth NeXt Bake generation already owns startup.")
    try:
        # One authoritative validation for the whole Bake start: it hashes the
        # topology once and scans the pin group once. Everything downstream
        # (pin capture, run plan, fingerprints, cache check) reuses it.
        objects=tuple(getattr(getattr(context,"scene",None),"objects",()))
        snapshot=validate_scene(context) if objects else None
        if snapshot is not None:
            _require_cache_directories(
                tuple(entry.obj for entry in snapshot.deformables))
            bake_range=snapshot.bake_range
            source_key, _source_reason = _scene_source_key(
                context, snapshot)
            if _verified_early_scene_available(snapshot, source_key):
                plan = build_run_plan(context, snapshot=snapshot)
                return _continue_production_bake(context, job_id, plan)
            animated_targets = tuple(
                (entry.obj.name, entry.pin_membership)
                for entry in (snapshot.deformables or ())
                if (entry.pin_membership.enabled and (
                    str(getattr(entry.obj.cloth_next, "pin_mode", "STATIC")) ==
                    "FOLLOW_ANIMATION" or bool(getattr(
                        entry.obj.cloth_next,
                        "advanced_pin_motion_enabled", False)) or bool(getattr(
                            entry.obj.cloth_next,
                            "advanced_pin_targets", ())) or bool(getattr(
                            entry.obj.cloth_next,
                            "soft_constraints", ())))))
            animated_colliders = tuple(
                obj for obj in snapshot.collider_objs
                if
                str(getattr(obj.cloth_next, "collider_motion", "STATIC")) ==
                "ANIMATED")
            force_capture = _capture_force_without_timeline(
                context, bake_range)
            needs_timeline = bool(
                animated_targets or animated_colliders
                or force_capture is None)
            if needs_timeline:
                try:
                    prefs = addon_preferences(context, __package__)
                    open_preparation_window = bool(
                        prefs.auto_launch_bake_window)
                except (KeyError, AttributeError):
                    open_preparation_window = True
                if open_preparation_window:
                    ok, message = companion_manager.ensure_running()
                    if not ok:
                        raise SceneValidationError(message)
            if needs_timeline:
                collider_rates = tuple(int(getattr(
                    obj.cloth_next, "collider_samples_per_frame",
                    COLLIDER_SAMPLES_PER_FRAME))
                    for obj in animated_colliders)
                pin_rates = (
                    collider_rates or (COLLIDER_SAMPLES_PER_FRAME,))
                points = build_sample_plan(
                    bake_range.start, bake_range.end,
                    collider_samples=(
                        pin_rates if animated_targets else collider_rates),
                    include_integer_frames=bool(
                        animated_targets or force_capture is None))
                capture={"context":context,"targets":animated_targets,
                    "range":bake_range,"points":points,"point_index":0,
                    "samples":{name: [] for name, _membership in animated_targets},
                    "force_samples":[], "active_scalar_types":set(),
                    "force_capture":force_capture,
                    "collider_states":_begin_collider_pump(
                        animated_colliders, bake_range, _scene_fps(context)),
                    "index_arrays":{
                        name: np.asarray(membership.vertex_indices,
                                         dtype=np.intp)
                        for name, membership in animated_targets},
                    "advanced_offsets": {
                        name: _advanced_pin_offsets(
                            bpy.data.objects.get(name), membership)
                        for name, membership in animated_targets
                        if bpy.data.objects.get(name) is not None},
                    "pin_target_initial": {},
                    "pin_base_positions": {},
                    "original":int(context.scene.frame_current),
                    "original_subframe":float(getattr(
                        context.scene, "frame_subframe", 0.0)),
                    "capture_started":time.perf_counter(),
                    "job_id":job_id,
                    "snapshot":snapshot,
                    "wait_for_companion":open_preparation_window,
                    "companion_deadline":time.monotonic() +
                        companion_manager.STARTUP_TIMEOUT_SECONDS}
                _suspend_pin_capture_playback(capture)
                _pin_capture=capture
                _pending_job_id=job_id
                activity = (BakeActivity.CAPTURING_PIN_TARGETS
                            if animated_targets else
                            BakeActivity.CAPTURING_COLLIDER_MOTION)
                message = ("Opening Bake window before animated Pin capture"
                           if animated_targets else
                           "Opening Bake window before animated Collider capture")
                shared_controller.update(status_message=message,
                    activity_code=activity,
                    progress_current=0,progress_total=len(points))
                # Re-arm the preparation timer for every Bake attempt. Blender
                # can retain a registered callback after an interrupted add-on
                # reload/cancel even though it is no longer scheduled to run;
                # the old guard then leaves the Bake window at 0 forever.
                if bpy.app.timers.is_registered(_pin_capture_pump):
                    bpy.app.timers.unregister(_pin_capture_pump)
                bpy.app.timers.register(_pin_capture_pump, first_interval=.05)
                return job_id,True
        plan=build_run_plan(context,snapshot=snapshot)
    except (SceneValidationError, ClothNextError) as exc:
        modal_lock.release(job_id)
        message = exc.record.user_message if isinstance(exc, ClothNextError) else str(exc)
        shared_controller.fail(message)
        companion_manager.persist_bake_error(shared_controller.snapshot())
        raise
    except Exception as exc:  # noqa: BLE001 -- Blender API failures stay visible
        modal_lock.release(job_id)
        details = traceback.format_exc()
        summary = "Preparing the Bake failed."
        code = classify_error("PREPARING", summary, details)
        shared_controller.fail(summary, details, error_code=code)
        companion_manager.persist_bake_error(shared_controller.snapshot())
        raise SceneValidationError(
            f"{summary} Error code: {code}. Check the Bake logs.") from exc
    return _continue_production_bake(context,job_id,plan)


def _suspend_pin_capture_playback(state) -> None:
    """Expose the same modifier stage used by deformable export.

    Animated Pin targets and the exported Bake-start mesh must address the
    same vertices.  Export stops after the last enabled Armature; leaving a
    later Mirror, Solidify, Subdivision, or playback modifier enabled here
    makes the evaluated Pin mesh larger and produces a false E105 topology
    failure.  Disable that downstream suffix once for the whole timeline
    capture and restore it afterwards.
    """
    saved = []
    try:
        for object_name, _membership in state["targets"]:
            obj = bpy.data.objects.get(object_name)
            if obj is None:
                raise SceneValidationError(
                    f"The Cloth object {object_name!r} no longer exists.")
            modifiers = tuple(getattr(obj, "modifiers", ()))
            armatures = [
                index for index, modifier in enumerate(modifiers)
                if (getattr(modifier, "type", "") == "ARMATURE"
                    and bool(getattr(modifier, "show_viewport", True)))]
            cutoff = armatures[-1] if armatures else -1
            for index, modifier in enumerate(modifiers):
                downstream = cutoff >= 0 and index > cutoff
                if (not downstream
                        and not is_cloth_next_playback_modifier(obj, modifier)):
                    continue
                if not bool(getattr(modifier, "show_viewport", True)):
                    continue
                saved.append((modifier,
                              bool(getattr(modifier, "show_viewport", True)),
                              bool(getattr(modifier, "show_render", True))))
                modifier.show_viewport = False
                modifier.show_render = False
        state["playback_states"] = saved
        if saved:
            _depsgraph_update(state["context"])
    except Exception:
        for modifier, viewport, render in reversed(saved):
            modifier.show_viewport = viewport
            modifier.show_render = render
        raise


def _restore_pin_capture_state(state) -> None:
    """Idempotently restore playback flags and the artist's original frame."""
    for modifier, viewport, render in reversed(
            state.pop("playback_states", ())):
        try:
            modifier.show_viewport = viewport
            modifier.show_render = render
        except (ReferenceError, AttributeError):
            pass
    context = state["context"]
    context.scene.frame_set(
        state["original"],
        subframe=float(state.get("original_subframe", 0.0)))


def _sample_evaluated_pin_positions(context, obj, membership, *,
                                    depsgraph=None, index_array=None):
    """Read one evaluated mesh in bulk without allocating a to_mesh copy."""
    if depsgraph is None:
        depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    has_armature = any(
        getattr(modifier, "type", "") == "ARMATURE"
        and bool(getattr(modifier, "show_viewport", True))
        for modifier in getattr(obj, "modifiers", ()))
    # Deformable export reads the untouched source mesh when no Armature is
    # active.  Do the same for Pins while retaining the evaluated object
    # transform (parents/object animation may still move the Pin target).
    mesh = evaluated.data if has_armature else obj.data
    count = len(mesh.vertices)
    if count != membership.source_vertex_count:
        raise SceneValidationError(
            f"Animated Pinning changed {obj.name} topology: "
            f"{membership.source_vertex_count} source vertices and "
            f"{count} evaluated vertices.")
    coordinates = np.empty(count * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coordinates)
    if index_array is None:
        index_array = np.asarray(membership.vertex_indices, dtype=np.intp)
    selected = coordinates.reshape((-1, 3))[index_array]
    matrix = np.asarray(solver_world_matrix(
        tuple(tuple(row) for row in evaluated.matrix_world)),
        dtype=np.float64)
    positions = selected @ matrix[:3, :3].T + matrix[:3, 3]
    return tuple(tuple(float(value) for value in row) for row in positions)


def _pin_capture_pump():
    global _pin_capture,_pending_job_id
    state=_pin_capture
    if state is None:return None
    context=state["context"]; scene=context.scene
    # Timers from an older Bake attempt can survive an error, cancellation or
    # extension reload.  They must never publish into a newer backend's shared
    # controller or open its Companion under their stale job id.
    state_job = state.get("job_id", "")
    owner = shared_controller.snapshot()
    if state_job and (owner.job_id != state_job
                      or owner.state is not BakeState.PREPARING):
        try:
            _cleanup_collider_pump(state.get("collider_states", {}))
        except Exception:
            pass
        try:
            _restore_pin_capture_state(state)
        except Exception:
            pass
        stale_job = state_job
        _pin_capture = None
        if _pending_job_id == stale_job:
            _pending_job_id = ""
        return None
    try:
        if state.get("wait_for_companion"):
            status, message = companion_manager.preparation_status()
            if status == "READY":
                state["wait_for_companion"] = False
            elif (status == "ERROR" or time.monotonic() >=
                  state["companion_deadline"]):
                raise SceneValidationError(
                    message if status == "ERROR" else
                    "The Bake window did not become ready before capture.")
            else:
                shared_controller.update(status_message=message)
                return .05
        if _cancel_event.is_set():
            raise SessionCancelled()
        point_index = state["point_index"]
        point = state["points"][point_index]
        frame = point.frame
        has_colliders = bool(state["collider_states"])
        has_pins = bool(state["targets"])
        if has_colliders and has_pins:
            capture_label = "Capturing animated Pins and Colliders"
        elif has_colliders:
            capture_label = "Exporting animated Collider"
        else:
            capture_label = "Capturing animated Pin targets"
        # Publish the exact sub-frame sample before Blender evaluates it.
        # Expensive modifiers can otherwise leave the previous whole-frame
        # label visible long enough to make 8 samples/frame look stalled.
        shared_controller.update(
            status_message=(f"{capture_label} - sample {point_index + 1} / "
                            f"{len(state['points'])} - frame "
                            f"{float(point.position):g}"),
            activity_code=(BakeActivity.CAPTURING_COLLIDER_MOTION
                           if has_colliders
                           else BakeActivity.CAPTURING_PIN_TARGETS),
            progress_current=point_index,
            progress_total=len(state["points"]))
        scene.frame_set(frame, subframe=point.subframe)
        depsgraph = context.evaluated_depsgraph_get()
        timings = state["snapshot"].timings
        timings["frame_set_count"] = timings.get(
            "frame_set_count", 0.0) + 1.0
        timings["depsgraph_evaluation_count"] = timings.get(
            "depsgraph_evaluation_count", 0.0) + 1.0
        if state["targets"]:
            for object_name, membership in state["targets"]:
                obj=bpy.data.objects.get(object_name)
                if obj is None:
                    raise SceneValidationError(
                        f"The Cloth object {object_name!r} no longer exists.")
                positions = _sample_evaluated_pin_positions(
                    context, obj, membership, depsgraph=depsgraph,
                    index_array=state["index_arrays"][object_name])
                object_settings = getattr(obj, "cloth_next", None)
                advanced_rows = state.get("advanced_offsets", {}).get(
                    object_name, ())
                if advanced_rows:
                    base = state["pin_base_positions"].setdefault(
                        object_name, positions)
                    positions = _advanced_pin_positions(
                        base, advanced_rows, depsgraph,
                        state["pin_target_initial"], object_name)
                elif bool(getattr(
                        object_settings, "advanced_pin_motion_enabled", False)):
                    target = getattr(object_settings, "pin_target", None)
                    if target is None:
                        raise SceneValidationError(
                            f"{obj.name}: Select a Target for Target Object Pinning.")
                    if target is obj:
                        raise SceneValidationError(
                            f"{obj.name}: The Cloth object cannot be its own Pin Target.")
                    target_eval = target.evaluated_get(depsgraph)
                    target_matrix = np.asarray(solver_world_matrix(
                        tuple(tuple(row) for row in target_eval.matrix_world)),
                        dtype=np.float64)
                    if object_name not in state["pin_target_initial"]:
                        state["pin_target_initial"][object_name] = target_matrix
                        state["pin_base_positions"][object_name] = positions
                    positions = _transform_pin_positions(
                        state["pin_base_positions"][object_name],
                        state["pin_target_initial"][object_name],
                        target_matrix)
                constraints = tuple(getattr(
                    object_settings, "soft_constraints", ()))
                if constraints:
                    base = state["pin_base_positions"].setdefault(
                        object_name, positions)
                    positions = _soft_constraint_positions(
                        base, constraints, depsgraph,
                        state["pin_target_initial"], object_name)
                state["samples"][object_name].append(
                    AnimatedPinTargetSample(
                        float(point.position), positions))
                timings["pin_sample_count"] = timings.get(
                    "pin_sample_count", 0.0) + 1.0
                timings["evaluated_get_count"] = timings.get(
                    "evaluated_get_count", 0.0) + 1.0
        if point.position.denominator == 1:
            if state["force_capture"] is None:
                force_state, active_scalar_types = _force_state(
                    context, wind_frame=frame)
                state["force_samples"].append(force_state)
                state["active_scalar_types"].update(active_scalar_types)
                timings["force_sample_count"] = timings.get(
                    "force_sample_count", 0.0) + 1.0
        collider_count, evaluated_count, mesh_count = (
            _pump_collider_point(
                depsgraph, point, state["collider_states"]))
        timings["collider_sample_count"] = timings.get(
            "collider_sample_count", 0.0) + collider_count
        timings["evaluated_get_count"] = timings.get(
            "evaluated_get_count", 0.0) + evaluated_count
        timings["to_mesh_count"] = timings.get(
            "to_mesh_count", 0.0) + mesh_count
        shared_controller.update(
            status_message=(f"{capture_label} - sample {point_index + 1} / "
                            f"{len(state['points'])} - frame "
                            f"{float(point.position):g}"),
            activity_code=(BakeActivity.CAPTURING_COLLIDER_MOTION
                           if state["collider_states"]
                           else BakeActivity.CAPTURING_PIN_TARGETS),
            progress_current=point_index + 1,
            progress_total=len(state["points"]))
        if point_index + 1 < len(state["points"]):
            state["point_index"] = point_index + 1
            return .005
        job_id=state["job_id"]
        sample_map={name:tuple(samples)
                    for name,samples in state["samples"].items()}
        snapshot=state.get("snapshot")
        samples=(None if not sample_map else
                 sample_map if snapshot is not None
                 and len(snapshot.deformables)>1
                 else next(iter(sample_map.values())))
        force_capture = state["force_capture"]
        if force_capture is None:
            force_capture = _force_capture_from_samples(
                state["force_samples"], state["active_scalar_types"],
                state["range"], _scene_fps(context))
        collider_captures = _finish_collider_pump(
            state["collider_states"])
        timings["capture_seconds"] = (
            timings.get("capture_seconds", 0.0)
            + time.perf_counter() - state["capture_started"])
        shared_controller.update(
            status_message="Preparing evaluated geometry",
            activity_code=BakeActivity.CAPTURING_GEOMETRY,
            progress_current=0,
            progress_total=1)
        # Cancel may arrive after the check at the beginning of this timer
        # tick. Do not continue into STARTING_COMPANION from CANCELLING after
        # an expensive final evaluated-frame capture.
        if _cancel_event.is_set():
            raise SessionCancelled()
        owner = shared_controller.snapshot()
        if state_job and (owner.job_id != state_job
                          or owner.state is not BakeState.PREPARING):
            # Ownership may change while Blender evaluates an expensive frame.
            # Let the next tick take the non-destructive stale cleanup path.
            return .0
        _restore_pin_capture_state(state)
        _pin_capture=None
        # Reuses the Bake's single validation; no second topology hash or pin scan.
        plan=build_run_plan(context,animated_pin_samples=samples,
                            force_capture=force_capture,
                            collider_captures=collider_captures,
                            snapshot=state.get("snapshot"))
        _continue_production_bake(context,job_id,plan); return None
    except SessionCancelled:
        try:
            _cleanup_collider_pump(state.get("collider_states", {}))
        except Exception:
            pass
        try:
            _restore_pin_capture_state(state)
        except Exception:
            pass
        _pin_capture=None; _pending_job_id=""
        if shared_controller.snapshot().state is not BakeState.CANCELLING:
            shared_controller.request_cancel()
        shared_controller.transition(
            BakeState.CANCELLED,
            status_message=(
                "Bake cancelled before a recovery checkpoint was available"))
        return None
    except Exception as exc:
        try:
            _cleanup_collider_pump(state.get("collider_states", {}))
        except Exception:
            pass
        try:_restore_pin_capture_state(state)
        except Exception:pass
        _pin_capture=None; _pending_job_id=""
        details=traceback.format_exc()
        summary=str(exc) or "Capturing animated Pin targets failed."
        code=classify_error("PREPARING",summary,details)
        shared_controller.fail(summary,details,error_code=code)
        _console_error("PREPARING",summary,details,code)
        companion_manager.persist_bake_error(shared_controller.snapshot())
        return None


def _startup_pump() -> float | None:
    global _pending_plan, _pending_job_id
    plan, job_id = _pending_plan, _pending_job_id
    if plan is None or not job_id: return None
    owner = shared_controller.snapshot()
    if (owner.job_id != job_id
            or owner.state is not BakeState.WAITING_FOR_COMPANION):
        # This callback belongs to an older PPF attempt and must not affect
        # the controller for a newer Bake attempt.
        if _pending_job_id == job_id:
            _pending_plan = None
            _pending_job_id = ""
        return None
    state, message = companion_manager.startup_status(job_id)
    if state == "WAITING":
        shared_controller.update(status_message=message); return .05
    if state != "READY":
        _pending_plan = None; _pending_job_id = ""
        shared_controller.fail(message); return None
    if not companion_manager.consume_ready(job_id): return .05
    shared_controller.transition(BakeState.COMPANION_READY,
                                 status_message="Bake window ready")
    try: bpy.ops.clothnext.bake_modal("INVOKE_DEFAULT", job_id=job_id)
    except (AttributeError, RuntimeError) as exc:
        modal_lock.release(job_id); _pending_plan = None; _pending_job_id = ""
        shared_controller.fail("The modal Bake workflow could not start.", str(exc))
    return None


def cancel_pending_startup() -> None:
    global _pending_plan, _pending_job_id, _pin_capture
    if not _pending_job_id: return
    job_id = _pending_job_id
    companion_manager.cancel_startup(job_id)
    if _pin_capture is not None:
        try:
            _restore_pin_capture_state(_pin_capture)
        except Exception:pass
        _pin_capture=None
        if bpy.app.timers.is_registered(_pin_capture_pump):
            bpy.app.timers.unregister(_pin_capture_pump)
    _pending_plan = None; _pending_job_id = ""
    if shared_controller.snapshot().state is not BakeState.CANCELLING:
        shared_controller.request_cancel()
    shared_controller.transition(BakeState.CANCELLED,
                                 status_message="Bake startup cancelled")


def request_cancel() -> None:
    if _pending_job_id:
        cancel_pending_startup(); return
    # A Companion can disappear after startup while no capture or worker owns
    # the controller anymore.  In that state merely entering CANCELLING leaves
    # the UI locked forever because no pump remains to publish CANCELLED.
    if not run_active() and _active_plan is None:
        snapshot = shared_controller.snapshot()
        if snapshot.active:
            companion_manager.cancel_startup(
                snapshot.job_id, "Orphaned Bake state cancelled")
            if snapshot.state is not BakeState.CANCELLING:
                shared_controller.request_cancel()
            shared_controller.transition(
                BakeState.CANCELLED, status_message="Stale Bake state cleared")
            modal_lock.release(snapshot.job_id)
        return
    _cancel_event.set()
    snapshot = shared_controller.snapshot()
    if snapshot.active and snapshot.state is not BakeState.CANCELLING:
        shared_controller.request_cancel()


def shutdown(join_timeout: float = 30.0) -> bool:
    """Cancel and detach Blender callbacks without forgetting a live worker.

    The worker is normally joined completely.  If an external process or I/O
    call ignores cancellation past the bounded timeout, keep the worker/plan
    references so another Bake cannot start on top of it.  The thread is a
    daemon only as a final Blender-exit safeguard; owned solver cleanup still
    happens inside the session worker.
    """
    global _worker, _active_plan, _unsubscribe, _pending_plan, _pending_job_id, _pin_capture
    if _pending_job_id:
        companion_manager.cancel_startup(_pending_job_id, "Add-on shutdown")
    if _pin_capture is not None:
        try:
            _restore_pin_capture_state(_pin_capture)
        except Exception:
            pass
    _pending_plan = None; _pending_job_id = ""; _pin_capture=None; modal_lock.release()
    _cancel_event.set()
    worker = _worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=max(0.0, float(join_timeout)))
    stopped = worker is None or not worker.is_alive()
    if stopped:
        _worker, _active_plan = None, None
        shared_telemetry.set_solver_pid(None)
    else:
        log_with_context(
            get_logger("solver.worker"), 40,
            "Bake worker did not stop during add-on shutdown",
            {"join_timeout": float(join_timeout)})
    if _unsubscribe is not None:
        _unsubscribe()
        _unsubscribe = None
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    if bpy.app.timers.is_registered(_pump_watchdog):
        bpy.app.timers.unregister(_pump_watchdog)
    if bpy.app.timers.is_registered(_startup_pump):
        bpy.app.timers.unregister(_startup_pump)
    if bpy.app.timers.is_registered(_pin_capture_pump):
        bpy.app.timers.unregister(_pin_capture_pump)
    while not _queue.empty():
        try:
            _queue.get_nowait()
        except queue.Empty:
            break
    return stopped


# ---------------------------------------------------------------------------
# Material fingerprint and parameter inspection (diagnostic only)

def current_settings_fingerprint(context) -> str | None:
    """Complete Bake fingerprint: settings AND geometry.

    EXPENSIVE — it runs a full validation (topology hash + pin scan). It must
    never be called from ``Panel.draw()`` or ``Panel.poll()``; the UI uses
    :func:`cheap_settings_fingerprint` and the recorded validation state
    instead. Kept for diagnostics and for callers that genuinely want the
    authoritative combined value.

    Returns ``None`` when the scene is not exactly one cloth plus one collider
    or a value is invalid — it never raises.
    """
    try:
        snapshot = validate_scene(context)
    except (SceneValidationError, MaterialValidationError, ClothNextError,
            ValueError):
        return None
    return snapshot.combined_fingerprint


def build_parameter_inspection(context) -> tuple[tuple[str, ...], dict]:
    """Validate the current settings and build the exact Param payload
    without starting PPF.

    Returns human-readable summary lines (artist and wire names) plus the
    JSON-safe payload dictionary. Contains no mesh data, no secrets, and no
    binary CBOR; placeholder UUIDs stand in for the per-run random ones.
    """
    snapshot = validate_scene(context)
    cloth_obj, collider_obj = snapshot.cloth_obj, snapshot.collider_obj
    shell, static = snapshot.shell, snapshot.static
    contact_enabled, preset = snapshot.contact_enabled, snapshot.preset_identifier
    pin_snapshot = snapshot.pin_membership
    pin_config = static_pin_config(pin_snapshot)
    scene = context.scene
    bake_range = snapshot.bake_range
    settings = SimulationSettings(
        frame_count=bake_range.output_count,
        fps=_scene_fps(context),
        gravity_blender=snapshot.gravity_blender,
        quality=snapshot.quality,
        wind_blender=snapshot.wind_blender)
    collider_specs = (() if collider_obj is None else
        ((collider_obj.name, "inspect-collider", static),))
    if not collider_specs:
        sentinel=internal_static_sentinel()
        collider_specs=((sentinel.name,sentinel.uuid,DEFAULT_STATIC_SETTINGS),)
    payload = build_multi_collider_param_payload(
        settings, cloth_obj.name, "inspect-cloth", collider_specs,
        shell=shell, contact_enabled=contact_enabled,
        static_pin=pin_config)
    lines: list[str] = [f"Material Preset: {preset}",
                        f"Cloth: {cloth_obj.name} (SHELL)"]
    for artist_label, ppf_key, value in \
            material_formatting.shell_wire_rows(shell):
        lines.append(f"{artist_label} — PPF {ppf_key}: {value}")
    if collider_obj is None:
        lines.append("Colliders: None (optional)")
        static_rows = ()
    else:
        lines.append(f"Collider: {collider_obj.name} (STATIC)")
        static_rows = material_formatting.static_wire_rows(static)
    for artist_label, ppf_key, value in static_rows:
        lines.append(f"{artist_label} — PPF {ppf_key}: {value}")
    wire_scene = payload["scene"]
    lines.append(f"Solver Quality — PPF dt: {wire_scene['dt']}, "
                 f"min-newton-steps: {wire_scene['min-newton-steps']}, "
                 f"cg-max-iter: {wire_scene['cg-max-iter']}, "
                 f"cg-tol: {wire_scene['cg-tol']}")
    lines.append(f"Scene — frames: {wire_scene['frames']}, "
                 f"fps: {wire_scene['fps']}, "
                 f"friction-mode: {wire_scene['friction-mode']}, "
                 f"disable-contact: {wire_scene['disable-contact']}")
    if pin_snapshot.enabled:
        mode=("TARGET_OBJECT" if bool(getattr(
            cloth_obj.cloth_next, "advanced_pin_motion_enabled", False)) else
            str(getattr(cloth_obj.cloth_next,"pin_mode","STATIC")))
        mode_label = ({"FOLLOW_ANIMATION": "Follow Animation",
                       "TARGET_OBJECT": "Target Object"}.get(mode, "Static"))
        lines.extend((f"Pinning: {mode_label}", f"Group: {pin_snapshot.group_name}",
                      f"Pinned vertices: {len(pin_snapshot.vertex_indices)}",
                      f"Index range: {pin_snapshot.vertex_indices[0]}–{pin_snapshot.vertex_indices[-1]}",
                      "Operations: 0", "Pull: Disabled", "Release: Never"))
    else:
        lines.append("Pinning: Disabled")
    return tuple(lines), payload


class CLOTHNEXT_OT_inspect_parameters(bpy.types.Operator):
    """Show the exact encoded PPF parameters without starting the solver"""

    bl_idname = "clothnext.inspect_parameters"
    bl_label = "Inspect Encoded Parameters"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            lines, payload = build_parameter_inspection(context)
        except (SceneValidationError, MaterialValidationError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        copied = False
        window_manager = getattr(context, "window_manager", None)
        if window_manager is not None:
            try:
                window_manager.clipboard = json.dumps(payload, indent=2)
                copied = True
            except (AttributeError, TypeError):
                copied = False
        if window_manager is not None and hasattr(window_manager,
                                                  "popup_menu"):
            def draw_popup(menu, _context, _lines=lines):
                for line in _lines:
                    menu.layout.label(text=line)
            window_manager.popup_menu(draw_popup,
                                      title="Encoded PPF Parameters",
                                      icon="INFO")
        suffix = (" JSON diagnostics copied to the clipboard."
                  if copied else "")
        self.report({"INFO"}, "Encoded parameters inspected — no solver "
                              "was started." + suffix)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operators

class CLOTHNEXT_OT_solver_test_run(bpy.types.Operator):
    """Run the real PPF solver diagnostic on the current scene"""

    bl_idname = "clothnext.solver_test_run"
    bl_label = "Run Real Solver Test"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return not run_active() and not shared_controller.snapshot().active

    def execute(self, context):
        try:
            warning = start_run(context, job_kind=BakeJobKind.SOLVER_TEST)
        except (SceneValidationError, ClothNextError) as exc:
            message = (exc.record.user_message
                       if isinstance(exc, ClothNextError) else str(exc))
            _console_error("SOLVER_TEST", message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if warning:
            self.report({"WARNING"}, "Real solver test started, but the Bake "
                        f"window could not be opened: {warning}")
        else:
            self.report({"INFO"}, "Real solver test started.")
        return {"FINISHED"}


class CLOTHNEXT_OT_bake(bpy.types.Operator):
    """Validate the scene and start a Cloth NeXt Bake."""

    bl_idname = "clothnext.bake"
    bl_label = "Bake"
    bl_description = (
        "Validate the scene and start a Bake with the selected solver "
        "installation")
    _capture_timer = None
    _capture_modal_cleaned = False

    @classmethod
    def poll(cls, _context):
        return (not run_active() and _pending_plan is None
                and not shared_controller.snapshot().active)

    def execute(self, context):
        try:
            from . import solver_backends
            _job_id, waiting = solver_backends.begin_bake(context)
        except (SceneValidationError, ClothNextError, ValueError) as exc:
            message = exc.record.user_message if isinstance(exc, ClothNextError) else str(exc)
            snapshot = shared_controller.snapshot()
            _console_error("PREPARING", message, snapshot.error_details,
                           snapshot.error_code)
            self.report({"ERROR"}, message); return {"CANCELLED"}
        self.report({"INFO"}, "Opening Bake window…" if waiting
                    else "Cloth NeXt bake started in Blender.")
        if _pin_capture is not None:
            manager = getattr(context, "window_manager", None)
            if manager is not None and hasattr(manager, "event_timer_add"):
                self._capture_modal_cleaned = False
                self._capture_timer = manager.event_timer_add(
                    .1, window=getattr(context, "window", None))
                manager.modal_handler_add(self)
                window = getattr(context, "window", None)
                if window is not None and hasattr(window, "cursor_modal_set"):
                    window.cursor_modal_set("WAIT")
                return {"RUNNING_MODAL"}
        return {"FINISHED"}

    def _cleanup_capture_modal(self, context):
        if self._capture_modal_cleaned:
            return
        self._capture_modal_cleaned = True
        manager = getattr(context, "window_manager", None)
        if self._capture_timer is not None and manager is not None:
            manager.event_timer_remove(self._capture_timer)
            self._capture_timer = None
        window = getattr(context, "window", None)
        if window is not None and hasattr(window, "cursor_modal_restore"):
            window.cursor_modal_restore()

    def modal(self, context, event):
        if _pin_capture is None:
            self._cleanup_capture_modal(context)
            return ({"CANCELLED"} if shared_controller.snapshot().state
                    is BakeState.CANCELLED else {"FINISHED"})
        if event.type == "ESC":
            request_cancel()
            return {"RUNNING_MODAL"}
        if event.type == "TIMER":
            for area in getattr(getattr(context, "screen", None), "areas", ()):
                area.tag_redraw()
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        request_cancel()
        self._cleanup_capture_modal(context)


class CLOTHNEXT_OT_bake_modal(bpy.types.Operator):
    """Modal lock entered only by the matching companion-ready gate."""

    bl_idname = "clothnext.bake_modal"
    bl_label = "Cloth NeXt Modal Bake"
    bl_options = {"INTERNAL"}
    job_id: bpy.props.StringProperty(options={"HIDDEN"})
    _timer = None
    _modal_cleaned = False

    def _cleanup_modal(self, context) -> None:
        if self._modal_cleaned:
            return
        self._modal_cleaned = True
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def invoke(self, context, _event):
        global _pending_plan, _pending_job_id
        plan = _pending_plan
        if (plan is None or self.job_id != _pending_job_id
                or shared_controller.snapshot().state is not BakeState.COMPANION_READY):
            return {"CANCELLED"}
        manager = getattr(context, "window_manager", None)
        if manager is None or not hasattr(manager, "event_timer_add"):
            return {"CANCELLED"}
        # Do every fallible step (lock, cache prepare, worker start) BEFORE
        # handing the operator to Blender's modal system. modal_handler_add()
        # must run only just before returning {"RUNNING_MODAL"}: if a modal
        # handler is registered and invoke() then returns {"CANCELLED"},
        # Blender frees the operator while the handler still points at it. The
        # timer we would have added fires ~0.1s later, modal() reads
        # self.job_id on freed memory, and Blender crashes with an
        # EXCEPTION_ACCESS_VIOLATION inside RNA_property_string_get. This is
        # exactly the E100 "cache could not be removed" path.
        if not modal_lock.acquire(self.job_id,
                                  companion_ready_job_id=self.job_id):
            return {"CANCELLED"}
        try:
            prepare_cache_for_new_run(plan)
            shared_controller.transition(BakeState.STARTING_RUN,
                                         status_message="Starting Bake run")
            _start_prepared_run(plan)
        except (SceneValidationError, ClothNextError, OSError) as exc:
            modal_lock.release(self.job_id)
            details = traceback.format_exc()
            summary = str(exc) or "Starting the Bake failed."
            code = classify_error("PREPARING", summary, details)
            shared_controller.fail(summary, details, error_code=code)
            _console_error("PREPARING", summary, details, code)
            companion_manager.persist_bake_error(shared_controller.snapshot())
            _pending_plan = None; _pending_job_id = ""
            return {"CANCELLED"}
        except Exception:  # noqa: BLE001 -- never let Blender hide startup errors
            modal_lock.release(self.job_id)
            details = traceback.format_exc()
            summary = "Starting the Bake failed unexpectedly."
            code = classify_error("PREPARING", summary, details)
            shared_controller.fail(summary, details, error_code=code)
            _console_error("PREPARING", summary, details, code)
            companion_manager.persist_bake_error(shared_controller.snapshot())
            _pending_plan = None; _pending_job_id = ""
            return {"CANCELLED"}
        # The bake is now live. Only here, certain to return RUNNING_MODAL, do
        # we register the timer and modal handler.
        self._modal_cleaned = False
        self._timer = manager.event_timer_add(
            .1, window=getattr(context, "window", None))
        manager.modal_handler_add(self)
        _pending_plan = None; _pending_job_id = ""
        return {"RUNNING_MODAL"}

    def execute(self, context):
        return self.invoke(context, None)

    def modal(self, context, event):
        snapshot = shared_controller.snapshot()
        if not modal_lock.active(self.job_id):
            self._cleanup_modal(context)
            return {"FINISHED"}
        if event.type == "ESC" and snapshot.can_cancel:
            request_cancel()
            return {"RUNNING_MODAL"}
        if event.type == "TIMER":
            for area in getattr(context.screen, "areas", ()):
                area.tag_redraw()
        if snapshot.state in {BakeState.FINISHED, BakeState.CANCELLED,
                              BakeState.ERROR}:
            self._cleanup_modal(context)
            return ({"CANCELLED"} if snapshot.state is BakeState.CANCELLED
                    else {"FINISHED"})
        # Consume scene-editing input while keeping Blender's event loop,
        # redraw, native window management and this timer alive.
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        request_cancel()
        modal_lock.release(self.job_id)
        self._cleanup_modal(context)


class CLOTHNEXT_OT_bake_cancel(bpy.types.Operator):
    """Cancel the active Cloth NeXt bake"""

    bl_idname = "clothnext.bake_cancel"
    bl_label = "Cancel"
    bl_description = (
        "Cancel the active Bake and attempt to save a recovery checkpoint "
        "before stopping the solver")

    @classmethod
    def poll(cls, _context):
        return ((_pending_plan is not None or run_active())
                and shared_controller.snapshot().can_cancel)

    def execute(self, _context):
        request_cancel()
        return {"FINISHED"}


class CLOTHNEXT_OT_open_preferences(bpy.types.Operator):
    """Open Blender preferences for Cloth NeXt solver configuration"""

    bl_idname = "clothnext.open_preferences"
    bl_label = "Open Add-on Preferences"

    def execute(self, _context):
        try:
            bpy.ops.screen.userpref_show()
            bpy.context.preferences.active_section = "ADDONS"
            addon_show = getattr(bpy.ops.preferences, "addon_show", None)
            if addon_show is not None:
                addon_show(module=package_addon_id(__package__))
        except (AttributeError, RuntimeError):
            self.report({"WARNING"}, "Open Edit > Preferences > Add-ons > Cloth NeXt.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_test_cancel(bpy.types.Operator):
    """Cancel the running PPF solver test"""

    bl_idname = "clothnext.solver_test_cancel"
    bl_label = "Cancel Solver Test"
    bl_description = (
        "Cancel the active Bake and attempt to save a recovery checkpoint "
        "before stopping the solver")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return run_active() and shared_controller.snapshot().can_cancel

    def execute(self, _context):
        request_cancel()
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_test_clear(bpy.types.Operator):
    """Remove the Cloth NeXt test cache modifier and its PC2 files"""

    bl_idname = "clothnext.solver_test_clear"
    bl_label = "Clear Solver Test Result"
    bl_options = {"INTERNAL", "UNDO"}

    @classmethod
    def poll(cls, _context):
        return not run_active()

    def execute(self, context):
        removed_modifiers = 0
        removed_files = 0
        target = getattr(context, "object", None)
        objects = (target,) if target is not None else bpy.data.objects
        for obj in objects:
            settings = getattr(obj, "cloth_next", None)
            owned_paths = []
            if getattr(obj, "type", "") == "CURVE":
                recorded = str(getattr(getattr(obj, "data", None), "get",
                                       lambda *_: "")(
                    "cloth_next_rod_cache", "") or "")
                if recorded and Path(recorded).name.startswith("cn_test_cloth_"):
                    owned_paths.append(Path(recorded))
                    action = getattr(getattr(obj.data, "animation_data", None),
                                     "action", None)
                    if (action is not None
                            and bool(action.get("cloth_next_rod_action", False))):
                        obj.data.animation_data_clear()
                        bpy.data.actions.remove(action)
                    try:
                        del obj.data["cloth_next_rod_cache"]
                    except (KeyError, TypeError, AttributeError):
                        pass
            for mod in list(obj.modifiers):
                if is_cloth_next_playback_modifier(obj,mod):
                    filepath = getattr(mod, "filepath", "")
                    obj.modifiers.remove(mod)
                    removed_modifiers += 1
                    if filepath:
                        owned_paths.append(Path(bpy.path.abspath(filepath)))
            for path in set(owned_paths):
                if not path.name.startswith("cn_test_cloth_"):
                    continue
                try:
                    existed = path.exists() or cache_metadata.sidecar_path(
                        path).exists()
                    path.unlink(missing_ok=True)
                    cache_metadata.sidecar_path(path).unlink(missing_ok=True)
                    removed_files += int(existed)
                except OSError:
                    pass
            if settings is not None and (owned_paths or removed_modifiers):
                settings.baked_settings_fingerprint = ""
                settings.baked_geometry_fingerprint = ""
                settings.baked_fingerprint_version = 0
                settings.baked_cache_condition = ""
                settings.baked_cache_message = ""
                settings.baked_metadata_digest = ""
                validation_state.forget(obj)
        self.report({"INFO"},
                    f"Removed {removed_modifiers} Cloth NeXt test cache "
                    f"modifier(s) and {removed_files} cache file(s); nothing "
                    "else was touched.")
        if shared_controller.snapshot().state in {
                BakeState.FINISHED, BakeState.CANCELLED, BakeState.ERROR}:
            shared_controller.reset()
        return {"FINISHED"}


class CLOTHNEXT_OT_intersection_previous(bpy.types.Operator):
    bl_idname = "clothnext.intersection_previous"
    bl_label = "Previous Violation"
    bl_description = "Display the previous solver-reported intersection"

    def execute(self, _context):
        from . import intersection_overlay
        intersection_overlay.previous_violation()
        return {"FINISHED"}


class CLOTHNEXT_OT_intersection_next(bpy.types.Operator):
    bl_idname = "clothnext.intersection_next"
    bl_label = "Next Violation"
    bl_description = "Display the next solver-reported intersection"

    def execute(self, _context):
        from . import intersection_overlay
        intersection_overlay.next_violation()
        return {"FINISHED"}


class CLOTHNEXT_OT_intersection_select_element(bpy.types.Operator):
    bl_idname = "clothnext.intersection_select_element"
    bl_label = "Select Intersection Object"
    bl_description = (
        "Select the artist-facing object for one side of the displayed "
        "intersection; generated proxy diagnostics select their source object")
    element_index: bpy.props.IntProperty(default=0, min=0, max=1)

    def execute(self, context):
        from . import intersection_overlay
        violation = intersection_overlay.current()
        if violation is None or self.element_index >= len(violation.elements):
            return {"CANCELLED"}
        target = next((
            obj for obj in context.scene.objects
            if str(getattr(obj, "name", "")) ==
            violation.elements[self.element_index].object_name), None)
        if target is None:
            self.report({"WARNING"}, "The reported object is no longer available.")
            return {"CANCELLED"}
        for obj in context.selected_objects:
            obj.select_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        return {"FINISHED"}


class CLOTHNEXT_OT_intersection_frame(bpy.types.Operator):
    bl_idname = "clothnext.intersection_frame"
    bl_label = "Frame Intersection"
    bl_description = (
        "Frame the selected object containing the displayed solver "
        "intersection in the 3D View")

    def execute(self, context):
        try:
            bpy.ops.view3d.view_selected("INVOKE_DEFAULT", use_all_regions=False)
        except (AttributeError, RuntimeError):
            self.report({"WARNING"}, "Open a 3D View to frame the intersection.")
            return {"CANCELLED"}
        return {"FINISHED"}


class CLOTHNEXT_OT_intersection_show_input(bpy.types.Operator):
    bl_idname = "clothnext.intersection_show_input"
    bl_label = "Show Solver Input"
    bl_description = (
        "Display the exact evaluated and triangulated geometry that Cloth NeXt "
        "sent to the solver for initial intersection validation")

    def execute(self, _context):
        from . import intersection_overlay
        if intersection_overlay.solver_input_snapshot() is None:
            self.report({"WARNING"}, "No retained solver input snapshot is available.")
            return {"CANCELLED"}
        intersection_overlay.toggle_solver_input()
        return {"FINISHED"}


class CLOTHNEXT_OT_intersection_clear(bpy.types.Operator):
    bl_idname = "clothnext.intersection_clear"
    bl_label = "Clear Intersection Display"
    bl_description = (
        "Remove intersection highlights and solver-input diagnostics from the "
        "viewport without changing any simulation object")

    def execute(self, _context):
        _clear_intersection_diagnostics()
        return {"FINISHED"}


def _recovery_metadata_from_scene(scene) -> Path | None:
    settings = getattr(scene, "cloth_next_recovery", None)
    root = str(getattr(settings, "recovery_directory", "") or "").strip()
    return Path(root) / recovery.METADATA_NAME if root else None


class CLOTHNEXT_OT_recovery_resume_latest(bpy.types.Operator):
    bl_idname = "clothnext.recovery_resume_latest"
    bl_label = "Resume Latest"
    bl_description = (
        "Continue this Bake from the latest verified solver checkpoint")
    bl_options = {"INTERNAL"}

    @staticmethod
    def _disabled_reason(settings) -> str:
        reason = str(getattr(settings, "status_detail", "") or "").strip()
        # Old scene files and builds may have cached identity compatibility as
        # the disabled reason even though compatibility is not the blocker.
        if not reason or reason.casefold() == "compatible":
            return "No verified resumable checkpoint is available"
        return reason

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "cloth_next_recovery", None)
        if settings is None:
            return False
        busy = run_active() or shared_controller.snapshot().active
        # resumable already encodes compatibility when it is known; the
        # from-disk snapshot marks it provisionally until Bake start verifies
        # the identity authoritatively.
        allowed = bool(settings.resumable and not busy)
        if not allowed and hasattr(cls, "poll_message_set"):
            cls.poll_message_set(
                "A solver or Bake operation is still running"
                if busy else cls._disabled_reason(settings))
        return allowed

    def execute(self, context):
        settings = getattr(context.scene, "cloth_next_recovery", None)
        if settings is None:
            self.report({"ERROR"}, "Recovery settings are unavailable.")
            return {"CANCELLED"}
        try:
            diagnostics = _refresh_recovery_ui_from_disk()
        except Exception as exc:  # noqa: BLE001 -- operator reports a safe error
            diagnostics = _current_recovery_diagnostics(context.scene)
            _record_recovery_refresh_failure(settings, exc, diagnostics)
            self.report({"ERROR"}, settings.status_detail)
            return {"CANCELLED"}
        metadata = _recovery_metadata_from_scene(context.scene)
        eligibility = (recovery.evaluate_resumable(metadata)
                       if metadata is not None else None)
        if (eligibility is None or not eligibility.available
                or eligibility.checkpoint_count <= 0):
            settings.resume_requested = False
            message = (eligibility.reason if eligibility is not None
                       else "No selected Recovery metadata exists")
            settings.status = "Recovery Metadata Invalid"
            settings.status_detail = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if run_active() or shared_controller.snapshot().active:
            settings.resume_requested = False
            message = "A solver or Bake operation is still running"
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        settings.resume_requested = True
        settings.status = "Resume Starting"
        settings.status_detail = (
            f"Verifying compatibility for checkpoint frame "
            f"{eligibility.latest_checkpoint_frame}")
        try:
            job_id, waiting = begin_production_bake(context)
            snapshot = shared_controller.snapshot()
            if not job_id or not snapshot.active:
                raise SceneValidationError(
                    "The production Bake did not enter a valid startup state")
        except (SceneValidationError, ClothNextError, OSError, RuntimeError) as exc:
            settings.resume_requested = False
            message = (exc.record.user_message
                       if isinstance(exc, ClothNextError) else str(exc))
            settings.status = "Recovery Check Failed"
            settings.status_detail = message or "Resume did not start"
            context_data = dict(diagnostics)
            context_data.update(
                stage="RESUME_START", exception_type=type(exc).__name__,
                message=message, resulting_ui_state=settings.status)
            log_with_context(_recovery_log, logging.ERROR,
                             "Recovery Resume failed to start", context_data)
            self.report({"ERROR"}, settings.status_detail)
            return {"CANCELLED"}
        settings.status = "Resume Starting"
        settings.status_detail = (
            "Opening Bake window" if waiting else "Production Resume started")
        self.report({"INFO"}, "Recovery Resume started.")
        return {"FINISHED"}


class CLOTHNEXT_OT_recovery_start_fresh(bpy.types.Operator):
    bl_idname = "clothnext.recovery_start_fresh"
    bl_label = "Start Fresh"
    bl_description = (
        "Delete the saved recovery project and its verified checkpoints, "
        "then start a new Bake. The previous Bake cannot be resumed afterward")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return not run_active()

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        metadata = _recovery_metadata_from_scene(context.scene)
        if metadata is None:
            return {"FINISHED"}
        record = recovery.load_project(metadata, verify_checkpoints=False)
        if record is not None:
            try:
                record = recovery.transition(
                    metadata, record, recovery.ProjectState.ABANDONED,
                    error="Fresh Bake confirmed by user")
            except ValueError:
                pass
            project = Path(record.project_root).resolve()
            server_root = Path(record.server_data_root).resolve()
            if project != server_root and project.is_relative_to(server_root):
                shutil.rmtree(project, ignore_errors=True)
            for _uuid, partial in record.partial_pc2:
                try:
                    Path(partial).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                recovery.transition(
                    metadata, record, recovery.ProjectState.DELETED)
            except (OSError, ValueError):
                pass
        settings = context.scene.cloth_next_recovery
        settings.resume_requested = False
        settings.resumable = False
        settings.compatible = False
        settings.status = "Fresh Bake"
        settings.status_detail = "Previous recovery project removed"
        return {"FINISHED"}


class CLOTHNEXT_OT_recovery_clear_checkpoints(bpy.types.Operator):
    bl_idname = "clothnext.recovery_clear_checkpoints"
    bl_label = "Clear Checkpoints"
    bl_description = (
        "Delete all verified checkpoints for this recovery project. "
        "This Bake cannot be resumed afterward")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return not run_active()

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        metadata = _recovery_metadata_from_scene(context.scene)
        if metadata is not None:
            recovery.clear_checkpoints(metadata)
        settings = context.scene.cloth_next_recovery
        settings.resumable = False
        settings.compatible = False
        settings.status = "No checkpoint"
        settings.status_detail = "Saved States cleared"
        return {"FINISHED"}


class CLOTHNEXT_OT_recovery_open_folder(bpy.types.Operator):
    bl_idname = "clothnext.recovery_open_folder"
    bl_label = "Open Recovery Folder"
    bl_description = (
        "Open the folder containing this Bake's recovery metadata and "
        "verified checkpoint files")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        metadata = _recovery_metadata_from_scene(context.scene)
        return bool(metadata is not None and metadata.parent.is_dir())

    def execute(self, context):
        metadata = _recovery_metadata_from_scene(context.scene)
        assert metadata is not None
        os.startfile(str(metadata.parent))
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_test_open_logs(bpy.types.Operator):
    """Open the folder holding the last solver test run's logs and data"""

    bl_idname = "clothnext.solver_test_open_logs"
    bl_label = "Open Test Log Folder"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return (_last_work_directory is not None
                and _last_work_directory.is_dir())

    def execute(self, _context):
        assert _last_work_directory is not None
        os.startfile(str(_last_work_directory))  # noqa: S606 — explicit user action
        return {"FINISHED"}


class CLOTHNEXT_OT_companion_open_logs(bpy.types.Operator):
    bl_idname = "clothnext.companion_open_logs"
    bl_label = "Open Bake Window Logs"

    def execute(self, _context):
        os.startfile(str(companion_manager.log_directory()))
        return {"FINISHED"}


class CLOTHNEXT_OT_validate(bpy.types.Operator):
    """Validate the Cloth NeXt scene now (topology, materials, and pinning)"""

    bl_idname = "clothnext.validate"
    bl_label = "Validate Scene"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        # Cheap: a controller flag. No mesh is touched to decide this.
        return not run_active() and not shared_controller.snapshot().active

    def execute(self, context):
        try:
            snapshot = validate_scene(context)
        except (SceneValidationError, MaterialValidationError,
                ClothNextError) as exc:
            message = (exc.record.user_message
                       if isinstance(exc, ClothNextError) else str(exc))
            _console_error("VALIDATING", message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        pinned = len(snapshot.pin_membership.vertex_indices)
        self.report({"INFO"}, f"Cloth NeXt scene validated · {pinned} pinned "
                              f"vertices." if snapshot.pin_membership.enabled
                    else "Cloth NeXt scene validated.")
        return {"FINISHED"}


class CLOTHNEXT_OT_set_cache_directory(bpy.types.Operator):
    """Choose the folder where Cloth NeXt writes and keeps the bake result.

    A cache left in Blender's temporary folder is deleted on the next launch;
    a chosen folder keeps every baked result across restarts. The folder is
    applied to every enabled Cloth, Cable / Rope, and Soft Body object at once.
    """

    bl_idname = "clothnext.set_cache_directory"
    bl_label = "Set Cache Directory"
    bl_options = {"INTERNAL", "UNDO"}
    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, _context):
        return not run_active() and not shared_controller.snapshot().active

    @staticmethod
    def _deformables(context):
        scene = getattr(context, "scene", None)
        objects = getattr(scene, "objects", ()) if scene is not None else ()
        return [obj for obj in objects
                if getattr(getattr(obj, "cloth_next", None), "enabled", False)
                and getattr(obj.cloth_next, "role", "") in
                {"CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY"}]

    def invoke(self, context, _event):
        deformables = self._deformables(context)
        if not deformables:
            self.report({"ERROR"},
                        "Enable a Cloth, Cable / Rope, or Soft Body object first.")
            return {"CANCELLED"}
        existing = str(getattr(deformables[0].cloth_next,
                               "cache_directory", "") or "").strip()
        if existing:
            self.directory = bpy.path.abspath(existing)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        deformables = self._deformables(context)
        if not deformables:
            self.report({"ERROR"},
                        "Enable a Cloth, Cable / Rope, or Soft Body object first.")
            return {"CANCELLED"}
        directory = str(self.directory or "").strip()
        if not directory:
            self.report({"ERROR"}, "No folder was chosen.")
            return {"CANCELLED"}
        for obj in deformables:
            obj.cloth_next.cache_directory = directory
        self.report({"INFO"},
                    f"Cache directory set for {len(deformables)} object(s): "
                    f"{directory}")
        return {"FINISHED"}


def install_validator() -> None:
    """Hand the expensive validator to the cheap runtime state module.

    validation_state owns only the recorded outcome and the debounced timer;
    the mesh work lives here. Installed as a registration step so an
    unregister/register cycle re-arms it.
    """
    validation_state.set_validator(_validate_active_cloth)


CLASSES = (CLOTHNEXT_OT_bake, CLOTHNEXT_OT_bake_modal,
           CLOTHNEXT_OT_bake_cancel,
           CLOTHNEXT_OT_open_preferences,
           CLOTHNEXT_OT_validate,
           CLOTHNEXT_OT_solver_test_run, CLOTHNEXT_OT_solver_test_cancel,
           CLOTHNEXT_OT_solver_test_clear, CLOTHNEXT_OT_solver_test_open_logs,
           CLOTHNEXT_OT_intersection_previous,
           CLOTHNEXT_OT_intersection_next,
           CLOTHNEXT_OT_intersection_select_element,
           CLOTHNEXT_OT_intersection_frame,
           CLOTHNEXT_OT_intersection_show_input,
           CLOTHNEXT_OT_intersection_clear,
           CLOTHNEXT_OT_recovery_resume_latest,
           CLOTHNEXT_OT_recovery_start_fresh,
           CLOTHNEXT_OT_recovery_clear_checkpoints,
           CLOTHNEXT_OT_recovery_open_folder,
           CLOTHNEXT_OT_companion_open_logs,
           CLOTHNEXT_OT_set_cache_directory,
           CLOTHNEXT_OT_inspect_parameters)
