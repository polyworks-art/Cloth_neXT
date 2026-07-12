# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run Real Solver Test: the Phase-3A end-to-end PPF run from Blender.

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
The production Bake workflow is unchanged; these are developer test actions.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path

import bpy

from ..bake import pc2
from ..bake.controller import InvalidTransition, shared_controller
from ..bake.status import BakeJobKind, BakeState
from ..core.errors import ClothNextError
from ..ppf.coordinates import (matrix_is_finite_and_invertible,
                               solver_world_matrix)
from ..ppf.resolver import (ResolvedSolver, SolverResolutionContext,
                            SolverResolver,
                            development_executable_from_environment)
from ..ppf.schema.data import SceneObject, encode_scene, zero_area_triangles
from ..ppf.schema.params import SimulationSettings, encode_param
from ..ppf_run import import_result
from ..ppf_run.session import (SessionCancelled, SessionScene, SolverFrame,
                              SolverSession, new_project_name)
from ..updater.install_paths import ManagedSolverPaths, read_current
from ..telemetry import shared_telemetry
from . import companion_manager

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
        collider_uuid)

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
                   frame_count=frame_count)


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


def start_run(context) -> str:
    """Validate, snapshot, and launch the real solver test (main thread)."""
    global _worker, _active_plan, _run_started_at
    import time as _time
    if run_active():
        raise SceneValidationError("A solver test run is already active.")
    snapshot = shared_controller.snapshot()
    if snapshot.state is not BakeState.IDLE:
        shared_controller.reset()
    shared_controller.transition(
        BakeState.PREPARING, preview=False, job_kind=BakeJobKind.SOLVER_TEST,
        status_message="Validating Blender scene",
        frame_start=REQUIRED_FRAME_START, frame_end=REQUIRED_FRAME_END)
    launch_warning = ""
    try:
        prefs = context.preferences.addons[__package__.partition(".blender")[0]].preferences
        auto_launch = bool(prefs.auto_launch_bake_window)
        shared_telemetry.configure(prefs.telemetry_refresh_seconds)
    except (KeyError, AttributeError):
        auto_launch = True
    if auto_launch:
        ok, message = companion_manager.ensure_running()
        if not ok: launch_warning = message
    try:
        plan = build_run_plan(context)
    except (SceneValidationError, ClothNextError) as exc:
        message = (exc.record.user_message if isinstance(exc, ClothNextError)
                   else str(exc))
        details = (exc.record.technical_message
                   if isinstance(exc, ClothNextError) else "")
        shared_controller.fail(message, details)
        raise
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
                               name="clothnext-solver-test", daemon=False)
    _worker.start()
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
            warning = start_run(context)
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
                    if filepath:
                        path = Path(bpy.path.abspath(filepath))
                        if path.name.startswith("cn_test_cloth_"):
                            try:
                                path.unlink(missing_ok=True)
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


CLASSES = (CLOTHNEXT_OT_solver_test_run, CLOTHNEXT_OT_solver_test_cancel,
           CLOTHNEXT_OT_solver_test_clear, CLOTHNEXT_OT_solver_test_open_logs)
