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

import json
import hashlib
import math
import os
import queue
import threading
import time
import traceback
import uuid as uuid_module
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from ..bake import pc2
from ..bake.frame_range import BakeFrameRange, BakeRangeError
from ..bake.transport import EnterBakeMode
from ..bake.controller import InvalidTransition, shared_controller
from ..bake.status import BakeActivity, BakeJobKind, BakeState
from ..core.errors import ClothNextError
from ..core.logging import get_logger, log_with_context
from ..materials import MaterialValidationError
from ..materials import formatting as material_formatting
from ..pinning import (STATIC_PIN_WEIGHT_THRESHOLD, AnimatedPinTargetSample,
                       PinMode, StaticPinError, StaticPinSnapshot,
                       static_pin_config)
from ..ppf.coordinates import (matrix_is_finite_and_invertible,
                               solver_world_matrix,
                               solver_world_to_object_local,
                               transform_points_numpy)
from ..ppf.resolver import (ResolvedSolver, SolverResolutionContext,
                            SolverResolver,
                            development_executable_from_environment)
from ..ppf.schema.data import SceneObject, encode_scene, zero_area_triangles
from ..ppf.schema.params import (SimulationSettings, build_param_payload,
                                 encode_param, shell_wire_params,
                                 static_wire_params)
from ..ppf_run import import_result
from ..ppf_run.session import (SessionCancelled, SessionScene, SolverFrame,
                              SolverSession, new_project_name)
from ..updater.install_paths import ManagedSolverPaths, read_current
from ..telemetry import shared_telemetry
from . import companion_manager, modal_lock, object_properties
from .playback_cache import (is_cloth_next_playback_modifier,
                             mark_owned_playback, without_owned_playback)

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
    # Immutable pure material snapshot metadata (Phase 3B): the fingerprint
    # marks the finished result, and the JSON-safe meta dict is written next
    # to the PC2 cache so a stale result stays detectable.
    settings_fingerprint: str = ""
    preset_identifier: str = ""
    material_meta: dict = field(default_factory=dict)


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
        if settings.role == "CLOTH":
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
        shell = object_properties.shell_settings_from(cloth_obj.cloth_next)
    except MaterialValidationError as exc:
        raise SceneValidationError(
            f"{cloth_obj.name}: invalid material value — {exc}") from exc
    try:
        static = object_properties.static_settings_from(
            collider_obj.cloth_next)
    except MaterialValidationError as exc:
        raise SceneValidationError(
            f"{collider_obj.name}: invalid contact value — {exc}") from exc
    contact_enabled = bool(cloth_obj.cloth_next.collision.enabled)
    preset_identifier = str(cloth_obj.cloth_next.material.preset)
    return shell, static, contact_enabled, preset_identifier


def _snapshot_static_pin(cloth_obj) -> StaticPinSnapshot:
    """Capture binary vertex-group membership on Blender's main thread."""
    settings = cloth_obj.cloth_next
    enabled = bool(getattr(settings, "pinning_enabled", False))
    group_name = str(getattr(settings, "pin_group", "") or "")
    vertex_count = len(getattr(getattr(cloth_obj, "data", None), "vertices", ()))
    object_id = str(getattr(cloth_obj, "name_full",
                            getattr(cloth_obj, "name", "")))
    mesh = getattr(cloth_obj, "data", None)
    topology_record = {
        "vertices": vertex_count,
        "edges": [tuple(int(i) for i in edge.vertices)
                  for edge in getattr(mesh, "edges", ())],
        "polygons": [tuple(int(i) for i in polygon.vertices)
                     for polygon in getattr(mesh, "polygons", ())],
    }
    topology_signature = hashlib.sha256(json.dumps(
        topology_record, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if not enabled:
        return StaticPinSnapshot(False, group_name, object_id, vertex_count, (),
                                 source_topology_signature=topology_signature)
    if not group_name:
        raise SceneValidationError("Select a Pin Group.")
    groups = getattr(cloth_obj, "vertex_groups", None)
    group = groups.get(group_name) if groups is not None else None
    if group is None:
        raise SceneValidationError("The selected Pin Group no longer exists.")
    group_index = int(group.index)
    indices = []
    for vertex in cloth_obj.data.vertices:
        for membership in vertex.groups:
            if (int(membership.group) == group_index
                    and float(membership.weight) > STATIC_PIN_WEIGHT_THRESHOLD):
                indices.append(int(vertex.index))
                break
    try:
        return StaticPinSnapshot(True, group_name, object_id, vertex_count,
                                 tuple(indices),
                                 source_topology_signature=topology_signature)
    except StaticPinError as exc:
        raise SceneValidationError(str(exc)) from exc

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


def build_run_plan(context, *, animated_pin_samples=None) -> RunPlan:
    """Validate the scene on the main thread and freeze the run inputs."""
    scene = context.scene
    cloth_obj, collider_obj = _enabled_objects_by_role(context)
    try:
        bake_range = BakeFrameRange(int(cloth_obj.cloth_next.bake_start),
                                    int(cloth_obj.cloth_next.bake_end))
    except (BakeRangeError, TypeError, ValueError) as exc:
        raise SceneValidationError(str(exc)) from exc
    if (getattr(collider_obj, "animation_data", None) is not None
            or len(getattr(collider_obj, "constraints", ())) > 0):
        raise SceneValidationError("Animated colliders are not supported yet.")
    # Material validation is deliberately first after role/scope validation:
    # even the solver version probe is a subprocess, so invalid mapped values
    # must fail before resolution can launch it.
    shell, static, contact_enabled, preset_identifier = _snapshot_materials(
        cloth_obj, collider_obj)
    pin_membership = _snapshot_static_pin(cloth_obj)
    modifiers = tuple(getattr(cloth_obj, "modifiers", ()))
    relevant_modifiers=tuple(mod for mod in modifiers
                             if not is_cloth_next_playback_modifier(cloth_obj,mod))
    if relevant_modifiers and not pin_membership.enabled:
        raise SceneValidationError(
            f"{cloth_obj.name} has modifiers; the current unpinned solver "
            "slice requires a plain mesh.")
    original_frame = int(scene.frame_current)
    try:
        with without_owned_playback(cloth_obj,lambda:_depsgraph_update(context)):
            scene.frame_set(bake_range.start); _depsgraph_update(context)
            depsgraph = context.evaluated_depsgraph_get()
            cloth_vertices, cloth_triangles = _extract_mesh(
                cloth_obj, depsgraph, needs_edges=True)
            pin_snapshot=_capture_animated_pin(context,cloth_obj,bake_range,
                                               pin_membership,animated_pin_samples)
        collider_vertices, collider_triangles = _extract_mesh(
            collider_obj, depsgraph, needs_edges=False)
        cloth_world = tuple(tuple(row) for row in cloth_obj.matrix_world)
        collider_world = tuple(tuple(row) for row in collider_obj.matrix_world)
    finally:
        scene.frame_set(original_frame)
    degenerate = zero_area_triangles(cloth_vertices, cloth_triangles)
    if degenerate:
        raise SceneValidationError(
            f"{cloth_obj.name} has {len(degenerate)} zero-area triangle(s) "
            f"(first index {degenerate[0]}); clean the mesh before running.")
    for obj, world in ((cloth_obj, cloth_world),
                       (collider_obj, collider_world)):
        if not matrix_is_finite_and_invertible(world):
            raise SceneValidationError(
                f"{obj.name} has a non-finite or non-invertible world matrix.")
    if pin_snapshot.enabled and len(cloth_vertices) != pin_snapshot.source_vertex_count:
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
    pin_config = static_pin_config(pin_snapshot)
    resolved = resolve_solver(context)

    cloth_uuid = f"cn-cloth-{uuid_module.uuid4().hex[:12]}"
    collider_uuid = f"cn-collider-{uuid_module.uuid4().hex[:12]}"
    scene_cloth = SceneObject(cloth_obj.name, cloth_uuid, cloth_vertices,
                              cloth_triangles, solver_world_matrix(cloth_world),
                              pin_snapshot.vertex_indices)
    scene_collider = SceneObject(collider_obj.name, collider_uuid,
                                 collider_vertices, collider_triangles,
                                 solver_world_matrix(collider_world))
    data_payload, data_hash = encode_scene(scene_cloth, scene_collider)
    frame_count = bake_range.output_count
    try:
        quality = object_properties.solver_quality_from(scene)
    except ValueError as exc:
        raise SceneValidationError(str(exc)) from exc
    settings = SimulationSettings(
        frame_count=frame_count, fps=int(scene.render.fps),
        gravity_blender=tuple(scene.gravity) if scene.use_gravity
        else (0.0, 0.0, 0.0), quality=quality)
    param_payload, param_hash = encode_param(
        settings, cloth_obj.name, cloth_uuid, collider_obj.name,
        collider_uuid, shell=shell, static=static,
        contact_enabled=contact_enabled, static_pin=pin_config)
    fingerprint = material_formatting.settings_fingerprint(
        shell, static, contact_enabled, preset_identifier,
        bake_start=bake_range.start, bake_end=bake_range.end,
        pinning_fingerprint=pin_snapshot.fingerprint, quality=quality)
    material_meta = {
        "version": 1,
        "fingerprint": fingerprint,
        "preset": preset_identifier,
        "contact_enabled": contact_enabled,
        "shell": shell_wire_params(shell),
        "static": static_wire_params(static),
        "quality": {
            "dt": settings.quality.time_step,
            "min-newton-steps": settings.quality.min_newton_steps,
            "cg-max-iter": settings.quality.cg_max_iter,
            "cg-tol": settings.quality.cg_tol,
        },
        "pressure": {"enabled": shell.enable_inflate,
                     "stored": shell.inflate_pressure,
                     "wire": shell_wire_params(shell)["pressure"]},
        "blender_start_frame": bake_range.start,
        "blender_end_frame": bake_range.end,
        "output_frame_count": frame_count,
        "solver_step_count": bake_range.solver_steps,
        "fps": int(scene.render.fps),
        "pc2_sample_count": frame_count,
        "completion_state": "complete",
        "pinning": {
            "enabled": pin_snapshot.enabled,
            "mode": pin_snapshot.mode.value,
            "group": pin_snapshot.group_name,
            "count": len(pin_snapshot.vertex_indices),
            "threshold": pin_snapshot.threshold,
            "fingerprint": pin_snapshot.fingerprint,
        },
    }

    project_name = new_project_name()
    session_scene = SessionScene(
        project_name=project_name,
        cloth_name=cloth_obj.name, cloth_uuid=cloth_uuid,
        cloth_vertex_count=len(cloth_vertices),
        collider_name=collider_obj.name, collider_uuid=collider_uuid,
        frame_count=frame_count,
        data_payload=data_payload, param_payload=param_payload,
        data_hash=data_hash, param_hash=param_hash)

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
                   settings_fingerprint=fingerprint,
                   preset_identifier=preset_identifier,
                   material_meta=material_meta)


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


def _discard_incomplete(plan: RunPlan | None) -> None:
    if plan is None:
        return
    for target in (plan.pc2_path, plan.pc2_path.with_suffix(".meta.json")):
        if _is_within(target, plan.pc2_path.parent):
            try: target.unlink(missing_ok=True)
            except OSError: pass

def _worker_main(plan: RunPlan) -> None:
    def emit(event) -> None:
        _queue.put(("event", event))

    writer = None
    try:
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
                "message": (f"Preparing playback data · frame "
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
            emit(type("CacheEvent", (), {
                "phase": "WRITING_CACHE",
                "message": (f"Writing playback cache · frame "
                            f"{frame.solver_frame + 1} / {plan.frame_count}"),
                "frame_current": frame.solver_frame,
                "frame_total": plan.frame_count,
                "indeterminate": False,
            })())
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
        diagnostics.timings["pc2_fstat"] = writer.fstat_seconds
        diagnostics.timings["pc2_fsync"] = writer.fsync_seconds
        diagnostics.timings["pc2_close"] = writer.close_seconds
        diagnostics.timings["pc2_replace"] = writer.replace_seconds
        diagnostics.timings["pc2_validation"] = writer.validation_seconds
        diagnostics.timings["total"] = time.monotonic() - _run_started_at
        if plan.material_meta:
            sidecar_step = time.monotonic()
            metadata = dict(plan.material_meta)
            metadata.update({"cache_format": "POINTCACHE2",
                "vertex_count": header.vertex_count, "frame_count": header.frame_count,
                "expected_bytes": writer.expected_size,
                "actual_bytes": plan.pc2_path.stat().st_size,
                "writer_version": pc2.PC2_WRITER_VERSION,
                "timings": dict(diagnostics.timings),
                "solver_mode": diagnostics.solver_mode,
                "solver_release_identity": diagnostics.package_version or "unknown"})
            sidecar = plan.pc2_path.with_suffix(".meta.json")
            temporary = sidecar.with_name(f".{sidecar.name}.{uuid_module.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            os.replace(temporary, sidecar)
            diagnostics.timings["sidecar_write"] = time.monotonic() - sidecar_step
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
        _discard_incomplete(plan)
        _queue.put(("cancelled", None, None))
    except ClothNextError as exc:
        if writer is not None:
            writer.abort()
        _discard_incomplete(plan)
        _queue.put(("error", exc.record.user_message,
                    f"{exc.record.technical_message}\n"
                    f"Recommended: {exc.record.recommended_action}"))
    except Exception as exc:  # noqa: BLE001 — surfaced as a visible ERROR state
        if writer is not None:
            writer.abort()
        _discard_incomplete(plan)
        _queue.put(("error", "The solver test failed unexpectedly.",
                    f"{type(exc).__name__}: {exc}"))


def _attach_playback(plan: RunPlan, header: pc2.Pc2Header) -> dict[str, float]:
    timings: dict[str, float] = {}

    def measured(label: str, operation):
        started = time.perf_counter()
        result = operation()
        timings[label] = time.perf_counter() - started
        return result

    verified = pc2.read_header(plan.pc2_path)
    if verified != header:
        raise ValueError("PC2 file changed between write and attach")
    if verified.vertex_count != len(plan.initial_local):
        raise ValueError("PC2 vertex count does not match the cloth")
    if verified.frame_count != plan.frame_count:
        raise ValueError("PC2 frame count is not the requested range")
    obj = bpy.data.objects.get(plan.cloth_object_name)
    if obj is None:
        raise ValueError(f"cloth object {plan.cloth_object_name!r} no longer "
                         "exists")
    stale = [mod for mod in obj.modifiers
             if is_cloth_next_playback_modifier(obj,mod)]
    previous_paths = {Path(bpy.path.abspath(mod.filepath)) for mod in stale
                      if getattr(mod, "filepath", "")}
    # Reuse the active Cloth NeXt modifier. Removing it and constructing a new
    # one forces expensive dependency-graph rebuilding in real production
    # scenes. Configure inactive properties first and switch the filepath last;
    # that single assignment is the atomic handoff from the old valid cache.
    if stale:
        modifier = stale[0]
        extras = stale[1:]
    else:
        modifier = measured("modifier_create", lambda: getattr(
            obj.modifiers, "new")(name=import_result.MODIFIER_NAME,
                                   type="MESH_CACHE"))
        extras = []
    modifier.name = import_result.MODIFIER_NAME
    measured("modifier_settings", lambda: _configure_playback_modifier(
        modifier, plan.frame_start))
    measured("modifier_filepath", lambda: setattr(
        modifier, "filepath", str(plan.pc2_path)))
    measured("modifier_ownership", lambda: mark_owned_playback(
        obj, modifier, str(plan.pc2_path)))
    settings = getattr(obj, "cloth_next", None)
    if settings is not None and plan.settings_fingerprint:
        settings.baked_settings_fingerprint = plan.settings_fingerprint
    # Only after the new cache is attached, drop older Cloth NeXt test caches.
    for extra in extras:
        measured("extra_modifier_cleanup", lambda extra=extra:
                 obj.modifiers.remove(extra))
    cleanup_started = time.perf_counter()
    for old_path in previous_paths:
        if old_path != plan.pc2_path and old_path.name.startswith("cn_test_cloth_"):
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass
    timings["old_cache_cleanup"] = time.perf_counter() - cleanup_started
    return timings


def _configure_playback_modifier(modifier, frame_start: int) -> None:
    """Configure without activating a cache path; called on Blender's main thread."""
    modifier.cache_format = "PC2"
    # PC2 sample zero is the uploaded initial state at Blender Bake Start.
    modifier.frame_start = float(frame_start)
    modifier.interpolation = "LINEAR"
    modifier.deform_mode = "OVERWRITE"
    modifier.play_mode = "SCENE"
    modifier.forward_axis = "POS_Y"
    modifier.up_axis = "POS_Z"


def _publish_attach_timings(plan: RunPlan, timings: dict[str, float]) -> None:
    """Atomically add main-thread attach measurements to the sidecar."""
    sidecar = plan.pc2_path.with_suffix(".meta.json")
    if not sidecar.is_file():
        return
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    recorded = metadata.get("timings")
    if not isinstance(recorded, dict):
        recorded = {}
        metadata["timings"] = recorded
    recorded.update(timings)
    temporary = sidecar.with_name(
        f".{sidecar.name}.{uuid_module.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        os.replace(temporary, sidecar)
    finally:
        temporary.unlink(missing_ok=True)


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
                attach_timings = _attach_playback(plan, header)
                diagnostics.timings["modifier_attach"] = (
                    _time.monotonic() - attach_step)
                diagnostics.timings.update(attach_timings)
                _publish_attach_timings(plan, diagnostics.timings)
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
    """Make timer failures terminal instead of leaving stale active UI."""
    global _worker, _active_plan
    try:
        shared_controller.fail(
            "Importing the solver result failed.", details)
    except Exception:
        # Controller listeners are third-party boundaries too. Cleanup must
        # still happen if one of them caused the original timer failure.
        pass
    modal_lock.release()
    shared_telemetry.set_solver_pid(None)
    _worker, _active_plan = None, None


def _pump() -> float | None:
    """Exception boundary required by Blender's timer API.

    Blender silently unregisters a timer callback that raises. Without this
    boundary all three UIs retain the last IMPORTING snapshot forever even
    though the worker and PC2 writer have completed.
    """
    try:
        return _pump_once()
    except Exception:  # noqa: BLE001 -- surface the complete Blender traceback
        _abort_failed_pump(traceback.format_exc())
        return None


def _pump_watchdog() -> float | None:
    """Restore the result pump if Blender removed it during an active run."""
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
    job_id = _begin_controller(BakeJobKind.BAKE)
    try:
        objects=tuple(getattr(getattr(context,"scene",None),"objects",()))
        cloth_obj=None
        if objects:cloth_obj,_=_enabled_objects_by_role(context)
        membership=_snapshot_static_pin(cloth_obj) if cloth_obj is not None else None
        bake_range=(BakeFrameRange(int(cloth_obj.cloth_next.bake_start),int(cloth_obj.cloth_next.bake_end))
                    if cloth_obj is not None else None)
        if (membership is not None and membership.enabled
                and str(getattr(cloth_obj.cloth_next,"pin_mode","STATIC"))=="FOLLOW_ANIMATION"):
            _pin_capture={"context":context,"object_name":cloth_obj.name,"membership":membership,
                "range":bake_range,"next":bake_range.start,"samples":[],
                "original":int(context.scene.frame_current),"job_id":job_id}
            _pending_job_id=job_id
            shared_controller.update(status_message="Capturing animated Pin targets",
                activity_code=BakeActivity.CAPTURING_PIN_TARGETS,
                progress_current=0,progress_total=bake_range.output_count)
            if not bpy.app.timers.is_registered(_pin_capture_pump):
                bpy.app.timers.register(_pin_capture_pump,first_interval=.01)
            return job_id,True
        plan=build_run_plan(context)
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
        plan=build_run_plan(context,animated_pin_samples=samples)
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
    """Fingerprint of the current one-cloth/one-collider material state.

    Returns ``None`` when the scene does not hold exactly one enabled
    cloth and collider or a value is invalid — never raises from draw.
    """
    try:
        cloth_obj, collider_obj = _enabled_objects_by_role(context)
        shell, static, contact_enabled, preset = _snapshot_materials(
            cloth_obj, collider_obj)
        pin_snapshot=_snapshot_static_pin(cloth_obj)
        quality=object_properties.solver_quality_from(context.scene)
        pin_mode=str(getattr(cloth_obj.cloth_next,"pin_mode","STATIC"))
        pin_fingerprint=hashlib.sha256(
            f"{pin_snapshot.fingerprint}\0{pin_mode}".encode("utf-8")).hexdigest()
    except (SceneValidationError, ValueError):
        return None
    return material_formatting.settings_fingerprint(
        shell, static, contact_enabled, preset,
        bake_start=int(cloth_obj.cloth_next.bake_start),
        bake_end=int(cloth_obj.cloth_next.bake_end),
        pinning_fingerprint=pin_fingerprint, quality=quality)


def build_parameter_inspection(context) -> tuple[tuple[str, ...], dict]:
    """Validate the current settings and build the exact Param payload
    without starting PPF.

    Returns human-readable summary lines (artist and wire names) plus the
    JSON-safe payload dictionary. Contains no mesh data, no secrets, and no
    binary CBOR; placeholder UUIDs stand in for the per-run random ones.
    """
    cloth_obj, collider_obj = _enabled_objects_by_role(context)
    shell, static, contact_enabled, preset = _snapshot_materials(
        cloth_obj, collider_obj)
    pin_snapshot = _snapshot_static_pin(cloth_obj)
    pin_config = static_pin_config(pin_snapshot)
    scene = context.scene
    try:
        bake_range = BakeFrameRange(int(cloth_obj.cloth_next.bake_start),
                                    int(cloth_obj.cloth_next.bake_end))
    except BakeRangeError as exc:
        raise SceneValidationError(str(exc)) from exc
    settings = SimulationSettings(
        frame_count=bake_range.output_count,
        fps=int(scene.render.fps),
        gravity_blender=tuple(scene.gravity) if scene.use_gravity
        else (0.0, 0.0, 0.0),
        quality=object_properties.solver_quality_from(scene))
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
            for mod in list(obj.modifiers):
                if is_cloth_next_playback_modifier(obj,mod):
                    filepath = getattr(mod, "filepath", "")
                    obj.modifiers.remove(mod)
                    removed_modifiers += 1
                    settings = getattr(obj, "cloth_next", None)
                    if settings is not None:
                        settings.baked_settings_fingerprint = ""
                    if filepath:
                        path = Path(bpy.path.abspath(filepath))
                        if path.name.startswith("cn_test_cloth_"):
                            try:
                                path.unlink(missing_ok=True)
                                path.with_suffix(".meta.json").unlink(
                                    missing_ok=True)
                                removed_files += 1
                            except OSError:
                                pass
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


CLASSES = (CLOTHNEXT_OT_bake, CLOTHNEXT_OT_bake_modal,
           CLOTHNEXT_OT_bake_cancel,
           CLOTHNEXT_OT_open_preferences,
           CLOTHNEXT_OT_solver_test_run, CLOTHNEXT_OT_solver_test_cancel,
           CLOTHNEXT_OT_solver_test_clear, CLOTHNEXT_OT_solver_test_open_logs,
           CLOTHNEXT_OT_companion_open_logs,
           CLOTHNEXT_OT_inspect_parameters)
