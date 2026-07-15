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
import json
import math
import os
import queue
import re
import threading
import time
import traceback
import uuid as uuid_module
from dataclasses import asdict, dataclass, field
from pathlib import Path

import bpy
import numpy as np

from .. import manifest_version
from ..bake import cache_metadata
from ..bake import pc2
from ..bake.controller import InvalidTransition, shared_controller
from ..bake.frame_range import BakeFrameRange, BakeRangeError
from ..bake.status import BakeActivity, BakeJobKind, BakeState
from ..bake.transport import EnterBakeMode
from ..core.errors import ClothNextError
from ..core.logging import get_logger, log_with_context
from ..materials import MaterialValidationError
from ..materials import formatting as material_formatting
from ..pinning import (
    STATIC_PIN_WEIGHT_THRESHOLD,
    AnimatedPinTargetSample,
    PinMode,
    StaticPinError,
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
    SolverResolutionContext,
    SolverResolver,
    development_executable_from_environment,
)
from ..ppf.schema.data import (SceneObject, encode_deformable_scene,
                               encode_scene, zero_area_triangles)
from ..ppf.schema.params import (
    SimulationSettings,
    build_param_payload,
    static_wire_params,
)
from ..materials.deformables import DeformableMaterialError
from ..curve_rod import CurveRodError, sample_curve
from ..ppf_run import import_result
from ..ppf_run.session import (
    SessionCancelled,
    SessionScene,
    SolverFrame,
    SolverSession,
    new_project_name,
)
from ..telemetry import shared_telemetry
from ..topology import geometry_fingerprint as combine_geometry_fingerprint
from ..topology import mesh_geometry_signature
from ..topology import mesh_topology_signature as _hash_mesh_topology
from ..topology import pin_indices_signature
from ..updater.install_paths import ManagedSolverPaths, read_current
from . import companion_manager, modal_lock, object_properties, validation_state
from .playback_cache import (
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


def _on_controller_snapshot(snapshot) -> None:
    """Any CANCELLING transition (panel, HUD, or companion IPC) reaches the
    worker through the shared cancel event."""
    if snapshot.state is BakeState.CANCELLING and _worker is not None:
        _cancel_event.set()


class SceneValidationError(ValueError):
    pass


@dataclass(slots=True)
class ColliderMotionCapture:
    """Compact main-thread capture ready for the official PPF scene fields."""

    motion_type: str
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    transform: tuple[tuple[float, float, float, float], ...]
    animation: dict | None = None
    temporary_path: Path | None = None

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
    fps: int = 24
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


# ---------------------------------------------------------------------------
# Solver resolution (reuses the existing resolver and preferences)

def _version_probe(executable: Path) -> tuple[str, str, str]:
    from ..ppf.models import ConnectionOwnership
    from ..ppf.process import SolverProcessConfig, SolverProcessManager
    config = SolverProcessConfig(
        executable_path=executable, working_directory=executable.parent,
        connect_timeout=10.0, ownership_mode=ConnectionOwnership.OWNED_PROCESS)
    return SolverProcessManager(config).executable_version()


def _managed_root() -> Path | None:
    try:
        paths = ManagedSolverPaths.default()
        active = read_current(paths)
        if active is None:
            return None
        return active.executable_path(paths).parent
    except (OSError, ValueError):
        return None


def resolve_solver(context) -> ResolvedSolver:
    addon_id = __package__.partition(".blender")[0]
    external = None
    try:
        preferences = context.preferences.addons[addon_id].preferences
        raw = (preferences.external_solver_path or "").strip()
        external = Path(raw) if raw else None
    except (KeyError, AttributeError):
        pass
    resolver = SolverResolver(_version_probe)
    resolved = resolver.resolve(SolverResolutionContext(
        external_path=external,
        managed_root=_managed_root(),
        development_executable=development_executable_from_environment()))
    if resolved is None or resolved.executable_path is None:
        raise SceneValidationError(
            "No compatible PPF solver installation is configured. Select or "
            "install one in the Cloth NeXt add-on preferences.")
    return resolved


# ---------------------------------------------------------------------------
# Main-thread scene snapshot and validation

def _enabled_objects_by_role(context) -> tuple[object, object]:
    cloth_objects, collider_objects = [], []
    for obj in context.scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is None or not settings.enabled:
            continue
        if settings.role in {"CLOTH", "ROD", "SOFT_BODY"}:
            cloth_objects.append(obj)
        elif settings.role == "COLLIDER":
            collider_objects.append(obj)
    if len(cloth_objects) != 1:
        raise SceneValidationError(
            f"Exactly one enabled Cloth NeXt cloth object is required for the "
            f"test run; found {len(cloth_objects)}.")
    if len(collider_objects) != 1:
        raise SceneValidationError(
            f"Exactly one enabled Cloth NeXt collider object is required for "
            f"the test run; found {len(collider_objects)}.")
    return cloth_objects[0], collider_objects[0]


def _enabled_objects_for_bake(context) -> tuple[object, tuple[object, ...]]:
    """Return one Cloth and deterministically ordered enabled Colliders."""
    cloth_objects, collider_objects = [], []
    for obj in context.scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is None or not settings.enabled:
            continue
        if settings.role in {"CLOTH", "ROD", "SOFT_BODY"}:
            cloth_objects.append(obj)
        elif settings.role == "COLLIDER":
            collider_objects.append(obj)
    if len(cloth_objects) != 1:
        raise SceneValidationError(
            f"Exactly one enabled Cloth NeXt cloth object is required; "
            f"found {len(cloth_objects)}.")
    if not collider_objects:
        raise SceneValidationError(
            "At least one enabled Cloth NeXt collider object is required.")
    collider_objects.sort(key=lambda obj: (
        validation_state.object_key(obj),
        str(getattr(obj, "name_full", getattr(obj, "name", "")))))
    return cloth_objects[0], tuple(collider_objects)


def _extract_mesh(obj, depsgraph, *, needs_edges: bool):
    """Evaluated local vertices + loop triangles, original vertex order."""
    if obj.type != "MESH":
        raise SceneValidationError(f"{obj.name} is not a mesh object.")
    if any(mod.type == "CLOTH" for mod in obj.modifiers):
        raise SceneValidationError(
            f"{obj.name} carries a native Blender Cloth modifier; remove it — "
            "Cloth NeXt never uses native cloth.")
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
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
    finally:
        evaluated.to_mesh_clear()


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
        else:
            shell = object_properties.shell_settings_from(cloth_obj.cloth_next)
            preset_identifier = str(cloth_obj.cloth_next.material.preset)
    except (MaterialValidationError, DeformableMaterialError) as exc:
        raise SceneValidationError(
            f"{cloth_obj.name}: invalid material value — {exc}") from exc
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
        cloth_obj, collider_objs[0])
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

SETTINGS_FINGERPRINT_VERSION = 2
BAKE_FINGERPRINT_VERSION = 2


def _cheap_pinning_fingerprint(cloth_obj) -> str:
    """Pinning *intent* — enabled, group name, mode. Never the indices."""
    settings = cloth_obj.cloth_next
    record = "\0".join((
        "1" if getattr(settings, "pinning_enabled", False) else "0",
        str(getattr(settings, "pin_group", "") or ""),
        str(getattr(settings, "pin_mode", "STATIC")),
    ))
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def _scene_fps(context) -> int:
    render = getattr(getattr(context, "scene", None), "render", None)
    try:
        return int(getattr(render, "fps", 24) or 24)
    except (TypeError, ValueError):
        return 24


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
                     else (collider_obj,))
    statics = static if isinstance(static, tuple) else (static,)
    if str(cloth_obj.cloth_next.role) == "CLOTH":
        base = material_formatting.settings_fingerprint(
            shell, statics[0], contact_enabled, preset_identifier,
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
            "world_matrix": world,
            "material": static_wire_params(material),
        }, sort_keys=True, separators=(",", ":")))
    record = "\0".join((
        str(SETTINGS_FINGERPRINT_VERSION), base,
        validation_state.object_key(cloth_obj),
        str(cloth_obj.cloth_next.role),
        json.dumps(_world_matrix_record(cloth_obj),
                   separators=(",", ":")),
        *collider_settings,
        str(_scene_fps(context)),
    ))
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def cheap_settings_fingerprint(context) -> str | None:
    """UI-safe settings fingerprint. Reads properties only — never a mesh.

    Returns ``None`` when the scene is not exactly one cloth plus one
    collider, or a mapped value is invalid.
    """
    try:
        cloth_obj, collider_objs = _enabled_objects_for_bake(context)
        shell, statics, contact_enabled, preset = _snapshot_materials_multi(
            cloth_obj, collider_objs)
        quality = object_properties.solver_quality_from(context.scene)
    except (SceneValidationError, MaterialValidationError, ValueError,
            AttributeError):
        return None
    return _settings_fingerprint(context, cloth_obj, collider_objs, shell,
                                 statics, contact_enabled, preset, quality)


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
class ValidationSnapshot:
    """One complete, validated view of the scene. Produced exactly once.

    Holds live ``bpy`` objects and is therefore main-thread-only and
    strictly transient: it is handed straight to :func:`build_run_plan` and
    never stored in a PropertyGroup, a handler, or a thread.
    """

    cloth_obj: object
    collider_obj: object
    collider_objs: tuple[object, ...]
    bake_range: BakeFrameRange
    shell: object
    static: object
    statics: tuple[object, ...]
    contact_enabled: bool
    preset_identifier: str
    quality: object
    pin_membership: StaticPinSnapshot
    topology_signature: str
    settings_fingerprint: str
    geometry_fingerprint: str
    combined_fingerprint: str


def validate_scene(context) -> ValidationSnapshot:
    """Fully validate the scene: topology, materials, pinning, fingerprints.

    EXPENSIVE by design and the *only* place the mesh is scanned. Called from
    Bake, Rebake, the explicit Validate operator, and the debounced validation
    timer — never from ``Panel.draw()`` or ``Panel.poll()``.

    The result is recorded in :mod:`validation_state` (VALID or INVALID with a
    readable message) and returned so the Bake path can reuse it without
    scanning anything a second time.
    """
    cloth_obj, collider_objs = _enabled_objects_for_bake(context)
    collider_obj = collider_objs[0]
    validation_state.mark_validating(cloth_obj)
    try:
        try:
            bake_range = BakeFrameRange(int(cloth_obj.cloth_next.bake_start),
                                        int(cloth_obj.cloth_next.bake_end))
        except (BakeRangeError, TypeError, ValueError) as exc:
            raise SceneValidationError(str(exc)) from exc
        shell, statics, contact_enabled, preset_identifier = (
            _snapshot_materials_multi(cloth_obj, collider_objs))
        static = statics[0]
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
                    "stash it before baking Rod playback.")
            topology_signature = hashlib.sha256(json.dumps(
                {"vertices": len(vertices), "edges": edges},
                separators=(",", ":")).encode("utf-8")).hexdigest()
            deformable_shape_signature = cache_metadata.deterministic_hash({
                "vertices": vertices, "edges": edges})
            if bool(cloth_obj.cloth_next.pinning_enabled):
                raise SceneValidationError(
                    "Rod pinning is not available yet; disable Pinning.")
            pin_membership = StaticPinSnapshot(
                False, "", str(cloth_obj.name), len(vertices), (),
                source_topology_signature=topology_signature)
        else:
            topology_signature = mesh_topology_signature(
                getattr(cloth_obj, "data", None))
            deformable_shape_signature = mesh_geometry_signature(
                getattr(cloth_obj, "data", None),
                topology_signature=topology_signature)
            if role == "SOFT_BODY" and bool(cloth_obj.cloth_next.pinning_enabled):
                raise SceneValidationError(
                    "Soft Body pinning is not available yet; disable Pinning.")
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

def _solver_position(matrix,position):
    x,y,z=position
    return tuple(sum(float(matrix[row][column])*value for column,value in
                     enumerate((x,y,z,1.0))) for row in range(3))

def _capture_animated_pin(context,cloth_obj,bake_range,membership,
                          precomputed=None):
    mode=PinMode(str(getattr(cloth_obj.cloth_next,"pin_mode","STATIC")))
    common=dict(source_topology_signature=membership.source_topology_signature,
                mode=mode,bake_start=bake_range.start,bake_end=bake_range.end,
                fps=int(context.scene.render.fps))
    if not membership.enabled or mode is PinMode.STATIC:
        return StaticPinSnapshot(membership.enabled,membership.group_name,
            membership.source_object_id,membership.source_vertex_count,
            membership.vertex_indices,**common)
    if precomputed is not None:
        return StaticPinSnapshot(True,membership.group_name,membership.source_object_id,
            membership.source_vertex_count,membership.vertex_indices,
            samples=tuple(precomputed),**common)
    scene=context.scene; original=int(scene.frame_current); samples=[]
    try:
        for frame in range(bake_range.start,bake_range.end+1):
            scene.frame_set(frame); _depsgraph_update(context)
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
                samples.append(AnimatedPinTargetSample(frame,positions))
            finally:evaluated.to_mesh_clear()
    finally:
        scene.frame_set(original); _depsgraph_update(context)
    return StaticPinSnapshot(True,membership.group_name,membership.source_object_id,
        membership.source_vertex_count,membership.vertex_indices,
        samples=tuple(samples),**common)


def _matrix_trs(matrix):
    """Decompose one solver-space matrix using Blender's evaluated math."""
    from mathutils import Matrix
    location, rotation, scale = Matrix(matrix).decompose()
    return ([float(value) for value in location],
            [float(rotation.w), float(rotation.x), float(rotation.y),
             float(rotation.z)],
            [float(value) for value in scale])


COLLIDER_SAMPLES_PER_FRAME = 2


def _collider_sample_points(bake_range: BakeFrameRange, fps: int):
    """Half-frame evaluated samples, including both Bake endpoints."""
    intervals = bake_range.output_count - 1
    count = intervals * COLLIDER_SAMPLES_PER_FRAME + 1
    result = []
    for index in range(count):
        position = bake_range.start + index / COLLIDER_SAMPLES_PER_FRAME
        frame = math.floor(position)
        subframe = position - frame
        result.append((frame, subframe,
                       index / (float(fps) * COLLIDER_SAMPLES_PER_FRAME)))
    return tuple(result)


def _capture_collider_motion(context, collider_obj,
                             bake_range: BakeFrameRange) -> ColliderMotionCapture:
    """Capture and classify one animated Collider on Blender's main thread."""
    scene = context.scene
    original_frame = int(scene.frame_current)
    sample_points = _collider_sample_points(
        bake_range, _scene_fps(context))
    sample_count = len(sample_points)
    times = [point[2] for point in sample_points]
    reference_vertices = None
    reference_triangles = None
    matrices = []
    local_samples = None
    temporary_path = None
    try:
        for offset, (frame, subframe, _time) in enumerate(sample_points):
            if _cancel_event.is_set():
                raise SessionCancelled()
            shared_controller.update(
                status_message=(f"Capturing collider animation · frame "
                                f"{frame + subframe:g} / {bake_range.end}"),
                current_frame=frame, progress_current=offset + 1,
                progress_total=sample_count)
            scene.frame_set(frame, subframe=subframe)
            _depsgraph_update(context)
            evaluated = collider_obj.evaluated_get(
                context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                count = len(mesh.vertices)
                mesh.calc_loop_triangles()
                triangles = tuple(tuple(int(i) for i in tri.vertices)
                                  for tri in mesh.loop_triangles)
                if count == 0 or not triangles:
                    raise SceneValidationError(
                        f'Collider "{collider_obj.name}" has an empty '
                        f'evaluated mesh at frame {frame + subframe:g}.')
                if reference_triangles is not None and (
                        count != len(reference_vertices)
                        or triangles != reference_triangles):
                    expected_triangles = len(reference_triangles)
                    detail = ("triangle indices changed" if
                              len(triangles) == expected_triangles
                              else "triangle count changed")
                    raise SceneValidationError(
                        f'Collider "{collider_obj.name}" changes topology at '
                        f'frame {frame + subframe:g}. Expected {len(reference_vertices)} '
                        f'vertices and {expected_triangles} triangles; got '
                        f'{count} vertices and {len(triangles)} triangles '
                        f'({detail}). Animated colliders must keep a '
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
                matrices.append(solver_world_matrix(world))
                if reference_vertices is None:
                    reference_vertices = local.copy()
                    reference_triangles = triangles
                    temporary_path = (Path(bpy.app.tempdir)
                        / f"cloth_next_collider_{uuid_module.uuid4().hex}.bin")
                    local_samples = np.memmap(
                        temporary_path, dtype="<f4", mode="w+",
                        shape=(sample_count, count, 3))
                local_samples[offset] = local
            finally:
                evaluated.to_mesh_clear()

        assert reference_vertices is not None and reference_triangles is not None
        assert local_samples is not None
        deforming = any(not np.allclose(local_samples[index],
                                        reference_vertices, rtol=0.0,
                                        atol=1e-6)
                                        for index in range(1, sample_count))
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
                {"time": times, "translation": translations,
                 "quaternion": quaternions, "scale": scales,
                 "segments": [
                     {"interpolation": "LINEAR",
                      "handle_right": [1.0 / 3.0, 0.0],
                      "handle_left": [2.0 / 3.0, 1.0]}
                     for _index in range(sample_count - 1)]})
            local_samples._mmap.close()
            temporary_path.unlink(missing_ok=True)
            return result

        # Reuse the local-position memmap in place for absolute solver-world
        # positions; peak numeric storage stays frames x vertices x 12 bytes.
        for index, matrix in enumerate(matrices):
            transform = np.asarray(matrix, dtype=np.float64)
            local_samples[index] = (
                local_samples[index] @ transform[:3, :3].T
                + transform[:3, 3])
        local_samples.flush()
        identity = tuple(tuple(1.0 if row == column else 0.0
                               for column in range(4)) for row in range(4))
        return ColliderMotionCapture(
            "DEFORMING_ANIMATED",
            tuple(tuple(float(value) for value in row)
                  for row in local_samples[0]),
            reference_triangles, identity,
            {"time": times, "vert_frames": local_samples}, temporary_path)
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
        scene.frame_set(original_frame)
        _depsgraph_update(context)


def build_run_plan(context, *, animated_pin_samples=None,
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
    static = statics[0]
    contact_enabled = snapshot.contact_enabled
    preset_identifier = snapshot.preset_identifier
    pin_membership = snapshot.pin_membership
    modifiers = tuple(getattr(cloth_obj, "modifiers", ()))
    relevant_modifiers=tuple(mod for mod in modifiers
                             if not is_cloth_next_playback_modifier(cloth_obj,mod))
    if relevant_modifiers and not pin_membership.enabled:
        raise SceneValidationError(
            f"{cloth_obj.name} has modifiers; the current unpinned solver "
            "slice requires a plain mesh.")
    # Compatibility probing happens before animation capture so a missing
    # solver cannot leave behind a large temporary Collider buffer.
    resolved = resolve_solver(context)
    original_frame = int(scene.frame_current)
    collider_records = []
    try:
        with without_owned_playback(cloth_obj,lambda:_depsgraph_update(context)):
            scene.frame_set(bake_range.start); _depsgraph_update(context)
            depsgraph = context.evaluated_depsgraph_get()
            if deformable_role == "ROD":
                cloth_vertices, cloth_edges, _curve_splines = sample_curve(cloth_obj)
                cloth_triangles = ()
            else:
                if deformable_role == "SOFT_BODY":
                    mesh = cloth_obj.data
                    open_edges = _non_manifold_edge_count(mesh)
                    if open_edges:
                        raise SceneValidationError(
                            f"{cloth_obj.name} is not a closed manifold surface "
                            f"({open_edges} boundary/non-manifold edges). Seal the "
                            "mesh before Soft Body tetrahedralization.")
                cloth_vertices, cloth_triangles = _extract_mesh(
                    cloth_obj, depsgraph, needs_edges=True)
                cloth_edges = ()
            pin_snapshot=_capture_animated_pin(context,cloth_obj,bake_range,
                                               pin_membership,animated_pin_samples)
        for current in collider_objs:
            if str(getattr(current.cloth_next, "collider_motion",
                           "STATIC")) == "ANIMATED":
                capture = _capture_collider_motion(
                    context, current, bake_range)
                collider_records.append((current, capture.vertices,
                                         capture.triangles, None, capture))
            else:
                scene.frame_set(bake_range.start); _depsgraph_update(context)
                depsgraph = context.evaluated_depsgraph_get()
                vertices, triangles = _extract_mesh(
                    current, depsgraph, needs_edges=False)
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
        scene.frame_set(original_frame)
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
    pin_config = static_pin_config(pin_snapshot)

    cloth_uuid = f"cn-cloth-{uuid_module.uuid4().hex[:12]}"
    scene_cloth = SceneObject(cloth_obj.name, cloth_uuid, cloth_vertices,
                              cloth_triangles, solver_world_matrix(cloth_world),
                              pin_snapshot.vertex_indices, edges=cloth_edges)
    scene_colliders = []
    collider_specs = []
    motion_meta = []
    for (current, vertices, triangles, world, capture), material in zip(
            collider_records, statics):
        collider_uuid = f"cn-collider-{uuid_module.uuid4().hex[:12]}"
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
                            "vertex_count": len(vertices),
                            "triangle_count": len(triangles)})
    try:
        if deformable_role == "CLOTH":
            data_payload, data_hash = encode_scene(scene_cloth, scene_colliders)
        else:
            data_payload, data_hash = encode_deformable_scene(
                scene_cloth, scene_colliders,
                group_type="ROD" if deformable_role == "ROD" else "SOLID")
    finally:
        for _obj, _vertices, _triangles, _world, capture in collider_records:
            if capture is not None:
                capture.cleanup()
    frame_count = bake_range.output_count
    quality = snapshot.quality
    settings = SimulationSettings(
        frame_count=frame_count, fps=int(scene.render.fps),
        gravity_blender=tuple(scene.gravity) if scene.use_gravity
        else (0.0, 0.0, 0.0), quality=quality)
    if deformable_role == "CLOTH":
        from ..ppf.schema.params import encode_multi_collider_param
        param_payload, param_hash = encode_multi_collider_param(
            settings, cloth_obj.name, cloth_uuid, collider_specs, shell=shell,
            contact_enabled=contact_enabled, static_pin=pin_config)
    else:
        from ..ppf.schema.params import encode_deformable_param
        param_payload, param_hash = encode_deformable_param(
            settings, cloth_obj.name, cloth_uuid, collider_specs,
            group_type="ROD" if deformable_role == "ROD" else "SOLID",
            material=shell, contact_enabled=contact_enabled)
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
        "fps": int(scene.render.fps),
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
        "static": static_wire_params(static),
        "colliders": motion_meta,
        "quality": {
            "dt": settings.quality.time_step,
            "min-newton-steps": settings.quality.min_newton_steps,
            "cg-max-iter": settings.quality.cg_max_iter,
            "cg-tol": settings.quality.cg_tol,
        },
        "blender_start_frame": bake_range.start,
        "blender_end_frame": bake_range.end,
        "output_frame_count": frame_count,
        "solver_step_count": bake_range.solver_steps,
        "fps": int(scene.render.fps),
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

    project_name = new_project_name()
    session_scene = SessionScene(
        project_name=project_name,
        cloth_name=cloth_obj.name, cloth_uuid=cloth_uuid,
        cloth_vertex_count=len(cloth_vertices),
        collider_name=collider_specs[0][0], collider_uuid=collider_specs[0][1],
        frame_count=frame_count,
        data_payload=data_payload, param_payload=param_payload,
        data_hash=data_hash, param_hash=param_hash,
        deformable_type=("ROD" if deformable_role == "ROD" else
                         "SOLID" if deformable_role == "SOFT_BODY" else "SHELL"),
        deformable_world_matrix=solver_world_matrix(cloth_world))

    configured_cache = str(getattr(cloth_obj.cloth_next,
                                   "cache_directory", "") or "").strip()
    cache_directory = (Path(bpy.path.abspath(configured_cache))
                       if configured_cache else _cache_directory())
    work_directory = Path(bpy.app.tempdir) / f"cloth_next_run_{project_name}"
    pc2_path = cache_directory / f"cn_test_cloth_{project_name[10:]}.pc2"
    return RunPlan(scene=session_scene, resolved=resolved,
                   initial_local=cloth_vertices, world_matrix=cloth_world,
                   cloth_object_name=cloth_obj.name,
                   work_directory=work_directory, pc2_path=pc2_path,
                   frame_count=frame_count,
                   frame_start=bake_range.start, frame_end=bake_range.end,
                   fps=int(scene.render.fps),
                   settings_fingerprint=settings_fp,
                   geometry_fingerprint=geometry_fp,
                   topology_signature=snapshot.topology_signature,
                   preset_identifier=preset_identifier,
                   material_meta=material_meta,
                   deformable_role=deformable_role)


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
                    "The previous Rod cache could not be replaced. Rebake was not started.")
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


def _record_worker_failure(plan: RunPlan, summary: str, details: str) -> str:
    """Persist and print worker diagnostics without masking the real error."""
    failure_path = plan.work_directory / "failure.log"
    report = f"{summary}\n\n{details}\n"
    try:
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(report, encoding="utf-8")
        location = str(failure_path.resolve())
    except OSError as exc:
        location = f"unavailable ({type(exc).__name__}: {exc})"
    visible = f"{details}\nDiagnostic log: {location}"
    print(f"Cloth NeXt Bake failed: {summary}\n{visible}", flush=True)
    log_with_context(get_logger("solver.worker"), 40, summary, {
        "details": details, "diagnostic_log": location,
    })
    return visible


def _present_worker_error(plan: RunPlan, exc: ClothNextError) -> tuple[str, str]:
    """Translate technical solver failures into actionable Blender language."""
    record = exc.record
    technical = record.technical_message
    convergence = re.search(
        r"Linear solver failed to converge: advance failed at frame (\d+)",
        technical, re.IGNORECASE)
    if convergence:
        solver_frame = int(convergence.group(1))
        blender_frame = plan.frame_start + solver_frame
        summary = ("Simulation could not converge at Blender frame "
                   f"{blender_frame}.")
        action = ("Try a smaller Time Step or denser Collider motion "
                  "sampling, then inspect contact around the preceding "
                  "and failing frames.")
        details = (f"Stage: collision and constraint solve\n"
                   f"Solver frame: {solver_frame}\n"
                   f"Blender frame: {blender_frame}\n"
                   f"Cause: {technical}\n"
                   f"What to do: {action}")
        return summary, details
    details = (f"Cause: {technical}\n"
               f"What to do: {record.recommended_action}")
    return record.user_message, details

def _worker_main(plan: RunPlan) -> None:
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
            sample_rate=import_result.PC2_SAMPLE_RATE)
        step = time.monotonic()
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
            local = transform_points_numpy(to_local,
                                           frame.positions_solver_world)
            transform_seconds += time.monotonic() - step
            if _cancel_event.is_set():
                raise SessionCancelled()
            step = time.monotonic()
            writer.write_frame(local)
            write_seconds += time.monotonic() - step

        session = SolverSession(resolved=plan.resolved, scene=plan.scene,
                                work_directory=plan.work_directory,
                                emit=emit, cancel_event=_cancel_event,
                                frame_sink=consume)
        diagnostics = session.run()
        if not hasattr(diagnostics, "timings"):
            diagnostics.timings = {}
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
    except SessionCancelled:
        if writer is not None:
            writer.abort()
        _discard_incomplete(plan, state="cancelled",
                            reason="Bake cancelled before publication")
        _queue.put(("cancelled", None, None))
    except ClothNextError as exc:
        if writer is not None:
            writer.abort()
        _discard_incomplete(plan, state="failed", reason=str(exc))
        summary, details = _present_worker_error(plan, exc)
        _queue.put(("error", summary,
                    _record_worker_failure(plan, summary, details)))
    except Exception:  # noqa: BLE001 — surfaced as a visible ERROR state
        if writer is not None:
            writer.abort()
        _discard_incomplete(plan, state="failed",
                            reason="Unexpected Bake worker failure")
        summary = "The solver test failed unexpectedly."
        details = traceback.format_exc()
        _queue.put(("error", summary,
                    _record_worker_failure(plan, summary, details)))


def _configure_playback_modifier(modifier, frame_start: int) -> None:
    """Configure the modifier before switching it to the new cache."""
    modifier.cache_format = "PC2"
    modifier.frame_start = float(frame_start)
    modifier.interpolation = "LINEAR"
    modifier.deform_mode = "OVERWRITE"
    modifier.play_mode = "SCENE"
    modifier.forward_axis = "POS_Y"
    modifier.up_axis = "POS_Z"


_ROD_FCURVE_GROUP = "Cloth NeXt Rod Cache"


def _attach_curve_rod_playback(obj, plan: RunPlan,
                               header: pc2.Pc2Header) -> None:
    """Attach a Curve rod result without converting the artist's Curve.

    Blender's Mesh Cache modifier and shape keys cannot deform Curve
    datablocks. Control points and Bezier handles are therefore keyframed
    directly while preserving Curve bevel/material setup.
    """
    if obj.type != "CURVE":
        raise ValueError("Rod playback requires the original Curve object")
    if header.vertex_count != len(plan.initial_local):
        raise ValueError("Rod cache point count no longer matches the Curve")
    animation = getattr(obj.data, "animation_data", None)
    action = getattr(animation, "action", None)
    if action is not None:
        if not bool(action.get("cloth_next_rod_action", False)):
            raise ValueError(
                "Curve has user animation; Rod playback was not attached")
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
            raise ValueError("Curve topology changed before Rod import")
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


def _attach_playback(plan: RunPlan, header: pc2.Pc2Header) -> None:
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
    else:
        modifier = getattr(obj.modifiers, "new")(
            name=import_result.MODIFIER_NAME, type="MESH_CACHE")
        extras = []
    modifier.name = import_result.MODIFIER_NAME
    _configure_playback_modifier(modifier, plan.frame_start)
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
        # Only after the new cache is attached, drop older Cloth NeXt caches.
        for extra in extras:
            obj.modifiers.remove(extra)
        for old_path in previous_paths:
            if (old_path != plan.pc2_path
                    and old_path.name.startswith("cn_test_cloth_")):
                for target in (old_path,
                               cache_metadata.sidecar_path(old_path)):
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
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


def _pump_once() -> float | None:
    global _worker, _active_plan
    plan = _active_plan
    if plan is None:
        return None
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
                _safe_transition(
                    state, status_message=event.message,
                    current_frame=current,
                    progress_current=(current - plan.frame_start + 1
                                      if current is not None else 0),
                    progress_total=(None if event.indeterminate
                                    else total))
        elif kind == "finished":
            header, diagnostics = message[1], message[2]
            try:
                _safe_transition(BakeState.IMPORTING,
                                 status_message="Creating Blender playback cache")
                attach_step = _time.monotonic()
                _attach_playback(plan, header)
                diagnostics.timings["modifier_attach"] = (
                    _time.monotonic() - attach_step)
                shared_controller.transition(
                    BakeState.FINISHED,
                    status_message=f"Finished — {header.frame_count} frames "
                                   f"cached to {plan.pc2_path.name}",
                    progress_current=plan.frame_count,
                    progress_total=plan.frame_count,
                    current_frame=plan.frame_end,
                    frame_start=plan.frame_start,
                    frame_end=plan.frame_end)
            except (ValueError, RuntimeError, InvalidTransition) as exc:
                shared_controller.fail("Importing the solver result failed.",
                                       str(exc))
            _worker, _active_plan = None, None
            modal_lock.release()
            shared_telemetry.set_solver_pid(None)
            return None
        elif kind == "cancelled":
            _safe_transition(BakeState.CANCELLED,
                             status_message="Solver test cancelled")
            _discard_incomplete(plan)
            modal_lock.release()
            _worker, _active_plan = None, None
            shared_telemetry.set_solver_pid(None)
            return None
        elif kind == "error":
            shared_controller.fail(message[1], message[2])
            _discard_incomplete(plan)
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
    shared_controller.update(
        elapsed_seconds=_time.monotonic() - _run_started_at)
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
    global _unsubscribe
    import time as _time
    plan.pc2_path.parent.mkdir(parents=True, exist_ok=True)
    plan.work_directory.mkdir(parents=True, exist_ok=True)
    _last_work_directory = plan.work_directory
    shared_controller.transition(
        BakeState.EXPORTING, status_message="Exporting cloth mesh",
        active_object_name=plan.cloth_object_name,
        frame_start=plan.frame_start, frame_end=plan.frame_end,
        current_frame=plan.frame_start, progress_current=1,
        progress_total=plan.frame_count)
    _cancel_event.clear()
    while not _queue.empty():
        try: _queue.get_nowait()
        except queue.Empty: break
    _active_plan = plan
    _run_started_at = _time.monotonic()
    if _unsubscribe is None:
        _unsubscribe = shared_controller.subscribe(_on_controller_snapshot)
    _worker = threading.Thread(target=_worker_main, args=(plan,),
                               name="clothnext-bake-worker", daemon=False)
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
    global _pending_plan,_pending_job_id
    try:
        prefs=context.preferences.addons[__package__.partition(".blender")[0]].preferences
        auto_launch=bool(prefs.auto_launch_bake_window)
        shared_telemetry.configure(prefs.telemetry_refresh_seconds)
    except (KeyError,AttributeError):auto_launch=True
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

def begin_production_bake(context) -> tuple[str, bool]:
    """Validate and reserve production Bake without worker or modal lock."""
    global _pending_plan, _pending_job_id, _pin_capture
    if run_active() or _pending_plan is not None or _pin_capture is not None:
        raise SceneValidationError("A Cloth NeXt bake is already active.")
    # Cancellation belongs to one Bake attempt. It may still be set after a
    # previous Cancel or add-on shutdown, while animated Collider capture runs
    # before _start_prepared_run() gets a chance to clear it.
    _cancel_event.clear()
    job_id = _begin_controller(BakeJobKind.BAKE)
    try:
        # One authoritative validation for the whole Bake start: it hashes the
        # topology once and scans the pin group once. Everything downstream
        # (pin capture, run plan, fingerprints, cache check) reuses it.
        objects=tuple(getattr(getattr(context,"scene",None),"objects",()))
        snapshot=validate_scene(context) if objects else None
        if snapshot is not None:
            cloth_obj=snapshot.cloth_obj
            membership=snapshot.pin_membership
            bake_range=snapshot.bake_range
            if (membership.enabled
                    and str(getattr(cloth_obj.cloth_next,"pin_mode","STATIC"))=="FOLLOW_ANIMATION"):
                _pin_capture={"context":context,"object_name":cloth_obj.name,"membership":membership,
                    "range":bake_range,"next":bake_range.start,"samples":[],
                    "original":int(context.scene.frame_current),"job_id":job_id,
                    "snapshot":snapshot}
                _pending_job_id=job_id
                shared_controller.update(status_message="Capturing animated Pin targets",
                    activity_code=BakeActivity.CAPTURING_PIN_TARGETS,
                    progress_current=0,progress_total=bake_range.output_count)
                if not bpy.app.timers.is_registered(_pin_capture_pump):
                    bpy.app.timers.register(_pin_capture_pump,first_interval=.01)
                return job_id,True
        plan=build_run_plan(context,snapshot=snapshot)
    except (SceneValidationError, ClothNextError) as exc:
        message = exc.record.user_message if isinstance(exc, ClothNextError) else str(exc)
        shared_controller.fail(message); raise
    return _continue_production_bake(context,job_id,plan)

def _pin_capture_pump():
    global _pin_capture,_pending_job_id
    state=_pin_capture
    if state is None:return None
    context=state["context"]; scene=context.scene; obj=bpy.data.objects.get(state["object_name"])
    try:
        if obj is None:raise SceneValidationError("The Cloth object no longer exists.")
        frame=state["next"]
        with without_owned_playback(obj,lambda:_depsgraph_update(context)):
            scene.frame_set(frame); _depsgraph_update(context)
            evaluated=obj.evaluated_get(context.evaluated_depsgraph_get()); mesh=evaluated.to_mesh()
            try:
                membership=state["membership"]
                if len(mesh.vertices)!=membership.source_vertex_count:
                    raise SceneValidationError(f"Animated Pinning changed Cloth topology at frame {frame}.")
                matrix=solver_world_matrix(tuple(tuple(row) for row in evaluated.matrix_world))
                positions=tuple(_solver_position(matrix,tuple(mesh.vertices[i].co)) for i in membership.vertex_indices)
                state["samples"].append(AnimatedPinTargetSample(frame,positions))
            finally:evaluated.to_mesh_clear()
        scene.frame_set(state["original"]); _depsgraph_update(context)
        shared_controller.update(status_message=f"Capturing animated Pin targets · frame {frame} / {state['range'].end}",
            activity_code=BakeActivity.CAPTURING_PIN_TARGETS,
            progress_current=frame-state["range"].start+1)
        if frame<state["range"].end:
            state["next"]=frame+1; return .01
        job_id=state["job_id"]; samples=tuple(state["samples"]); _pin_capture=None
        # Reuses the Bake's single validation; no second topology hash or pin scan.
        plan=build_run_plan(context,animated_pin_samples=samples,
                            snapshot=state.get("snapshot"))
        _continue_production_bake(context,job_id,plan); return None
    except Exception as exc:
        try:scene.frame_set(state["original"]); _depsgraph_update(context)
        except Exception:pass
        _pin_capture=None; _pending_job_id=""; shared_controller.fail(str(exc)); return None


def _startup_pump() -> float | None:
    global _pending_plan, _pending_job_id
    plan, job_id = _pending_plan, _pending_job_id
    if plan is None or not job_id: return None
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
            context=_pin_capture["context"]
            context.scene.frame_set(_pin_capture["original"]); _depsgraph_update(context)
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
    _cancel_event.set()
    snapshot = shared_controller.snapshot()
    if snapshot.active and snapshot.state is not BakeState.CANCELLING:
        shared_controller.request_cancel()


def shutdown(join_timeout: float = 30.0) -> None:
    """Unregister/exit path: cancel, join the worker, drop the timer. The
    session's own cleanup stops the exact owned solver process and never an
    external server."""
    global _worker, _active_plan, _unsubscribe, _pending_plan, _pending_job_id, _pin_capture
    if _pending_job_id:
        companion_manager.cancel_startup(_pending_job_id, "Add-on shutdown")
    _pending_plan = None; _pending_job_id = ""; _pin_capture=None; modal_lock.release()
    _cancel_event.set()
    worker = _worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=join_timeout)
    _worker, _active_plan = None, None
    shared_telemetry.set_solver_pid(None)
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
        fps=int(scene.render.fps),
        gravity_blender=tuple(scene.gravity) if scene.use_gravity
        else (0.0, 0.0, 0.0),
        quality=snapshot.quality)
    payload = build_param_payload(
        settings, cloth_obj.name, "inspect-cloth",
        collider_obj.name, "inspect-collider",
        shell=shell, static=static, contact_enabled=contact_enabled,
        static_pin=pin_config)
    lines: list[str] = [f"Material Preset: {preset}",
                        f"Cloth: {cloth_obj.name} (SHELL)"]
    for artist_label, ppf_key, value in \
            material_formatting.shell_wire_rows(shell):
        lines.append(f"{artist_label} — PPF {ppf_key}: {value}")
    lines.append(f"Collider: {collider_obj.name} (STATIC)")
    for artist_label, ppf_key, value in \
            material_formatting.static_wire_rows(static):
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
        mode=str(getattr(cloth_obj.cloth_next,"pin_mode","STATIC"))
        lines.extend((f"Pinning: {'Follow Animation' if mode=='FOLLOW_ANIMATION' else 'Static'}", f"Group: {pin_snapshot.group_name}",
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
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if warning:
            self.report({"WARNING"}, "Real solver test started, but the Bake "
                        f"window could not be opened: {warning}")
        else:
            self.report({"INFO"}, "Real solver test started.")
        return {"FINISHED"}


class CLOTHNEXT_OT_bake(bpy.types.Operator):
    """Validate and begin the asynchronous production startup gate."""

    bl_idname = "clothnext.bake"
    bl_label = "Bake"

    @classmethod
    def poll(cls, _context):
        return (not run_active() and _pending_plan is None
                and not shared_controller.snapshot().active)

    def execute(self, context):
        try:
            _job_id, waiting = begin_production_bake(context)
        except (SceneValidationError, ClothNextError) as exc:
            message = exc.record.user_message if isinstance(exc, ClothNextError) else str(exc)
            self.report({"ERROR"}, message); return {"CANCELLED"}
        self.report({"INFO"}, "Opening Bake window…" if waiting
                    else "Cloth NeXt bake started in Blender.")
        return {"FINISHED"}


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
        self._modal_cleaned = False
        self._timer = manager.event_timer_add(.1, window=getattr(context, "window", None))
        manager.modal_handler_add(self)
        if not modal_lock.acquire(self.job_id,
                                  companion_ready_job_id=self.job_id):
            self._cleanup_modal(context); return {"CANCELLED"}
        try:
            prepare_cache_for_new_run(plan)
            shared_controller.transition(BakeState.STARTING_RUN,
                                         status_message="Starting Bake run")
            _start_prepared_run(plan)
        except (SceneValidationError, ClothNextError, OSError) as exc:
            modal_lock.release(self.job_id); self._cleanup_modal(context)
            shared_controller.fail(str(exc)); _pending_plan = None; _pending_job_id = ""
            return {"CANCELLED"}
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
                addon_show(module=__package__.partition(".blender")[0])
        except (AttributeError, RuntimeError):
            self.report({"WARNING"}, "Open Edit > Preferences > Add-ons > Cloth NeXt.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_test_cancel(bpy.types.Operator):
    """Cancel the running PPF solver test"""

    bl_idname = "clothnext.solver_test_cancel"
    bl_label = "Cancel Solver Test"
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
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        pinned = len(snapshot.pin_membership.vertex_indices)
        self.report({"INFO"}, f"Cloth NeXt scene validated · {pinned} pinned "
                              f"vertices." if snapshot.pin_membership.enabled
                    else "Cloth NeXt scene validated.")
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
           CLOTHNEXT_OT_companion_open_logs,
           CLOTHNEXT_OT_inspect_parameters)
