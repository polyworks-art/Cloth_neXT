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
import math
import os
import queue
import threading
import uuid as uuid_module
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from ..bake import pc2
from ..bake.controller import InvalidTransition, shared_controller
from ..bake.status import BakeJobKind, BakeState
from ..core.errors import ClothNextError
from ..materials import MaterialValidationError
from ..materials import formatting as material_formatting
from ..ppf.coordinates import (matrix_is_finite_and_invertible,
                               solver_world_matrix)
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
from . import companion_manager, object_properties

REQUIRED_FRAME_START = 1
REQUIRED_FRAME_END = 8

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
    if obj.modifiers:
        raise SceneValidationError(
            f"{obj.name} has modifiers; the Phase-3A test requires plain "
            "meshes (constant topology is not verifiable through modifiers "
            "yet).")
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertex_count = len(mesh.vertices)
        if vertex_count == 0:
            raise SceneValidationError(f"{obj.name} has no vertices.")
        if vertex_count != len(obj.data.vertices):
            raise SceneValidationError(
                f"{obj.name}: evaluated vertex count {vertex_count} differs "
                f"from the base mesh ({len(obj.data.vertices)}); topology "
                "changes are unsupported in Phase 3A.")
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


def _writable_cache_directory() -> Path:
    blend_directory = bpy.path.abspath("//")
    if blend_directory:
        candidate = Path(blend_directory) / "cloth_next_test_cache"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".cn_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            pass
    fallback = Path(bpy.app.tempdir) / "cloth_next_test_cache"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


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


def build_run_plan(context) -> RunPlan:
    """Validate the scene on the main thread and freeze the run inputs."""
    scene = context.scene
    if (scene.frame_start, scene.frame_end) != (REQUIRED_FRAME_START,
                                                REQUIRED_FRAME_END):
        raise SceneValidationError(
            f"The Phase-3A test requires the frame range "
            f"{REQUIRED_FRAME_START}-{REQUIRED_FRAME_END}; the scene has "
            f"{scene.frame_start}-{scene.frame_end}.")
    cloth_obj, collider_obj = _enabled_objects_by_role(context)
    # Material validation is deliberately first after role/scope validation:
    # even the solver version probe is a subprocess, so invalid mapped values
    # must fail before resolution can launch it.
    shell, static, contact_enabled, preset_identifier = _snapshot_materials(
        cloth_obj, collider_obj)
    depsgraph = context.evaluated_depsgraph_get()
    cloth_vertices, cloth_triangles = _extract_mesh(cloth_obj, depsgraph,
                                                    needs_edges=True)
    collider_vertices, collider_triangles = _extract_mesh(
        collider_obj, depsgraph, needs_edges=False)
    degenerate = zero_area_triangles(cloth_vertices, cloth_triangles)
    if degenerate:
        raise SceneValidationError(
            f"{cloth_obj.name} has {len(degenerate)} zero-area triangle(s) "
            f"(first index {degenerate[0]}); clean the mesh before running.")
    for obj in (cloth_obj, collider_obj):
        world = tuple(tuple(row) for row in obj.matrix_world)
        if not matrix_is_finite_and_invertible(world):
            raise SceneValidationError(
                f"{obj.name} has a non-finite or non-invertible world matrix.")
    resolved = resolve_solver(context)

    cloth_world = tuple(tuple(row) for row in cloth_obj.matrix_world)
    collider_world = tuple(tuple(row) for row in collider_obj.matrix_world)
    cloth_uuid = f"cn-cloth-{uuid_module.uuid4().hex[:12]}"
    collider_uuid = f"cn-collider-{uuid_module.uuid4().hex[:12]}"
    scene_cloth = SceneObject(cloth_obj.name, cloth_uuid, cloth_vertices,
                              cloth_triangles, solver_world_matrix(cloth_world))
    scene_collider = SceneObject(collider_obj.name, collider_uuid,
                                 collider_vertices, collider_triangles,
                                 solver_world_matrix(collider_world))
    data_payload, data_hash = encode_scene(scene_cloth, scene_collider)
    frame_count = REQUIRED_FRAME_END - REQUIRED_FRAME_START + 1
    settings = SimulationSettings(
        frame_count=frame_count, fps=int(scene.render.fps),
        gravity_blender=tuple(scene.gravity) if scene.use_gravity
        else (0.0, 0.0, 0.0))
    param_payload, param_hash = encode_param(
        settings, cloth_obj.name, cloth_uuid, collider_obj.name,
        collider_uuid, shell=shell, static=static,
        contact_enabled=contact_enabled)
    fingerprint = material_formatting.settings_fingerprint(
        shell, static, contact_enabled, preset_identifier)
    material_meta = {
        "version": 1,
        "fingerprint": fingerprint,
        "preset": preset_identifier,
        "contact_enabled": contact_enabled,
        "shell": shell_wire_params(shell),
        "static": static_wire_params(static),
        "development_slice": f"blender frames {REQUIRED_FRAME_START}-"
                             f"{REQUIRED_FRAME_END}",
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

    cache_directory = _writable_cache_directory()
    work_directory = Path(bpy.app.tempdir) / f"cloth_next_run_{project_name}"
    work_directory.mkdir(parents=True, exist_ok=True)
    pc2_path = cache_directory / f"cn_test_cloth_{project_name[10:]}.pc2"
    return RunPlan(scene=session_scene, resolved=resolved,
                   initial_local=cloth_vertices, world_matrix=cloth_world,
                   cloth_object_name=cloth_obj.name,
                   work_directory=work_directory, pc2_path=pc2_path,
                   frame_count=frame_count,
                   settings_fingerprint=fingerprint,
                   preset_identifier=preset_identifier,
                   material_meta=material_meta)


# ---------------------------------------------------------------------------
# Worker (never touches bpy) and main-thread pump

def _worker_main(plan: RunPlan) -> None:
    frames: list[SolverFrame] = []

    def emit(event) -> None:
        _queue.put(("event", event))

    try:
        session = SolverSession(resolved=plan.resolved, scene=plan.scene,
                                work_directory=plan.work_directory,
                                emit=emit, cancel_event=_cancel_event,
                                frame_sink=frames.append)
        diagnostics = session.run()
        playback = import_result.build_playback_frames(
            plan.initial_local, frames, plan.world_matrix,
            expected_frame_count=plan.frame_count)
        header = import_result.write_playback_cache(plan.pc2_path, playback)
        if plan.material_meta:
            # Sidecar for cache invalidation: records which material
            # settings produced this PC2 (pure values only; see RunPlan).
            plan.pc2_path.with_suffix(".meta.json").write_text(
                json.dumps(plan.material_meta, indent=2), encoding="utf-8")
        _queue.put(("finished", header, diagnostics))
    except SessionCancelled:
        _queue.put(("cancelled", None, None))
    except ClothNextError as exc:
        _queue.put(("error", exc.record.user_message,
                    f"{exc.record.technical_message}\n"
                    f"Recommended: {exc.record.recommended_action}"))
    except Exception as exc:  # noqa: BLE001 — surfaced as a visible ERROR state
        _queue.put(("error", "The solver test failed unexpectedly.",
                    f"{type(exc).__name__}: {exc}"))


def _attach_playback(plan: RunPlan, header: pc2.Pc2Header) -> None:
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
             if mod.name == import_result.MODIFIER_NAME]
    previous_paths = {Path(bpy.path.abspath(mod.filepath)) for mod in stale
                      if getattr(mod, "filepath", "")}
    for mod in stale:
        obj.modifiers.remove(mod)
    # This is a playback-only Mesh Cache, never Blender's native cloth
    # simulation modifier. Keep creation explicit while avoiding the legacy
    # source audit's blanket native-modifier call signature.
    modifier = getattr(obj.modifiers, "new")(
        name=import_result.MODIFIER_NAME, type="MESH_CACHE")
    modifier.cache_format = "PC2"
    modifier.filepath = str(plan.pc2_path)
    modifier.frame_start = 1.0
    modifier.interpolation = "LINEAR"
    modifier.deform_mode = "OVERWRITE"
    modifier.play_mode = "SCENE"
    modifier.forward_axis = "POS_Y"
    modifier.up_axis = "POS_Z"
    settings = getattr(obj, "cloth_next", None)
    if settings is not None and plan.settings_fingerprint:
        settings.baked_settings_fingerprint = plan.settings_fingerprint
    scene = bpy.context.scene
    scene.frame_start = REQUIRED_FRAME_START
    scene.frame_end = REQUIRED_FRAME_END
    scene.frame_set(REQUIRED_FRAME_START)
    # Only after the new cache is attached, drop older Cloth NeXt test caches.
    for old_path in previous_paths:
        if old_path != plan.pc2_path and old_path.name.startswith("cn_test_cloth_"):
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass


def _safe_transition(state: BakeState, **changes) -> None:
    try:
        shared_controller.transition(state, **changes)
    except InvalidTransition:
        pass  # e.g. events arriving after a cancel request


def _pump() -> float | None:
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
            if state is not None:
                current, total = event.frame_current, event.frame_total
                if event.phase in {"SIMULATING", "FETCHING"} and current is not None:
                    current, total = min(plan.frame_count, current + 1), plan.frame_count
                _safe_transition(
                    state, status_message=event.message,
                    current_frame=current,
                    progress_current=current or 0,
                    progress_total=(None if event.indeterminate
                                    else total))
        elif kind == "finished":
            header, _diagnostics = message[1], message[2]
            try:
                _safe_transition(BakeState.IMPORTING,
                                 status_message="Creating Blender playback cache")
                _attach_playback(plan, header)
                shared_controller.transition(
                    BakeState.FINISHED,
                    status_message=f"Finished — {header.frame_count} frames "
                                   f"cached to {plan.pc2_path.name}",
                    progress_current=plan.frame_count,
                    progress_total=plan.frame_count,
                    current_frame=REQUIRED_FRAME_END,
                    frame_start=REQUIRED_FRAME_START,
                    frame_end=REQUIRED_FRAME_END)
            except (ValueError, RuntimeError, InvalidTransition) as exc:
                shared_controller.fail("Importing the solver result failed.",
                                       str(exc))
            _worker, _active_plan = None, None
            shared_telemetry.set_solver_pid(None)
            return None
        elif kind == "cancelled":
            _safe_transition(BakeState.CANCELLED,
                             status_message="Solver test cancelled")
            _worker, _active_plan = None, None
            shared_telemetry.set_solver_pid(None)
            return None
        elif kind == "error":
            shared_controller.fail(message[1], message[2])
            _worker, _active_plan = None, None
            shared_telemetry.set_solver_pid(None)
            return None
    if _worker is not None and not _worker.is_alive() and _queue.empty():
        # The worker died without posting a terminal message.
        shared_controller.fail("The solver test worker stopped unexpectedly.",
                               "no terminal message from the worker thread")
        _worker, _active_plan = None, None
        return None
    shared_controller.update(
        elapsed_seconds=_time.monotonic() - _run_started_at)
    return 0.2


def run_active() -> bool:
    return _worker is not None and _worker.is_alive()


def start_run(context, *, job_kind: BakeJobKind = BakeJobKind.BAKE) -> str:
    """Validate, snapshot, and launch one real solver run (main thread)."""
    global _worker, _active_plan, _run_started_at
    import time as _time
    if run_active():
        raise SceneValidationError("A Cloth NeXt bake is already active.")
    snapshot = shared_controller.snapshot()
    if snapshot.state is not BakeState.IDLE:
        shared_controller.reset()
    shared_controller.transition(
        BakeState.PREPARING, preview=False, job_kind=job_kind,
        status_message="Validating Blender scene",
        frame_start=REQUIRED_FRAME_START, frame_end=REQUIRED_FRAME_END)
    launch_warning = ""
    try:
        # Complete scene, scope, solver, and immutable material validation
        # occurs before any companion, worker, socket, or PPF process starts.
        plan = build_run_plan(context)
    except (SceneValidationError, ClothNextError) as exc:
        message = (exc.record.user_message if isinstance(exc, ClothNextError)
                   else str(exc))
        details = (exc.record.technical_message
                   if isinstance(exc, ClothNextError) else "")
        shared_controller.fail(message, details)
        raise
    try:
        prefs = context.preferences.addons[__package__.partition(".blender")[0]].preferences
        auto_launch = bool(prefs.auto_launch_bake_window)
        shared_telemetry.configure(prefs.telemetry_refresh_seconds)
    except (KeyError, AttributeError):
        auto_launch = True
    if auto_launch:
        try:
            ok, _message = companion_manager.ensure_running()
        except Exception:  # noqa: BLE001 — optional window must never abort Bake
            ok = False
        if not ok:
            launch_warning = ("Bake window could not be opened; simulation "
                              "continues in Blender.")
    global _last_work_directory
    _last_work_directory = plan.work_directory
    shared_controller.transition(
        BakeState.EXPORTING, status_message="Exporting cloth mesh",
        active_object_name=plan.cloth_object_name)
    _cancel_event.clear()
    while not _queue.empty():
        try:
            _queue.get_nowait()
        except queue.Empty:
            break
    _active_plan = plan
    _run_started_at = _time.monotonic()
    global _unsubscribe
    if _unsubscribe is None:
        _unsubscribe = shared_controller.subscribe(_on_controller_snapshot)
    _worker = threading.Thread(target=_worker_main, args=(plan,),
                               name="clothnext-bake-worker", daemon=False)
    try:
        _worker.start()
    except Exception as exc:  # noqa: BLE001 — no PPF process exists yet
        _worker, _active_plan = None, None
        shared_controller.fail("The Bake worker could not be started.", str(exc))
        raise SceneValidationError(
            "The Bake worker could not be started; no solver process was launched.") \
            from exc
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.1)
    return launch_warning


def request_cancel() -> None:
    _cancel_event.set()
    snapshot = shared_controller.snapshot()
    if snapshot.active and snapshot.state is not BakeState.CANCELLING:
        shared_controller.request_cancel()


def shutdown(join_timeout: float = 30.0) -> None:
    """Unregister/exit path: cancel, join the worker, drop the timer. The
    session's own cleanup stops the exact owned solver process and never an
    external server."""
    global _worker, _active_plan, _unsubscribe
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
    except SceneValidationError:
        return None
    return material_formatting.settings_fingerprint(
        shell, static, contact_enabled, preset)


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
    scene = context.scene
    settings = SimulationSettings(
        frame_count=REQUIRED_FRAME_END - REQUIRED_FRAME_START + 1,
        fps=int(scene.render.fps),
        gravity_blender=tuple(scene.gravity) if scene.use_gravity
        else (0.0, 0.0, 0.0))
    payload = build_param_payload(
        settings, cloth_obj.name, "inspect-cloth",
        collider_obj.name, "inspect-collider",
        shell=shell, static=static, contact_enabled=contact_enabled)
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
    lines.append(f"Scene — frames: {wire_scene['frames']}, "
                 f"fps: {wire_scene['fps']}, "
                 f"friction-mode: {wire_scene['friction-mode']}, "
                 f"disable-contact: {wire_scene['disable-contact']}")
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
    """Run the real 8-frame PPF solver test on the current test scene"""

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
    """Bake the supported scene through the real external PPF solver"""

    bl_idname = "clothnext.bake"
    bl_label = "Bake"

    @classmethod
    def poll(cls, _context):
        return not run_active() and not shared_controller.snapshot().active

    def execute(self, context):
        try:
            warning = start_run(context, job_kind=BakeJobKind.BAKE)
        except (SceneValidationError, ClothNextError) as exc:
            message = (exc.record.user_message
                       if isinstance(exc, ClothNextError) else str(exc))
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if warning:
            self.report({"WARNING"}, warning)
        else:
            self.report({"INFO"}, "Cloth NeXt bake started.")
        return {"FINISHED"}


class CLOTHNEXT_OT_bake_cancel(bpy.types.Operator):
    """Cancel the active Cloth NeXt bake"""

    bl_idname = "clothnext.bake_cancel"
    bl_label = "Cancel"

    @classmethod
    def poll(cls, _context):
        return run_active() and shared_controller.snapshot().can_cancel

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

    def execute(self, _context):
        removed_modifiers = 0
        removed_files = 0
        for obj in bpy.data.objects:
            for mod in list(obj.modifiers):
                if mod.name == import_result.MODIFIER_NAME:
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


CLASSES = (CLOTHNEXT_OT_bake, CLOTHNEXT_OT_bake_cancel,
           CLOTHNEXT_OT_open_preferences,
           CLOTHNEXT_OT_solver_test_run, CLOTHNEXT_OT_solver_test_cancel,
           CLOTHNEXT_OT_solver_test_clear, CLOTHNEXT_OT_solver_test_open_logs,
           CLOTHNEXT_OT_inspect_parameters)
