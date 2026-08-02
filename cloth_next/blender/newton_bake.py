# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental Newton offline Bake using the existing PC2 playback path."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import ExitStack
import hashlib
import json
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
from ..newton_preview.client import NewtonWorkerClient
from ..newton_preview.artifacts import (prune_owned_sessions,
                                        remove_owned_session)
from ..newton_preview.contracts import (ColliderAnimation, NEWTON_VERSION,
                                        PROTOCOL_VERSION, PinAnimation, PreviewCloth,
                                        PreviewCreateRequest, PreviewResult,
                                        WARP_VERSION)
from . import newton_preview

_PUMP_INTERVAL = 0.05


@dataclass
class _BakeTarget:
    source_name: str
    source_uuid: str
    source_local_vertices: tuple
    inverse_world: tuple
    pc2_path: Path
    metadata: dict


@dataclass
class _BakeSession:
    request: PreviewCreateRequest
    targets: tuple[_BakeTarget, ...]
    cancel_event: threading.Event
    messages: queue.Queue
    worker: threading.Thread | None = None
    client: NewtonWorkerClient | None = None


_session: _BakeSession | None = None


def active() -> bool:
    return _session is not None and _session.worker is not None \
        and _session.worker.is_alive()


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _capture(context) -> _BakeSession:
    scene = context.scene
    cloths, colliders = newton_preview._validate_scope(scene)
    configured = tuple(str(cloth.cloth_next.cache_directory or "").strip()
                       for cloth in cloths)
    for cloth, directory in zip(cloths, configured):
        if not directory:
            raise ValueError(
                f"Set a Cache Directory for {cloth.name} before baking with Newton.")
    original_frame = int(scene.frame_current)
    start = int(cloths[0].cloth_next.bake_start)
    end = int(cloths[0].cloth_next.bake_end)
    try:
        scene.frame_set(start)
        cloth_meshes = tuple(newton_preview._cached_triangulated_world_mesh(
            context, cloth) for cloth in cloths)
        pin_sets = tuple(newton_preview._pin_indices(cloth, len(mesh.vertices))
                         for cloth, mesh in zip(cloths, cloth_meshes))
        collider_meshes = tuple(
            newton_preview._cached_triangulated_world_mesh(context, obj)
            for obj in colliders)
        inverse_arrays, local_sets = [], []
        for cloth, cloth_mesh in zip(cloths, cloth_meshes):
            matrix = np.asarray(tuple(tuple(float(value) for value in row)
                                      for row in cloth.matrix_world),
                                dtype=np.float64)
            inverse_array = np.linalg.inv(matrix)
            world = np.asarray(cloth_mesh.vertices, dtype=np.float64)
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
            collider_animations.append(ColliderAnimation(
                collider_index, newton_preview._animated_collider_samples(
                    context, scene, collider, reference, start, end)))
        for cloth_index, (cloth, reference, pins) in enumerate(
                zip(cloths, cloth_meshes, pin_sets)):
            if (not pins
                    or str(cloth.cloth_next.pin_mode) != "FOLLOW_ANIMATION"):
                continue
            pin_animations.append(PinAnimation(
                cloth_index, newton_preview._animated_pin_samples(
                    context, scene, cloth, reference, pins, start, end)))
    finally:
        scene.frame_set(original_frame)
    settings = scene.cloth_next_newton_preview
    quality = newton_preview._quality(settings)
    materials = tuple(newton_preview._material(cloth) for cloth in cloths)
    identity = newton_preview._scene_identity(
        scene, cloths, colliders, cloth_meshes, collider_meshes, pin_sets,
        materials, quality, tuple(collider_animations), tuple(pin_animations))
    session_id = uuid.uuid4().hex
    app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    session_root = app_data / "ClothNeXt" / "newton" / "sessions" / session_id
    try:
        prune_owned_sessions(session_root.parent, keep=20,
                             exclude=(session_id,))
    except OSError:
        pass  # a stale diagnostic directory must not block a new Bake
    request = PreviewCreateRequest(
        session_id, identity, cloth_meshes[0], collider_meshes, pin_sets[0],
        materials[0], quality, start, end,
        float(scene.render.fps) / float(scene.render.fps_base),
        float(settings.time_scale), newton_preview._gravity(scene),
        str(session_root / "results"),
        additional_cloths=tuple(PreviewCloth(
            str(cloth.cloth_next.persistent_export_id), mesh, pins, material)
            for cloth, mesh, pins, material in zip(
                cloths[1:], cloth_meshes[1:], pin_sets[1:], materials[1:])),
        collider_animations=tuple(collider_animations),
        pin_animations=tuple(pin_animations), solver="VBD")
    request.validate()
    settings_value = {
        "backend": "NEWTON", "newton": NEWTON_VERSION, "warp": WARP_VERSION,
        "protocol": PROTOCOL_VERSION, "solver": request.solver,
        "materials": [material.__dict__ for material in materials],
        "quality": quality.__dict__,
        "frame_start": request.frame_start, "frame_end": request.frame_end,
        "fps": request.fps, "time_scale": request.time_scale,
        "gravity": request.gravity,
    }
    frame_count = request.frame_end - request.frame_start + 1
    targets = []
    for cloth_index, (cloth, mesh, pins, local_vertices, inverse_array,
                      directory) in enumerate(zip(
            cloths, cloth_meshes, pin_sets, local_sets, inverse_arrays,
            configured)):
        geometry_value = {
            "backend_schema": 2,
            "object_uuid": str(cloth.cloth_next.persistent_export_id),
            "role": str(cloth.cloth_next.role), "vertices": mesh.vertices,
            "triangles": mesh.triangles, "pins": pins,
            "colliders": [{"vertices": item.vertices, "triangles": item.triangles}
                          for item in collider_meshes],
            "collider_animations": [item.__dict__ for item in collider_animations],
            "pin_animations": [item.__dict__ for item in pin_animations
                               if item.cloth_index == cloth_index],
        }
        geometry_hash, settings_hash = _hash(geometry_value), _hash(settings_value)
        object_identity = {
            "persistent_uuid": str(cloth.cloth_next.persistent_export_id),
            "role": str(cloth.cloth_next.role), "name": str(cloth.name)}
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
                "solver": {"mode": "NEWTON_EXPERIMENTAL", "solver": request.solver,
                           "newton_version": NEWTON_VERSION,
                           "warp_version": WARP_VERSION,
                           "protocol_version": str(PROTOCOL_VERSION)}},
            "expected": {"vertex_count": len(local_vertices),
                         "frame_count": frame_count, "start_frame": 0.0,
                         "sample_rate": 1.0},
            "details": {"backend": "NEWTON_EXPERIMENTAL",
                        "scene_identity": identity,
                        "blender_start_frame": start, "blender_end_frame": end}}
        cache_dir = Path(bpy.path.abspath(directory)).resolve()
        cache_path = cache_dir / f"cloth_next_newton_{session_id[:12]}.pc2"
        targets.append(_BakeTarget(
            str(cloth.name), str(cloth.cloth_next.persistent_export_id),
            local_vertices, tuple(tuple(float(value) for value in row)
                                  for row in inverse_array),
            cache_path, metadata))
    return _BakeSession(request, tuple(targets), threading.Event(),
                        queue.Queue(maxsize=128))


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
        client.send("create_preview", request=session.request.to_wire())
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


def begin(context) -> tuple[str, bool]:
    global _session
    if _session is not None or shared_controller.snapshot().active:
        raise ValueError("A Cloth NeXt bake is already active.")
    if bool(context.scene.cloth_next_newton_preview.enabled):
        raise ValueError("Disable Live Preview before starting a Newton Bake.")
    installed, _label, _path = newton_preview.newton_installation_status()
    if not installed:
        raise ValueError("Install Newton · Principia in Cloth NeXt Preferences first.")
    if shared_controller.snapshot().state is not BakeState.IDLE:
        shared_controller.reset()
    job = shared_controller.transition(
        BakeState.PREPARING, status_message="Validating Newton Bake",
        frame_start=None, frame_end=None).job_id
    try:
        session = _capture(context)
        _session = session
        shared_controller.transition(
            BakeState.STARTING_RUN, status_message="Starting Newton Bake",
            active_object_name=session.targets[0].source_name,
            frame_start=session.request.frame_start,
            frame_end=session.request.frame_end,
            progress_current=0,
            progress_total=session.request.frame_end - session.request.frame_start + 1)
        shared_controller.transition(BakeState.EXPORTING,
                                     status_message="Exporting Newton scene")
        session.worker = threading.Thread(
            target=_worker_main, args=(session,), daemon=True,
            name="clothnext-newton-bake")
        session.worker.start()
        if not bpy.app.timers.is_registered(_pump):
            bpy.app.timers.register(_pump, first_interval=_PUMP_INTERVAL)
        return job, False
    except Exception as exc:
        _session = None
        shared_controller.fail("Newton Bake preparation failed.", str(exc))
        raise


def _playback_plan(session, target):
    from ..ppf_run.session import SessionScene
    from .solver_test import RunPlan
    scene = SessionScene(
        "newton-experimental", target.source_name, target.source_uuid,
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
        "Newton Experimental", target.metadata, "CLOTH")


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
                    solver_mode="NEWTON_EXPERIMENTAL",
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
    global _session
    session = _session
    if session is None:
        return
    request_cancel()
    if session.worker is not None:
        session.worker.join(timeout=max(0.0, float(timeout)))
    if session.client is not None:
        session.client.shutdown(grace=1.0)
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    _session = None
