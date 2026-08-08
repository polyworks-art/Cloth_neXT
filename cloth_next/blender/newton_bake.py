# SPDX-License-Identifier: GPL-3.0-or-later
"""Newton offline Bake using the common Bake window and PC2 playback path."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import uuid

import bpy
import numpy as np

from .. import manifest_version
from ..bake import cache_metadata, pc2
from ..bake.controller import shared_controller
from ..bake.status import BakeState
from ..bake.transport import EnterBakeMode
from ..newton_preview.client import NewtonWorkerClient
from ..newton_preview.artifacts import (prune_owned_sessions,
                                        remove_owned_session)
from ..newton_preview.request_artifact import write_request_artifact
from ..newton_preview.contracts import (ColliderAnimation, NEWTON_VERSION,
                                        PROTOCOL_VERSION, PinAnimation, PreviewCloth,
                                        PreviewCreateRequest, PreviewResult,
                                        PreviewRigidBody, PreviewSoftBody,
                                        WARP_VERSION)
from . import companion_manager, modal_lock, newton_preview, object_properties

_PUMP_INTERVAL = 0.05


@dataclass
class _BakeTarget:
    source_name: str
    source_uuid: str
    source_local_vertices: tuple
    inverse_world: tuple
    pc2_path: Path
    metadata: dict
    role: str


@dataclass
class _BakeSession:
    request: PreviewCreateRequest
    targets: tuple[_BakeTarget, ...]
    cancel_event: threading.Event
    messages: queue.Queue
    worker: threading.Thread | None = None
    client: NewtonWorkerClient | None = None


_session: _BakeSession | None = None
_capture_iterator = None
_capture_cancel_event = None


def active() -> bool:
    return _session is not None


def _bake_quality(scene, dynamics):
    """Resolve product quality through Newton-native preset values."""
    from ..newton_preview.contracts import PreviewQuality
    settings = getattr(scene, "cloth_next_solver", None)
    preset = str(getattr(settings, "quality_preset", "HIGH") or "HIGH")
    presets = {"LOW": (2, 4), "MEDIUM": (4, 8),
               "HIGH": (8, 12), "EXTREME": (16, 20)}
    if preset == "CUSTOM":
        substeps = int(getattr(settings, "newton_substeps", 8))
        iterations = int(getattr(settings, "newton_iterations", 12))
    else:
        substeps, iterations = presets.get(preset, presets["HIGH"])
    self_contact = all(bool(obj.cloth_next.collision.enabled)
                       for obj in dynamics)
    return PreviewQuality(
        preset, substeps, iterations, 10, 12, self_contact)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _capture_steps(context):
    scene = context.scene
    enabled = newton_preview._enabled_objects(scene)
    cloths = tuple(sorted(
        (obj for obj in enabled if str(obj.cloth_next.role) == "CLOTH"),
        key=lambda obj: (str(obj.cloth_next.persistent_export_id), str(obj.name))))
    soft_objects = tuple(sorted(
        (obj for obj in enabled if str(obj.cloth_next.role) == "SOFT_BODY"),
        key=lambda obj: (str(obj.cloth_next.persistent_export_id), str(obj.name))))
    rigid_objects = tuple(sorted(
        (obj for obj in enabled if str(obj.cloth_next.role) == "RIGID_BODY"),
        key=lambda obj: (str(obj.cloth_next.persistent_export_id), str(obj.name))))
    colliders = tuple(sorted(
        (obj for obj in enabled if str(obj.cloth_next.role) == "COLLIDER"),
        key=lambda obj: (str(obj.cloth_next.persistent_export_id), str(obj.name))))
    unsupported = tuple(obj for obj in enabled if str(obj.cloth_next.role)
                        not in {"CLOTH", "SOFT_BODY", "RIGID_BODY", "COLLIDER", "FORCE"})
    dynamics = (*cloths, *soft_objects, *rigid_objects)
    if not dynamics:
        raise ValueError("Newton Bake requires Cloth, Soft Body, or Rigid Body objects.")
    if unsupported:
        raise ValueError("Newton Bake does not support: " + ", ".join(
            str(obj.cloth_next.role) for obj in unsupported))
    ranges = {(int(obj.cloth_next.bake_start), int(obj.cloth_next.bake_end))
              for obj in dynamics}
    if len(ranges) != 1:
        raise ValueError("Use the same Bake range on all Newton dynamic objects.")
    for cloth in cloths:
        if bool(cloth.cloth_next.pressure.enable_inflate):
            raise ValueError(f"{cloth.name}: Newton does not support Pressure")
        if bool(cloth.cloth_next.pressure.sewing_enabled):
            raise ValueError(f"{cloth.name}: Newton does not support Sewing")
    configured = tuple(str(obj.cloth_next.cache_directory or "").strip()
                       for obj in dynamics)
    for obj, directory in zip(dynamics, configured):
        if not directory:
            raise ValueError(
                f"Set a Cache Directory for {obj.name} before baking with Newton.")
    original_frame = int(scene.frame_current)
    start, end = next(iter(ranges))
    try:
        scene.frame_set(start)
        dynamic_meshes_list = []
        for index, obj in enumerate(dynamics):
            dynamic_meshes_list.append(
                newton_preview._cached_triangulated_world_mesh(context, obj))
            yield ("progress", f"Preparing {obj.name}", index + 1,
                   len(dynamics) + len(colliders))
        dynamic_meshes = tuple(dynamic_meshes_list)
        cloth_meshes = dynamic_meshes[:len(cloths)]
        soft_meshes = dynamic_meshes[len(cloths):len(cloths) + len(soft_objects)]
        rigid_meshes = dynamic_meshes[len(cloths) + len(soft_objects):]
        pin_sets = tuple(newton_preview._pin_indices(cloth, len(mesh.vertices))
                         for cloth, mesh in zip(cloths, cloth_meshes))
        collider_meshes_list = []
        for index, obj in enumerate(colliders):
            collider_meshes_list.append(
                newton_preview._cached_triangulated_world_mesh(context, obj))
            yield ("progress", f"Preparing Collider {obj.name}",
                   len(dynamics) + index + 1,
                   len(dynamics) + len(colliders))
        collider_meshes = tuple(collider_meshes_list)
        inverse_arrays, local_sets = [], []
        for obj, dynamic_mesh in zip(dynamics, dynamic_meshes):
            matrix = np.asarray(tuple(tuple(float(value) for value in row)
                                      for row in obj.matrix_world),
                                dtype=np.float64)
            inverse_array = np.linalg.inv(matrix)
            world = np.asarray(dynamic_mesh.vertices, dtype=np.float64)
            homogeneous = np.concatenate(
                (world, np.ones((len(world), 1), dtype=np.float64)), axis=1)
            local = homogeneous @ inverse_array.T
            local_sets.append(tuple(tuple(float(value) for value in row[:3] / row[3])
                                    for row in local))
            inverse_arrays.append(inverse_array)
        collider_animations = []
        pin_animations = []
        for collider_index, collider in enumerate(colliders):
            if str(collider.cloth_next.collider_motion) != "ANIMATED":
                continue
            reference = collider_meshes[collider_index]
            samples = []
            reference_topology = None
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                sample, topology = newton_preview._evaluated_world_mesh_data(
                    context, collider)
                if reference_topology is None:
                    reference_topology = topology
                if (topology != reference_topology
                        or len(sample.vertices) != len(reference.vertices)):
                    raise ValueError(
                        f"{collider.name}: animated Collider topology must remain constant")
                samples.append(sample.vertices)
                yield ("animation", f"Sampling {collider.name} · Frame {frame}",
                       frame - start + 1, end - start + 1)
            collider_animations.append(ColliderAnimation(
                collider_index, tuple(samples)))
        for cloth_index, (cloth, reference, pins) in enumerate(
                zip(cloths, cloth_meshes, pin_sets)):
            if (not pins
                    or str(cloth.cloth_next.pin_mode) != "FOLLOW_ANIMATION"):
                continue
            samples = []
            reference_topology = None
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                sample, topology = newton_preview._evaluated_world_mesh_data(
                    context, cloth)
                if reference_topology is None:
                    reference_topology = topology
                if (topology != reference_topology
                        or len(sample.vertices) != len(reference.vertices)):
                    raise ValueError(
                        f"{cloth.name}: animated Pin topology must remain constant")
                samples.append(tuple(sample.vertices[index] for index in pins))
                yield ("animation", f"Sampling Pins · {cloth.name} · Frame {frame}",
                       frame - start + 1, end - start + 1)
            pin_animations.append(PinAnimation(cloth_index, tuple(samples)))
    finally:
        scene.frame_set(original_frame)
    quality = _bake_quality(scene, dynamics)
    materials = tuple(newton_preview._material(cloth) for cloth in cloths)
    soft_materials = tuple(object_properties.soft_body_settings_from(
        obj.cloth_next) for obj in soft_objects)
    rigid_materials = tuple(object_properties.rigid_body_settings_from(
        obj.cloth_next) for obj in rigid_objects)
    for obj, material in zip(soft_objects, soft_materials):
        if material.tetrahedralizer != "ftetwild":
            raise ValueError(
                f"{obj.name}: Newton Soft Bodies require the fTetWild tetrahedralizer.")
        if not math.isclose(material.volume_scale, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(
                f"{obj.name}: Newton does not support Rest Volume Scale; use 1.0.")
    identity = _hash({
        "schema": 3, "scene": str(scene.name),
        "dynamics": [(str(obj.cloth_next.persistent_export_id),
                      str(obj.cloth_next.role), mesh.vertices, mesh.triangles)
                     for obj, mesh in zip(dynamics, dynamic_meshes)],
        "colliders": [(mesh.vertices, mesh.triangles) for mesh in collider_meshes],
        "cloth_materials": [item.__dict__ for item in materials],
        "soft_materials": [asdict(item) for item in soft_materials],
        "rigid_materials": [asdict(item) for item in rigid_materials],
        "quality": quality.__dict__,
        "collider_animations": [item.__dict__ for item in collider_animations]})
    session_id = uuid.uuid4().hex
    app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    session_root = app_data / "ClothNeXt" / "newton" / "sessions" / session_id
    try:
        prune_owned_sessions(session_root.parent, keep=20,
                             exclude=(session_id,))
    except OSError:
        pass  # a stale diagnostic directory must not block a new Bake
    primary_mesh = cloth_meshes[0] if cloth_meshes else None
    primary_pins = pin_sets[0] if pin_sets else ()
    primary_material = materials[0] if materials else None
    soft_bodies = tuple(PreviewSoftBody(
        str(obj.cloth_next.persistent_export_id), mesh,
        material.volume_density, max(1.0e-6, material.stretch_resistance),
        material.poisson_ratio, material.shape_damping,
        material.surface_grip,
        material.collision_gap + material.surface_offset,
        max(1.0e-5, material.collision_gap + material.surface_offset),
        {"LOW": 0.2, "MEDIUM": 0.12, "HIGH": 0.08,
         "EXTREME": 0.05}.get(quality.name, 0.08))
        for obj, mesh, material in zip(soft_objects, soft_meshes, soft_materials))
    rigid_bodies = tuple(PreviewRigidBody(
        str(obj.cloth_next.persistent_export_id), mesh,
        material.volume_density, material.surface_grip,
        material.collision_gap + material.surface_offset)
        for obj, mesh, material in zip(rigid_objects, rigid_meshes, rigid_materials))
    request = PreviewCreateRequest(
        session_id, identity, primary_mesh, collider_meshes, primary_pins,
        primary_material, quality, start, end,
        float(scene.render.fps) / float(scene.render.fps_base),
        1.0, newton_preview._gravity(scene),
        str(session_root / "results"),
        additional_cloths=tuple(PreviewCloth(
            str(cloth.cloth_next.persistent_export_id), mesh, pins, material)
            for cloth, mesh, pins, material in zip(
                cloths[1:], cloth_meshes[1:], pin_sets[1:], materials[1:])),
        collider_animations=tuple(collider_animations),
        pin_animations=tuple(pin_animations), soft_bodies=soft_bodies,
        rigid_bodies=rigid_bodies, solver="VBD")
    request.validate()
    settings_value = {
        "backend": "NEWTON", "newton": NEWTON_VERSION, "warp": WARP_VERSION,
        "protocol": PROTOCOL_VERSION, "solver": request.solver,
        "materials": [material.__dict__ for material in materials],
        "soft_materials": [asdict(material) for material in soft_materials],
        "rigid_materials": [asdict(material) for material in rigid_materials],
        "quality": quality.__dict__,
        "frame_start": request.frame_start, "frame_end": request.frame_end,
        "fps": request.fps, "time_scale": request.time_scale,
        "gravity": request.gravity,
    }
    frame_count = request.frame_end - request.frame_start + 1
    targets = []
    for dynamic_index, (obj, mesh, local_vertices, inverse_array,
                        directory) in enumerate(zip(
            dynamics, dynamic_meshes, local_sets, inverse_arrays, configured)):
        role = str(obj.cloth_next.role)
        pins = pin_sets[dynamic_index] if dynamic_index < len(pin_sets) else ()
        geometry_value = {
            "backend_schema": 3,
            "object_uuid": str(obj.cloth_next.persistent_export_id),
            "role": role, "vertices": mesh.vertices,
            "triangles": mesh.triangles, "pins": pins,
            "colliders": [{"vertices": item.vertices, "triangles": item.triangles}
                          for item in collider_meshes],
            "collider_animations": [item.__dict__ for item in collider_animations],
            "pin_animations": [item.__dict__ for item in pin_animations
                               if item.cloth_index == dynamic_index],
        }
        geometry_hash, settings_hash = _hash(geometry_value), _hash(settings_value)
        object_identity = {
            "persistent_uuid": str(obj.cloth_next.persistent_export_id),
            "role": role, "name": str(obj.name)}
        fingerprints = {
            "settings": settings_hash, "geometry": geometry_hash,
            "combined": _hash({"settings": settings_hash, "geometry": geometry_hash}),
            "topology": _hash({"triangles": mesh.triangles}),
            "object": _hash(object_identity), "scene": identity}
        metadata = {
            "fingerprints": fingerprints,
            "identities": {
                "cloth_next_version": manifest_version(),
                "blender_version": ".".join(map(str, bpy.app.version)),
                "object": object_identity,
                "solver": {"mode": "NEWTON", "solver": request.solver,
                           "newton_version": NEWTON_VERSION,
                           "warp_version": WARP_VERSION,
                           "protocol_version": str(PROTOCOL_VERSION)}},
            "expected": {"vertex_count": len(local_vertices),
                         "frame_count": frame_count, "start_frame": 0.0,
                         "sample_rate": 1.0},
            "details": {"backend": "NEWTON",
                        "scene_identity": identity,
                        "blender_start_frame": start, "blender_end_frame": end}}
        cache_dir = Path(bpy.path.abspath(directory)).resolve()
        cache_path = cache_dir / f"cloth_next_newton_{session_id[:12]}.pc2"
        targets.append(_BakeTarget(
            str(obj.name), str(obj.cloth_next.persistent_export_id),
            local_vertices, tuple(tuple(float(value) for value in row)
                                  for row in inverse_array),
            cache_path, metadata, role))
    return _BakeSession(request, tuple(targets), threading.Event(),
                        queue.Queue(maxsize=128))


def _capture(context) -> _BakeSession:
    """Synchronous test/helper facade; production advances one yielded step per timer."""
    iterator = _capture_steps(context)
    while True:
        try:
            next(iterator)
        except StopIteration as finished:
            return finished.value


def _wait(client, session, predicate, timeout=300.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.cancel_event.is_set():
            raise InterruptedError("Newton Bake cancelled")
        message = client.poll(0.1)
        if message is None:
            if client.process is None or client.process.poll() is not None:
                raise RuntimeError(client.failure_details())
            continue
        if message.get("event") == "error":
            raise RuntimeError(message.get("message", "Newton worker error"))
        if predicate(message):
            return message
    raise TimeoutError("Newton Bake worker timed out")


def _result_positions(request, message):
    result = PreviewResult(
        str(message["session_id"]), str(message["scene_identity"]),
        int(message["frame"]), int(message["vertex_count"]),
        str(message["artifact"]), str(message["sha256"]),
        bool(message.get("complete", False)))
    result.validate_for(request)
    root = Path(request.result_directory).resolve()
    artifact = Path(result.artifact).resolve()
    if root not in artifact.parents or not artifact.is_file():
        raise ValueError("Newton Bake result is outside its owned session")
    raw = artifact.read_bytes()
    if hashlib.sha256(raw).hexdigest() != result.sha256:
        raise ValueError("Newton Bake result checksum mismatch")
    positions = np.load(artifact, allow_pickle=False)
    if positions.shape != (result.vertex_count, 3) or not np.isfinite(positions).all():
        raise ValueError("Newton Bake result contains invalid positions")
    return np.asarray(positions, dtype=np.float64)


def _world_to_local(positions, inverse):
    matrix = np.asarray(inverse, dtype=np.float64)
    values = np.concatenate((positions, np.ones((len(positions), 1))), axis=1)
    transformed = values @ matrix.T
    if np.any(np.abs(transformed[:, 3]) <= 1.0e-12):
        raise ValueError("Newton Bake produced an invalid homogeneous transform")
    return np.asarray(transformed[:, :3] / transformed[:, 3, None], dtype="<f4")


def _worker_main(session):
    client = NewtonWorkerClient(newton_preview._newton_python(),
                                package_root=Path(__file__).resolve().parents[2],
                                startup_timeout=60.0)
    session.client = client
    started = time.perf_counter()
    disposable = False
    try:
        health = client.start()
        session.messages.put(("status", BakeState.STARTING_SOLVER,
                              "Newton worker ready", health))
        artifact = write_request_artifact(
            session.request.result_directory, session.request.to_wire())
        client.send("create_preview", request_artifact=artifact,
                    result_directory=session.request.result_directory)
        _wait(client, session, lambda item: item.get("event") == "created")
        frame_count = session.request.frame_end - session.request.frame_start + 1
        partials = []
        for target in session.targets:
            partial = cache_metadata.partial_metadata(
                cache_path=target.pc2_path,
                fingerprints=target.metadata["fingerprints"],
                identities=target.metadata["identities"],
                expected=target.metadata["expected"],
                details=target.metadata["details"])
            cache_metadata.write_atomic(
                cache_metadata.sidecar_path(target.pc2_path), partial)
            partials.append(partial)
        with ExitStack() as stack:
            writers = [stack.enter_context(pc2.StreamingPc2Writer(
                target.pc2_path,
                vertex_count=len(target.source_local_vertices),
                frame_count=frame_count)) for target in session.targets]
            for offset, frame in enumerate(range(session.request.frame_start,
                                                 session.request.frame_end + 1)):
                if frame != session.request.frame_start:
                    client.send("update_target_frame", frame=frame)
                result = _wait(
                    client, session, lambda item, expected=frame:
                    item.get("event") == "result" and item.get("frame") == expected)
                positions = _result_positions(session.request, result)
                vertex_offset = 0
                for target, writer in zip(session.targets, writers):
                    count = len(target.source_local_vertices)
                    writer.write_frame(_world_to_local(
                        positions[vertex_offset:vertex_offset + count],
                        target.inverse_world))
                    vertex_offset += count
                session.messages.put(("progress", offset + 1, frame, frame_count))
            headers = tuple(writer.finalize() for writer in writers)
        elapsed = time.perf_counter() - started
        for target, partial in zip(session.targets, partials):
            complete = cache_metadata.completed_metadata(
                partial, cache_path=target.pc2_path,
                timings={"newton_bake_seconds": elapsed})
            cache_metadata.write_atomic(
                cache_metadata.sidecar_path(target.pc2_path), complete)
        disposable = True
        session.messages.put(("finished", headers))
    except InterruptedError:
        disposable = True
        session.messages.put(("cancelled",))
    except Exception as exc:
        session.messages.put(("failed", str(exc)))
    finally:
        client.shutdown()
        session.client = None
        if disposable:
            try:
                remove_owned_session(session.request.result_directory)
            except (OSError, ValueError):
                pass


def _capture_header(scene):
    dynamics = tuple(obj for obj in newton_preview._enabled_objects(scene)
                     if str(obj.cloth_next.role) in
                     {"CLOTH", "SOFT_BODY", "RIGID_BODY"})
    if not dynamics:
        raise ValueError("Newton Bake requires Cloth, Soft Body, or Rigid Body objects.")
    ranges = {(int(obj.cloth_next.bake_start), int(obj.cloth_next.bake_end))
              for obj in dynamics}
    if len(ranges) != 1:
        raise ValueError("Use the same Bake range on all Newton dynamic objects.")
    start, end = next(iter(ranges))
    return start, end, str(dynamics[0].name)


def begin(context) -> tuple[str, bool]:
    global _capture_iterator, _capture_cancel_event
    if (_session is not None or _capture_iterator is not None
            or shared_controller.snapshot().active):
        raise ValueError("A Cloth NeXt bake is already active.")
    installed, _label, _path = newton_preview.newton_installation_status()
    if not installed:
        raise ValueError("Install Newton · Principia in Cloth NeXt Preferences first.")
    if shared_controller.snapshot().state is not BakeState.IDLE:
        shared_controller.reset()
    job = shared_controller.transition(
        BakeState.PREPARING, status_message="Validating Newton Bake",
        frame_start=None, frame_end=None).job_id
    try:
        start, end, active_name = _capture_header(context.scene)
        _capture_iterator = _capture_steps(context)
        _capture_cancel_event = threading.Event()
        shared_controller.update(
            status_message="Preparing Newton scene",
            active_object_name=active_name,
            frame_start=start, frame_end=end,
            progress_current=0,
            progress_total=end - start + 1)
        shared_controller.transition(
            BakeState.STARTING_COMPANION,
            status_message="Starting Bake window")
        request = EnterBakeMode(
            job_id=job, blender_process_id=os.getpid(),
            frame_start=start, frame_end=end,
            preset_label="Newton")
        ok, message = companion_manager.begin_bake_mode(request)
        if not ok:
            raise ValueError(message)
        shared_controller.transition(
            BakeState.WAITING_FOR_COMPANION,
            status_message="Opening Bake window…")
        if not bpy.app.timers.is_registered(_startup_pump):
            bpy.app.timers.register(_startup_pump, first_interval=_PUMP_INTERVAL)
        return job, True
    except Exception as exc:
        _capture_iterator = None
        _capture_cancel_event = None
        shared_controller.fail("Newton Bake preparation failed.", str(exc))
        raise


def _startup_pump():
    """Begin cooperative scene capture only after the Bake window is ready."""
    if _capture_iterator is None:
        return None
    job = shared_controller.snapshot().job_id
    if _capture_cancel_event is not None and _capture_cancel_event.is_set():
        companion_manager.cancel_startup(job, "Newton Bake startup cancelled")
        shared_controller.transition(BakeState.CANCELLED,
                                     status_message="Newton Bake cancelled")
        _clear_session()
        return None
    state, message = companion_manager.startup_status(job)
    if state == "WAITING":
        shared_controller.update(status_message=message)
        return _PUMP_INTERVAL
    if state != "READY":
        shared_controller.fail(message)
        _clear_session()
        return None
    if not companion_manager.consume_ready(job):
        return _PUMP_INTERVAL
    shared_controller.transition(BakeState.COMPANION_READY,
                                 status_message="Preparing Newton scene")
    if not bpy.app.timers.is_registered(_capture_pump):
        bpy.app.timers.register(_capture_pump, first_interval=0.0)
    return None


def _capture_pump():
    """Evaluate at most one Blender object or animation frame per timer tick."""
    global _capture_iterator, _capture_cancel_event, _session
    iterator = _capture_iterator
    if iterator is None:
        return None
    if _capture_cancel_event is not None and _capture_cancel_event.is_set():
        iterator.close()
        _capture_iterator = None
        _capture_cancel_event = None
        shared_controller.transition(BakeState.CANCELLED,
                                     status_message="Newton Bake cancelled")
        return None
    try:
        phase, message, current, total = next(iterator)
        shared_controller.update(
            status_message=message, progress_current=current,
            progress_total=max(1, total))
        return 0.01 if phase == "animation" else _PUMP_INTERVAL
    except StopIteration as finished:
        _capture_iterator = None
        _capture_cancel_event = None
        _session = finished.value
        job = shared_controller.snapshot().job_id
        shared_controller.update(
            status_message="Newton scene ready", progress_current=0,
            progress_total=_session.request.frame_end - _session.request.frame_start + 1)
        try:
            bpy.ops.clothnext.newton_bake_modal("INVOKE_DEFAULT", job_id=job)
        except (AttributeError, RuntimeError) as exc:
            shared_controller.fail(
                "The modal Newton Bake workflow could not start.", str(exc))
            _clear_session()
        return None
    except Exception as exc:
        try:
            iterator.close()
        finally:
            _capture_iterator = None
            _capture_cancel_event = None
        shared_controller.fail("Newton scene preparation failed.", str(exc))
        return None


def _start_worker(session) -> None:
    shared_controller.transition(BakeState.STARTING_RUN,
                                 status_message="Starting Newton Bake")
    shared_controller.transition(BakeState.EXPORTING,
                                 status_message="Exporting Newton scene")
    session.worker = threading.Thread(
        target=_worker_main, args=(session,), daemon=True,
        name="clothnext-newton-bake")
    session.worker.start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=_PUMP_INTERVAL)


class CLOTHNEXT_OT_newton_bake_modal(bpy.types.Operator):
    """Global modal lifecycle entered only after Bake-window readiness."""

    bl_idname = "clothnext.newton_bake_modal"
    bl_label = "Cloth NeXt Newton Modal Bake"
    bl_options = {"INTERNAL"}
    job_id: bpy.props.StringProperty(options={"HIDDEN"})
    _timer = None

    def invoke(self, context, _event):
        session = _session
        manager = getattr(context, "window_manager", None)
        if (session is None or self.job_id != shared_controller.snapshot().job_id
                or shared_controller.snapshot().state is not BakeState.COMPANION_READY
                or manager is None or not hasattr(manager, "event_timer_add")):
            return {"CANCELLED"}
        if not modal_lock.acquire(
                self.job_id, companion_ready_job_id=self.job_id):
            return {"CANCELLED"}
        try:
            _start_worker(session)
        except Exception as exc:
            modal_lock.release(self.job_id)
            shared_controller.fail("Starting the Newton Bake failed.", str(exc))
            _clear_session()
            return {"CANCELLED"}
        self._timer = manager.event_timer_add(
            0.1, window=getattr(context, "window", None))
        manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        return self.invoke(context, None)

    def modal(self, context, event):
        snapshot = shared_controller.snapshot()
        if not modal_lock.active(self.job_id):
            self._cleanup(context)
            return {"FINISHED"}
        if event.type == "ESC" and snapshot.can_cancel:
            request_cancel()
        if event.type == "TIMER":
            for area in getattr(getattr(context, "screen", None), "areas", ()):
                area.tag_redraw()
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        request_cancel()
        self._cleanup(context)

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        modal_lock.release(self.job_id)


def _clear_session() -> None:
    global _session
    _session = None


def _playback_plan(session, target):
    from ..ppf_run.session import SessionScene
    from .solver_test import RunPlan
    scene = SessionScene(
        "newton", target.source_name, target.source_uuid,
        len(target.source_local_vertices), "", "", session.request.frame_end -
        session.request.frame_start + 1, b"", b"", "newton", "newton")
    return RunPlan(
        scene, None, target.source_local_vertices,
        tuple(tuple(float(v) for v in row) for row in
              np.linalg.inv(np.asarray(target.inverse_world))),
        target.source_name, target.pc2_path.parent, target.pc2_path,
        session.request.frame_end - session.request.frame_start + 1,
        session.request.frame_start, session.request.frame_end,
        session.request.fps,
        target.metadata["fingerprints"]["settings"],
        target.metadata["fingerprints"]["geometry"],
        target.metadata["fingerprints"]["topology"],
        "Newton", target.metadata, target.role, backend_id="NEWTON")


def _pump():
    global _session
    session = _session
    if session is None:
        return None
    try:
        for _ in range(64):
            try:
                message = session.messages.get_nowait()
            except queue.Empty:
                break
            if message[0] == "status":
                shared_controller.transition(
                    message[1], status_message=message[2],
                    solver_mode="NEWTON",
                    solver_version=NEWTON_VERSION)
                shared_controller.transition(BakeState.SIMULATING,
                                             status_message="Simulating with Newton")
            elif message[0] == "progress":
                current, frame, total = message[1:]
                shared_controller.update(
                    status_message=f"Newton Bake · Frame {frame}",
                    current_frame=frame, progress_current=current,
                    progress_total=total)
            elif message[0] == "finished":
                from .solver_test import _attach_playback
                shared_controller.transition(BakeState.IMPORTING,
                                             status_message="Attaching Newton PC2 cache")
                for target, header in zip(session.targets, message[1]):
                    _attach_playback(_playback_plan(session, target), header)
                total = session.request.frame_end - session.request.frame_start + 1
                shared_controller.transition(
                    BakeState.FINISHED,
                    status_message=f"Finished · {total} Newton frames cached",
                    progress_current=total, progress_total=total,
                    current_frame=session.request.frame_end)
                _session = None
                return None
            elif message[0] == "cancelled":
                shared_controller.transition(BakeState.CANCELLED,
                                             status_message="Newton Bake cancelled")
                _session = None
                return None
            elif message[0] == "failed":
                shared_controller.fail("Newton Bake failed.", message[1])
                _session = None
                return None
        return _PUMP_INTERVAL
    except Exception as exc:
        shared_controller.fail("Importing the Newton result failed.", str(exc))
        _session = None
        return None


def request_cancel() -> bool:
    global _capture_cancel_event
    if _capture_iterator is not None:
        if _capture_cancel_event is not None:
            _capture_cancel_event.set()
        if shared_controller.snapshot().state is not BakeState.CANCELLING:
            shared_controller.request_cancel()
        return True
    if _session is None:
        return False
    _session.cancel_event.set()
    if _session.client is not None:
        try:
            _session.client.send("cancel")
        except (OSError, RuntimeError):
            pass
    if shared_controller.snapshot().state is not BakeState.CANCELLING:
        shared_controller.request_cancel()
    return True


def shutdown(timeout=5.0) -> None:
    global _session, _capture_iterator, _capture_cancel_event
    if _capture_iterator is not None:
        try:
            _capture_iterator.close()
        finally:
            _capture_iterator = None
            _capture_cancel_event = None
    session = _session
    if session is not None:
        request_cancel()
        if session.worker is not None:
            session.worker.join(timeout=max(0.0, float(timeout)))
        if session.client is not None:
            session.client.shutdown(grace=1.0)
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    if bpy.app.timers.is_registered(_startup_pump):
        bpy.app.timers.unregister(_startup_pump)
    if bpy.app.timers.is_registered(_capture_pump):
        bpy.app.timers.unregister(_capture_pump)
    modal_lock.release()
    _session = None


CLASSES = (CLOTHNEXT_OT_newton_bake_modal,)
