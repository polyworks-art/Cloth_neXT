# SPDX-License-Identifier: GPL-3.0-or-later
"""Production Blender adapter for the external Newton Live Preview worker."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import struct
import threading
import time
import uuid

import bpy
import numpy as np

from ..newton_preview.client import NewtonWorkerClient
from ..newton_preview.artifacts import (prune_owned_sessions,
                                        remove_owned_session)
from ..newton_preview.contracts import (ColliderAnimation, PreviewCloth,
                                        PreviewCreateRequest, PreviewMesh,
                                        PreviewQuality, PreviewResult)
from ..newton_preview.coordinates import (transform_position,
                                           validate_world_transform)
from ..newton_preview.material import map_cloth_material
from ..newton_preview.install import read_current
from ..newton_preview.state import PreviewState, status_label, transition
from .playback_cache import (has_cloth_next_playback_marker,
                             without_owned_playback)

_TIMER_INTERVAL = 0.03
_PREVIEW_MARKER = "cloth_next_newton_preview_owned"
_mesh_capture_cache = {}
_mesh_capture_metrics = {"hits": 0, "misses": 0, "evaluations": 0,
                         "capture_seconds": 0.0, "hash_seconds": 0.0}
_owned_visibility_updates = {}


def _newton_python() -> Path:
    configured = os.environ.get("CLOTHNEXT_NEWTON_PYTHON", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    managed = read_current()
    if managed is not None:
        return managed
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return (base / "ClothNeXt/newton/versions/1.4.0-warp-1.15.0/venv"
                / "Scripts/python.exe")
    return (Path.home() / ".local/share/ClothNeXt/newton/versions"
            / "1.4.0-warp-1.15.0/venv/bin/python")


def newton_installation_status() -> tuple[bool, str, Path]:
    executable = _newton_python()
    return (executable.is_file(),
            "Newton 1.4.0 · Warp 1.15.0" if executable.is_file()
            else "Newton unavailable", executable)


def _quality(settings) -> PreviewQuality:
    values = {
        "FAST": (2, 4, 12, 8),
        "BALANCED": (4, 8, 10, 12),
        "HIGH": (8, 16, 6, 20),
    }
    substeps, iterations, cadence, maximum = values.get(
        str(settings.quality), values["BALANCED"])
    return PreviewQuality(str(settings.quality), substeps, iterations,
                          cadence, maximum, bool(settings.enable_self_contact))


def _enabled_objects(scene):
    return tuple(obj for obj in scene.objects
                 if bool(getattr(getattr(obj, "cloth_next", None),
                                 "enabled", False)))


def _evaluated_world_mesh_data(context, obj):
    """Return world-space triangles plus the pre-triangulation topology.

    Blender may choose a different diagonal for a deformed ngon/quad when
    ``calc_loop_triangles`` is called at another frame.  Polygon vertex loops
    are the durable topology signal; loop-triangle ordering is an export
    detail and must remain fixed to the first sample for animated Colliders.
    """
    matrix = tuple(tuple(float(value) for value in row)
                   for row in obj.matrix_world)
    validate_world_transform(matrix)
    update = getattr(getattr(context, "view_layer", None), "update", None)
    with without_owned_playback(obj, update=update):
        evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            topology = tuple(tuple(int(index) for index in polygon.vertices)
                             for polygon in mesh.polygons)
            mesh.calc_loop_triangles()
            vertices = tuple(transform_position(matrix, vertex.co)
                             for vertex in mesh.vertices)
            triangles = tuple(tuple(int(index) for index in tri.vertices)
                              for tri in mesh.loop_triangles)
            result = PreviewMesh(vertices, triangles)
            result.validate(label=str(obj.name))
            return result, topology
        finally:
            evaluated.to_mesh_clear()


def _triangulated_world_mesh(context, obj) -> PreviewMesh:
    return _evaluated_world_mesh_data(context, obj)[0]


def _animated_collider_samples(context, scene, collider, reference,
                               frame_start, frame_end):
    """Capture positions while retaining the reference triangle ordering."""
    samples = []
    reference_topology = None
    for frame in range(frame_start, frame_end + 1):
        scene.frame_set(frame)
        sample, topology = _evaluated_world_mesh_data(context, collider)
        if reference_topology is None:
            reference_topology = topology
        if (topology != reference_topology
                or len(sample.vertices) != len(reference.vertices)):
            raise ValueError(
                f"{collider.name}: animated Collider topology must remain constant")
        samples.append(sample.vertices)
    return tuple(samples)


def _mesh_capture_key(obj):
    digest = hashlib.sha256()
    for vertex in obj.data.vertices:
        digest.update(struct.pack("<3d", *(float(value) for value in vertex.co)))
    polygons = getattr(obj.data, "polygons", ())
    for polygon in polygons:
        indices = tuple(int(value) for value in polygon.vertices)
        digest.update(struct.pack("<I", len(indices)))
        digest.update(struct.pack(f"<{len(indices)}I", *indices))
    settings = obj.cloth_next
    matrix = tuple(tuple(round(float(value), 12) for value in row)
                   for row in obj.matrix_world)
    modifiers = []
    for modifier in obj.modifiers:
        if has_cloth_next_playback_marker(obj, modifier):
            continue
        target = getattr(modifier, "object", None)
        target_pose = ()
        if target is not None:
            pose = getattr(target, "pose", None)
            target_pose = tuple(
                (str(bone.name), tuple(tuple(round(float(value), 12)
                                            for value in row)
                                       for row in bone.matrix))
                for bone in getattr(pose, "bones", ()))
        modifiers.append((str(modifier.name), str(modifier.type),
                          bool(modifier.show_viewport),
                          bool(modifier.show_render),
                          str(getattr(target, "name", "")), target_pose))
    shape_keys = getattr(obj.data, "shape_keys", None)
    shape_values = tuple((str(block.name), round(float(block.value), 12),
                          bool(getattr(block, "mute", False)))
                         for block in getattr(shape_keys, "key_blocks", ()))
    return (str(settings.persistent_export_id), str(settings.role),
            str(obj.data.name), digest.hexdigest(), matrix, tuple(modifiers),
            shape_values)


def _cached_triangulated_world_mesh(context, obj) -> PreviewMesh:
    hash_started = time.perf_counter()
    key = _mesh_capture_key(obj)
    _mesh_capture_metrics["hash_seconds"] += time.perf_counter() - hash_started
    cached = _mesh_capture_cache.get(str(obj.name))
    if cached is not None and cached[0] == key:
        _mesh_capture_metrics["hits"] += 1
        return cached[2]
    started = time.perf_counter()
    result = _triangulated_world_mesh(context, obj)
    _mesh_capture_metrics["misses"] += 1
    _mesh_capture_metrics["evaluations"] += 1
    _mesh_capture_metrics["capture_seconds"] += time.perf_counter() - started
    dependencies = {(type(obj).__name__, str(getattr(obj, "name", ""))),
                    (type(obj.data).__name__, str(getattr(obj.data, "name", "")))}
    for modifier in obj.modifiers:
        target = getattr(modifier, "object", None)
        if target is not None:
            dependencies.add((type(target).__name__,
                              str(getattr(target, "name", ""))))
            data = getattr(target, "data", None)
            if data is not None:
                dependencies.add((type(data).__name__,
                                  str(getattr(data, "name", ""))))
    _mesh_capture_cache[str(obj.name)] = (key, dependencies, result)
    if len(_mesh_capture_cache) > 32:
        _mesh_capture_cache.pop(next(iter(_mesh_capture_cache)))
    return result


def _pin_indices(obj, vertex_count: int) -> tuple[int, ...]:
    settings = obj.cloth_next
    if not bool(settings.pinning_enabled):
        return ()
    if str(settings.pin_mode) != "STATIC":
        raise ValueError("Newton Live Preview supports Static pins only; change Pin Mode or disable Pinning")
    name = str(settings.pin_group or "")
    group = obj.vertex_groups.get(name) if name else None
    if group is None:
        raise ValueError("Newton Live Preview Pinning requires a valid vertex group")
    indices = []
    for vertex in obj.data.vertices:
        if any(item.group == group.index and item.weight > 0.0
               for item in vertex.groups):
            indices.append(int(vertex.index))
    if vertex_count != len(obj.data.vertices):
        raise ValueError("Newton Live Preview requires topology-preserving Cloth modifiers")
    return tuple(indices)


def _gravity(scene) -> tuple[float, float, float]:
    vectors = []
    for obj in _enabled_objects(scene):
        settings = obj.cloth_next
        if str(settings.role) != "FORCE" or str(settings.force.force_type) != "GRAVITY":
            continue
        direction = tuple(-float(obj.matrix_world[row][2]) for row in range(3))
        length = sum(component * component for component in direction) ** 0.5
        if length <= 1.0e-12:
            raise ValueError(f'{obj.name}: Gravity Force has a singular transform')
        direction = tuple(component / length for component in direction)
        strength = float(settings.force.strength)
        vectors.append(tuple(float(component) * strength for component in direction))
    if not vectors:
        return (0.0, 0.0, -9.81)
    return tuple(sum(vector[index] for vector in vectors) for index in range(3))


def _material(obj):
    material, damping, collision = (obj.cloth_next.material,
                                    obj.cloth_next.damping,
                                    obj.cloth_next.collision)
    return map_cloth_material(
        surface_weight=material.surface_weight,
        stretch_resistance=material.stretch_resistance,
        sideways_response=material.sideways_response,
        bend_resistance=material.bend_resistance,
        shape_damping=damping.shape_damping,
        fold_damping=damping.fold_damping,
        friction=collision.surface_grip,
        collision_gap=collision.collision_gap,
        surface_offset=collision.surface_offset)


def _validate_scope(scene):
    enabled = _enabled_objects(scene)
    cloths = [obj for obj in enabled if str(obj.cloth_next.role) == "CLOTH"]
    colliders = [obj for obj in enabled if str(obj.cloth_next.role) == "COLLIDER"]
    unsupported = [obj for obj in enabled
                   if str(obj.cloth_next.role) not in {"CLOTH", "COLLIDER", "FORCE"}]
    if not cloths:
        raise ValueError("Newton Live Preview requires at least one Cloth object")
    if unsupported:
        raise ValueError("Newton Live Preview does not support "
                         + ", ".join(str(obj.cloth_next.role) for obj in unsupported))
    ranges = {(int(cloth.cloth_next.bake_start),
               int(cloth.cloth_next.bake_end)) for cloth in cloths}
    if len(ranges) != 1:
        raise ValueError("Newton Live Preview requires the same Bake range on every Cloth object")
    for cloth in cloths:
        unsupported_modifiers = [
            modifier.name for modifier in cloth.modifiers
            if bool(getattr(modifier, "show_viewport", True))
            and not has_cloth_next_playback_marker(cloth, modifier)
            and str(getattr(modifier, "type", "")) != "ARMATURE"]
        if unsupported_modifiers:
            raise ValueError(
                f"{cloth.name}: Newton Live Preview currently supports only "
                "Armature deformation; disable unsupported Cloth modifiers: "
                + ", ".join(map(str, unsupported_modifiers)))
        if bool(cloth.cloth_next.pressure.enable_inflate):
            raise ValueError(f"{cloth.name}: Newton Live Preview does not support Pressure")
        if bool(cloth.cloth_next.pressure.sewing_enabled):
            raise ValueError(f"{cloth.name}: Newton Live Preview does not support Sewing")
        if getattr(cloth, "mode", "OBJECT") == "EDIT":
            raise ValueError("Exit Edit Mode before enabling Newton Live Preview")
    return tuple(sorted(cloths, key=lambda obj: (
        str(obj.cloth_next.persistent_export_id), str(obj.name)))), tuple(
            sorted(colliders, key=lambda obj: (
                str(obj.cloth_next.persistent_export_id), str(obj.name))))


def _scene_identity(scene, cloths, colliders, cloth_meshes, collider_meshes,
                    pin_sets, materials, quality, collider_animations=()) -> str:
    value = {
        "schema": 2, "scene": str(scene.name),
        "cloth_uuids": [str(cloth.cloth_next.persistent_export_id)
                         for cloth in cloths],
        "collider_uuids": [str(obj.cloth_next.persistent_export_id)
                           for obj in colliders],
        "cloths": [{"vertices": mesh.vertices,
                    "triangles": mesh.triangles}
                   for mesh in cloth_meshes],
        "colliders": [{"vertices": mesh.vertices,
                       "triangles": mesh.triangles}
                      for mesh in collider_meshes],
        "pins": pin_sets,
        "materials": [material.__dict__ for material in materials],
        "collider_animations": [animation.__dict__
                                for animation in collider_animations],
        "quality": quality.__dict__, "fps": float(scene.render.fps)
        / float(scene.render.fps_base),
    }
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class _CapturedCloth:
    source: object
    preview: object
    source_hide_viewport: bool


@dataclass
class _Captured:
    request: PreviewCreateRequest
    cloths: tuple[_CapturedCloth, ...]
    tracked_ids: tuple
    animated_collider_ids: tuple
    cheap_identity: str
    capture_metrics: dict

    @property
    def source(self):
        return self.cloths[0].source

    @property
    def preview(self):
        return self.cloths[0].preview

    @property
    def source_hide_viewport(self):
        return self.cloths[0].source_hide_viewport


def _cheap_identity(scene, cloths, colliders, settings) -> str:
    def matrix(obj):
        return tuple(tuple(round(float(value), 9) for value in row)
                     for row in obj.matrix_world)
    def cloth_value(cloth):
        material = cloth.cloth_next.material
        damping = cloth.cloth_next.damping
        collision = cloth.cloth_next.collision
        return {
            "object": (str(cloth.cloth_next.persistent_export_id),
                       str(cloth.cloth_next.role), str(cloth.data.name),
                       tuple((modifier.name, modifier.type,
                              bool(modifier.show_viewport))
                             for modifier in cloth.modifiers), matrix(cloth)),
            "pins": (bool(cloth.cloth_next.pinning_enabled),
                     str(cloth.cloth_next.pin_group),
                     str(cloth.cloth_next.pin_mode)),
            "range": (int(cloth.cloth_next.bake_start),
                      int(cloth.cloth_next.bake_end)),
            "material": tuple(float(item) for item in (
                material.surface_weight, material.stretch_resistance,
                material.sideways_response, material.bend_resistance,
                damping.shape_damping, damping.fold_damping,
                collision.surface_grip, collision.collision_gap,
                collision.surface_offset)),
        }
    value = {
        "cloths": [cloth_value(cloth) for cloth in cloths],
        "timing": (float(scene.render.fps), float(scene.render.fps_base),
                   float(settings.time_scale)),
        "quality": (str(settings.quality), bool(settings.enable_self_contact)),
        "colliders": tuple((str(obj.cloth_next.persistent_export_id),
                            str(obj.cloth_next.role),
                            str(obj.cloth_next.collider_motion),
                            str(obj.data.name),
                            matrix(obj) if str(obj.cloth_next.collider_motion) == "STATIC"
                            else tuple((modifier.name, modifier.type,
                                        bool(modifier.show_viewport))
                                       for modifier in obj.modifiers))
                           for obj in colliders),
        "forces": tuple((str(obj.cloth_next.persistent_export_id),
                         str(obj.cloth_next.force.force_type),
                         float(obj.cloth_next.force.strength), matrix(obj))
                        for obj in _enabled_objects(scene)
                        if str(obj.cloth_next.role) == "FORCE"),
    }
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _Session:
    def __init__(self):
        self.state = PreviewState.DISABLED
        self.capture = None
        self.client = None
        self.start_thread = None
        self.sender_thread = None
        self.stop_event = threading.Event()
        self.target_queue = queue.Queue(maxsize=1)
        self.start_result = queue.Queue(maxsize=1)
        self.last_applied_frame = None
        self.last_requested_frame = None
        self.last_request_time = 0.0
        self.status_data = {}
        self.error = ""
        self.was_playing = False
        self.has_played = False

    def set_state(self, target):
        self.state = transition(self.state, target)
        _sync_settings(self)


_session = _Session()
_property_guard = False


def _settings():
    scene = getattr(getattr(bpy, "context", None), "scene", None)
    return getattr(scene, "cloth_next_newton_preview", None)


def _sync_settings(session):
    settings = _settings()
    if settings is None:
        return
    settings.status = status_label(
        session.state,
        current_frame=session.status_data.get("current_frame", 0),
        target_frame=session.status_data.get("target_frame", 0))
    settings.status_detail = session.error
    settings.current_frame = int(session.status_data.get("current_frame", 0) or 0)
    settings.target_frame = int(session.status_data.get("target_frame", 0) or 0)


def _set_enabled_without_callback(value):
    global _property_guard
    settings = _settings()
    if settings is None:
        return
    _property_guard = True
    try:
        settings.enabled = bool(value)
    finally:
        _property_guard = False


def _create_preview_object(context, source, mesh):
    preview_mesh = bpy.data.meshes.new(f"CNX Newton Preview {source.name}")
    preview_mesh.from_pydata(mesh.vertices, (), mesh.triangles)
    preview_mesh.update()
    preview = bpy.data.objects.new(f"CNX Newton Preview {source.name}", preview_mesh)
    preview[_PREVIEW_MARKER] = True
    preview["cloth_next_source"] = str(source.name)
    preview.hide_render = True
    for material in source.data.materials:
        preview_mesh.materials.append(material)
    collection = source.users_collection[0] if source.users_collection else context.collection
    collection.objects.link(preview)
    source_hidden = bool(source.hide_viewport)
    _mark_owned_visibility_update(source)
    source.hide_viewport = True
    return preview, source_hidden


def _mark_owned_visibility_update(source):
    for identifier in (source, getattr(source, "data", None)):
        if identifier is None:
            continue
        dependency = (type(identifier).__name__,
                      str(getattr(identifier, "name", "")))
        _owned_visibility_updates[dependency] = (
            _owned_visibility_updates.get(dependency, 0) + 1)


def _capture(context) -> _Captured:
    scene = context.scene
    cloths, colliders = _validate_scope(scene)
    original_frame = int(scene.frame_current)
    start = int(cloths[0].cloth_next.bake_start)
    end = int(cloths[0].cloth_next.bake_end)
    try:
        scene.frame_set(start)
        metrics_before = dict(_mesh_capture_metrics)
        cloth_meshes = tuple(_cached_triangulated_world_mesh(context, cloth)
                            for cloth in cloths)
        collider_meshes = tuple(_cached_triangulated_world_mesh(context, obj)
                                for obj in colliders)
        collider_animations = []
        for collider_index, collider in enumerate(colliders):
            if str(collider.cloth_next.collider_motion) != "ANIMATED":
                continue
            reference = collider_meshes[collider_index]
            collider_animations.append(ColliderAnimation(
                collider_index, _animated_collider_samples(
                    context, scene, collider, reference, start, end)))
    finally:
        scene.frame_set(original_frame)
    pin_sets = tuple(_pin_indices(cloth, len(mesh.vertices))
                     for cloth, mesh in zip(cloths, cloth_meshes))
    settings = scene.cloth_next_newton_preview
    quality = _quality(settings)
    materials = tuple(_material(cloth) for cloth in cloths)
    identity = _scene_identity(
        scene, cloths, colliders, cloth_meshes, collider_meshes,
        pin_sets, materials, quality, tuple(collider_animations))
    session_id = uuid.uuid4().hex
    base = (Path(os.environ.get("LOCALAPPDATA", Path.home()))
            / "ClothNeXt/newton/sessions" / session_id)
    try:
        prune_owned_sessions(base.parent, keep=20, exclude=(session_id,))
    except OSError:
        pass  # retention failure is diagnostic-only, never a preview blocker
    request = PreviewCreateRequest(
        session_id, identity, cloth_meshes[0], collider_meshes, pin_sets[0],
        materials[0], quality, start, end,
        float(scene.render.fps) / float(scene.render.fps_base),
        float(settings.time_scale), _gravity(scene), str(base / "results"),
        additional_cloths=tuple(
            PreviewCloth(str(cloth.cloth_next.persistent_export_id), mesh,
                         pins, material)
            for cloth, mesh, pins, material in zip(
                cloths[1:], cloth_meshes[1:], pin_sets[1:], materials[1:])),
        collider_animations=tuple(collider_animations))
    request.validate()
    captured_cloths = []
    try:
        for cloth, mesh in zip(cloths, cloth_meshes):
            preview, source_hidden = _create_preview_object(context, cloth, mesh)
            captured_cloths.append(_CapturedCloth(
                cloth, preview, source_hidden))
    except Exception:
        for captured in captured_cloths:
            try:
                captured.source.hide_viewport = captured.source_hide_viewport
                mesh = captured.preview.data
                bpy.data.objects.remove(captured.preview, do_unlink=True)
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except (ReferenceError, RuntimeError, AttributeError):
                pass
        raise
    settings.session_id = session_id
    tracked = (*(item for cloth in cloths for item in (cloth, cloth.data)),
               *(item for collider in colliders
                                   for item in (collider, collider.data)))
    animated_ids = tuple(item for collider in colliders
                         if str(collider.cloth_next.collider_motion) == "ANIMATED"
                         for item in (collider, collider.data))
    capture_metrics = {
        f"newton_mesh_capture_{name}": _mesh_capture_metrics[name]
        - metrics_before[name] for name in metrics_before}
    return _Captured(request, tuple(captured_cloths), tuple(tracked),
                     animated_ids,
                     _cheap_identity(scene, cloths, colliders, settings),
                     capture_metrics)


def _start_worker(session, captured):
    try:
        root = Path(__file__).resolve().parents[2]
        client = NewtonWorkerClient(_newton_python(), package_root=root,
                                    startup_timeout=30.0)
        health = client.start()
        if session.stop_event.is_set():
            client.shutdown()
            return
        client.send("create_preview", request=captured.request.to_wire())
        session.start_result.put((client, health, None))
    except Exception as exc:
        session.start_result.put((None, None, exc))


def _sender(session):
    while not session.stop_event.is_set():
        try:
            frame = session.target_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        time.sleep(0.01)
        while True:
            try: frame = session.target_queue.get_nowait()
            except queue.Empty: break
        try:
            if session.client is not None:
                session.client.send("update_target_frame", frame=int(frame))
        except Exception as exc:
            session.error = str(exc)
            break


def start(context=None):
    global _session
    context = context or bpy.context
    if _session.state is PreviewState.FAILED:
        # Failure cleanup already restored the source and detached the old
        # worker. Start with a new identity so no late old result can apply.
        _session.stop_event.set()
        _session = _Session()
    if _session.state is not PreviewState.DISABLED:
        return
    _session = _Session()
    try:
        _session.set_state(PreviewState.CAPTURING_SCENE)
        _session.capture = _capture(context)
        _session.status_data.update(_session.capture.capture_metrics)
        _session.set_state(PreviewState.STARTING_WORKER)
        _session.start_thread = threading.Thread(
            target=_start_worker, args=(_session, _session.capture), daemon=True,
            name="clothnext-newton-start")
        _session.start_thread.start()
        if not bpy.app.timers.is_registered(_poll_timer):
            bpy.app.timers.register(_poll_timer, first_interval=_TIMER_INTERVAL)
    except Exception as exc:
        _fail(str(exc))


def request_toggle(context, enabled):
    if _property_guard:
        return
    if enabled:
        if not _newton_python().is_file():
            settings = context.scene.cloth_next_newton_preview
            settings.status = "Newton unavailable"
            settings.status_detail = "Install Newton in Cloth NeXt Preferences"
            _set_enabled_without_callback(False)
            return
        start(context)
    else:
        stop()


def _queue_target(frame):
    _session.last_requested_frame = int(frame)
    _session.last_request_time = time.monotonic()
    while True:
        try: _session.target_queue.get_nowait()
        except queue.Empty: break
    try: _session.target_queue.put_nowait(int(frame))
    except queue.Full: pass


def _frame_change_post(scene, _depsgraph=None):
    if _session.state not in {PreviewState.READY, PreviewState.PLAYING,
                              PreviewState.CATCHING_UP, PreviewState.PAUSED}:
        return
    frame = int(scene.frame_current)
    request = _session.capture.request
    frame = max(request.frame_start, min(request.frame_end, frame))
    _queue_target(frame)
    if frame < int(_session.status_data.get("current_frame", request.frame_start)):
        _session.set_state(PreviewState.RESETTING)
    elif frame > int(_session.status_data.get("current_frame", request.frame_start)) + 1:
        _session.set_state(PreviewState.CATCHING_UP)
    else:
        _session.set_state(PreviewState.PLAYING)


def _animation_playing() -> bool:
    manager = getattr(getattr(bpy, "context", None), "window_manager", None)
    return bool(manager is not None and any(
        bool(getattr(window.screen, "is_animation_playing", False))
        for window in manager.windows))


def _depsgraph_update_post(_scene, depsgraph):
    for update in getattr(depsgraph, "updates", ()):
        identifier = getattr(update, "id", None)
        if identifier is None or not (
                getattr(update, "is_updated_geometry", False)
                or getattr(update, "is_updated_transform", False)):
            continue
        dependency = (type(identifier).__name__,
                      str(getattr(identifier, "name", "")))
        allowance = _owned_visibility_updates.get(dependency, 0)
        if allowance > 0:
            if allowance == 1:
                _owned_visibility_updates.pop(dependency, None)
            else:
                _owned_visibility_updates[dependency] = allowance - 1
            continue
        stale = [name for name, entry in _mesh_capture_cache.items()
                 if dependency in entry[1]]
        for name in stale:
            _mesh_capture_cache.pop(name, None)
    if _session.capture is None or _session.state in {
            PreviewState.DISABLED, PreviewState.STOPPING, PreviewState.FAILED,
            PreviewState.STALE}:
        return
    tracked = _session.capture.tracked_ids
    for update in getattr(depsgraph, "updates", ()):
        if (update.id in tracked
                and update.id not in _session.capture.animated_collider_ids
                and (getattr(update, "is_updated_geometry", False)
                     or getattr(update, "is_updated_transform", False))):
            _session.error = f"{getattr(update.id, 'name', 'Scene')} changed; disable and re-enable Live Preview"
            _session.set_state(PreviewState.STALE)
            return


def _validate_artifact(message):
    request = _session.capture.request
    result = PreviewResult(
        str(message["session_id"]), str(message["scene_identity"]),
        int(message["frame"]), int(message["vertex_count"]),
        str(message["artifact"]), str(message["sha256"]),
        bool(message.get("complete", False)))
    result.validate_for(request)
    root = Path(request.result_directory).resolve()
    artifact = Path(result.artifact).resolve()
    if root not in artifact.parents or not artifact.is_file():
        raise ValueError("Newton result artifact is outside the owned session")
    expected_size = result.vertex_count * 12 + 128
    if artifact.stat().st_size > expected_size:
        raise ValueError("Newton result artifact exceeds its bounded size")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != result.sha256:
        raise ValueError("Newton result checksum mismatch")
    positions = np.load(artifact, allow_pickle=False)
    if positions.shape != (result.vertex_count, 3) or not np.isfinite(positions).all():
        raise ValueError("Newton result contains invalid positions")
    return result, np.asarray(positions, dtype=np.float32)


def _apply_result(message):
    result, positions = _validate_artifact(message)
    if result.frame != _session.last_requested_frame:
        return  # never show a stale later/earlier frame as the current request
    started = time.perf_counter()
    offset = 0
    for captured, cloth in zip(_session.capture.cloths,
                               _session.capture.request.cloths):
        preview = captured.preview
        if preview.name not in bpy.data.objects:
            raise RuntimeError("Newton preview object was removed")
        count = len(cloth.mesh.vertices)
        preview.data.vertices.foreach_set(
            "co", positions[offset:offset + count].reshape(-1))
        preview.data.update()
        offset += count
    _session.last_applied_frame = result.frame
    _session.status_data["newton_viewport_apply_seconds"] = time.perf_counter() - started
    current = int(_session.status_data.get("current_frame", result.frame))
    target = int(_session.status_data.get("target_frame", result.frame))
    if current != target:
        target_state = PreviewState.CATCHING_UP
    elif _session.has_played and not _animation_playing():
        target_state = PreviewState.PAUSED
    elif _session.has_played:
        target_state = PreviewState.PLAYING
    else:
        target_state = PreviewState.READY
    _session.set_state(target_state)


def _poll_timer():
    if _session.state is PreviewState.DISABLED:
        return None
    try:
        if (_session.capture is not None
                and _session.state not in {PreviewState.STALE,
                                           PreviewState.STOPPING,
                                           PreviewState.FAILED}):
            cloths, colliders = _validate_scope(bpy.context.scene)
            current_identity = _cheap_identity(
                bpy.context.scene, cloths, colliders, _settings())
            if current_identity != _session.capture.cheap_identity:
                _session.error = "Simulation settings changed; disable and re-enable Live Preview"
                _session.set_state(PreviewState.STALE)
                return _TIMER_INTERVAL
        if _session.state is PreviewState.STARTING_WORKER:
            try: client, health, error = _session.start_result.get_nowait()
            except queue.Empty: return _TIMER_INTERVAL
            if error is not None:
                raise error
            _session.client = client
            _session.status_data.update(health or {})
            _session.set_state(PreviewState.BUILDING_MODEL)
            _session.sender_thread = threading.Thread(
                target=_sender, args=(_session,), daemon=True,
                name="clothnext-newton-targets")
            _session.sender_thread.start()
        client = _session.client
        if client is not None:
            if client.process is None or client.process.poll() is not None:
                raise RuntimeError(client.failure_details())
            for _ in range(32):
                message = client.poll()
                if message is None: break
                event = message.get("event")
                if event == "error":
                    raise RuntimeError(message.get("message", "Newton worker error"))
                if event == "created":
                    _session.status_data.update(message)
                    _session.set_state(PreviewState.READY)
                    start_frame = _session.capture.request.frame_start
                    _session.last_requested_frame = start_frame
                elif event == "status":
                    _session.status_data.update(message)
                    _sync_settings(_session)
                elif event == "result":
                    _apply_result(message)
        playing = _animation_playing()
        if playing and not _session.was_playing:
            _session.has_played = True
            if _session.state in {PreviewState.READY, PreviewState.PAUSED}:
                _session.set_state(PreviewState.PLAYING)
        elif (not playing and _session.was_playing
              and _session.state in {PreviewState.PLAYING,
                                     PreviewState.CATCHING_UP}):
            if client is not None:
                client.send("pause")
            if (_session.last_applied_frame == _session.last_requested_frame):
                _session.set_state(PreviewState.PAUSED)
        _session.was_playing = playing
        return _TIMER_INTERVAL
    except Exception as exc:
        _fail(str(exc))
        return None


def _cleanup_preview():
    captured = _session.capture
    if captured is None:
        return
    for item in captured.cloths:
        try:
            _mark_owned_visibility_update(item.source)
            item.source.hide_viewport = item.source_hide_viewport
        except (ReferenceError, AttributeError):
            pass
        try:
            mesh = item.preview.data
            bpy.data.objects.remove(item.preview, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        except (ReferenceError, RuntimeError, AttributeError):
            pass


def _fail(message):
    _session.error = str(message)
    _session.stop_event.set()
    try:
        if _session.state is not PreviewState.FAILED:
            _session.state = PreviewState.FAILED
            _sync_settings(_session)
    finally:
        _set_enabled_without_callback(False)
        _cleanup_preview()
        if _session.client is not None:
            threading.Thread(target=_session.client.shutdown, daemon=True).start()


def stop(*, wait=False):
    global _session
    if _session.state is PreviewState.DISABLED:
        return
    old = _session
    try:
        if old.state is not PreviewState.STOPPING:
            old.state = PreviewState.STOPPING
            _sync_settings(old)
        old.stop_event.set()
        _cleanup_preview()
    finally:
        _session = _Session()
        _set_enabled_without_callback(False)
        _sync_settings(_session)
        if bpy.app.timers.is_registered(_poll_timer):
            bpy.app.timers.unregister(_poll_timer)
    def finish():
        if old.client is not None:
            old.client.shutdown(grace=2.0)
        for thread in (old.start_thread, old.sender_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)
        if old.capture is not None:
            try:
                remove_owned_session(old.capture.request.result_directory)
            except (OSError, ValueError):
                pass
    if wait:
        finish()
    else:
        threading.Thread(target=finish, daemon=True,
                         name="clothnext-newton-stop").start()


def _cleanup_orphaned_preview_objects():
    """Remove stale preview data once Blender's normal data API is ready.

    During add-on registration Blender may expose ``_RestrictData`` instead
    of the ordinary ``bpy.data`` collections.  Returning a short timer delay
    keeps registration side-effect safe and performs the cleanup immediately
    after Blender releases that restriction.
    """
    data = getattr(bpy, "data", None)
    objects = getattr(data, "objects", None)
    meshes = getattr(data, "meshes", None)
    if objects is None or meshes is None:
        return 0.1
    try:
        candidates = tuple(objects)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return 0.1
    for obj in candidates:
        if callable(getattr(obj, "get", None)) and bool(
                obj.get(_PREVIEW_MARKER, False)):
            mesh = obj.data
            objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                meshes.remove(mesh)
    return None


def install():
    from . import validation_state
    collection = getattr(bpy.app.handlers, "frame_change_post", None)
    if collection is not None:
        while _frame_change_post in collection:
            collection.remove(_frame_change_post)
        collection.append(_frame_change_post)
    _depsgraph_update_post._clothnext_newton_preview_observer = True
    validation_state.add_depsgraph_observer(_depsgraph_update_post)
    # Registration can run under Blender's _RestrictData guard.  Attempt the
    # orphan cleanup without assuming scene data is available and defer it to
    # the main-thread timer when necessary.
    delay = _cleanup_orphaned_preview_objects()
    timers = getattr(bpy.app, "timers", None)
    if (delay is not None and timers is not None
            and not timers.is_registered(_cleanup_orphaned_preview_objects)):
        timers.register(
            _cleanup_orphaned_preview_objects, first_interval=delay)


def uninstall():
    from . import validation_state
    stop(wait=True)
    collection = getattr(bpy.app.handlers, "frame_change_post", None)
    if collection is not None:
        while _frame_change_post in collection:
            collection.remove(_frame_change_post)
    validation_state.remove_depsgraph_observer(_depsgraph_update_post)
    timers = getattr(bpy.app, "timers", None)
    if (timers is not None
            and timers.is_registered(_cleanup_orphaned_preview_objects)):
        timers.unregister(_cleanup_orphaned_preview_objects)
    _mesh_capture_cache.clear()
    _owned_visibility_updates.clear()
