# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine backend in Blender: capability check, scene capture and bake.

Everything that touches ``bpy`` runs on Blender's main thread, from a
``bpy.app.timers`` pump (the UI stays responsive; scripts and tests drive the
same pump with :func:`bake_blocking`). The pump:

1. validates the scene with the shared Cloth NeXt validation (one snapshot),
2. refuses features the AETHER Engine cannot simulate (never approximates them),
3. captures the rest state at Bake Start and then, a few frames per tick, the
   evaluated animated Colliders and animation-following Pins up to Bake End,
4. hands an immutable :class:`~cloth_next.aether_engine.bake.BakeRequest` to the
   worker thread, which runs ``AETHER Engine.exe`` and streams the PC2 caches,
5. attaches the published caches through the shared transactional playback
   import (Mesh Cache modifier, sidecar inspection, rollback on failure).

Progress, cancellation and errors go through the shared Bake controller and
the Bake window, exactly like the other Cloth NeXt bakes.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, asdict
import os
from pathlib import Path
import time

import bpy
import numpy as np

from ..aether_engine import bake as abake, engine as aengine, materials as amat

TITLE = "AETHER Engine"
QUALITY_ITEMS = (
    ("NORMAL", "Normal", "Interactive quality: the default for Live Simulation"),
    ("ANIMATION", "Animation", "Intermediate stability for moving characters"),
    ("FITTING", "Fitting", "Accurate fabric: recommended for final AETHER bakes"),
)
CAPTURE_BUDGET_S = 0.04          # main-thread time per capture tick
SEAM_CLOSURE_SECONDS = 1.0       # seams close gradually over this time

_status_cache = {"time": 0.0, "value": None}
_job: "AetherBakeJob | None" = None


class AetherSceneError(ValueError):
    def __init__(self, message: str, code: str = "CNX-E106"):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ engine status
@dataclass(frozen=True)
class EngineStatus:
    ready: bool
    title: str
    detail: str = ""
    code: str = ""
    identity: aengine.EngineIdentity | None = None


def engine_status(*, refresh: bool = False) -> EngineStatus:
    """Located and version-probed engine, cached for panel drawing (no GPU access)."""
    now = time.monotonic()
    cached = _status_cache["value"]
    if not refresh and cached is not None and now - _status_cache["time"] < 30.0:
        return cached
    try:
        identity = aengine.identify()
        value = EngineStatus(True, f"AETHER Engine {identity.version}",
                             f"build {identity.build_id}" + (" · developer override"
                                                            if identity.developer_override else ""),
                             identity=identity)
    except aengine.EngineUnavailable as exc:
        value = EngineStatus(False, str(exc), code=exc.code)
    _status_cache.update(time=now, value=value)
    return value


def quality_of(scene, *, live: bool = False) -> str:
    settings = getattr(scene, "cloth_next_solver", None)
    key = "aether_live_quality" if live else "aether_bake_quality"
    return str(getattr(settings, key, "NORMAL" if live else "FITTING")).lower()


# ------------------------------------------------------------------ capabilities
def _unsupported_object(obj, settings) -> str:
    role = str(settings.role)
    if role != "CLOTH":
        return f"{obj.name}: {role.replace('_', ' ').title()} is not supported by the AETHER Engine (Cloth only)"
    if obj.type != "MESH":
        return f"{obj.name}: the AETHER Engine simulates mesh Cloth objects"
    if len(getattr(settings, "material_zones", ())):
        return f"{obj.name}: Material Zones are not supported by the AETHER Engine yet"
    if len(getattr(settings, "friction_regions", ())):
        return f"{obj.name}: Friction Regions are not supported by the AETHER Engine yet"
    if getattr(settings, "water_flow_enabled", False):
        return f"{obj.name}: Water Flow needs GAIA; the AETHER Engine has no fluid coupling"
    if getattr(settings, "pinning_enabled", False):
        if str(getattr(settings, "pin_mode", "STATIC")) == "TARGET_OBJECT":
            return f"{obj.name}: Pin Target Objects are not supported by the AETHER Engine yet"
        if (getattr(settings, "advanced_pin_motion_enabled", False) or len(getattr(settings, "advanced_pin_targets", ()))
                or len(getattr(settings, "soft_constraints", ()))):
            return f"{obj.name}: Advanced Pin Motion and Soft Constraints are not supported by the AETHER Engine yet"
    return ""


def not_enforced_notes(context) -> list[str]:
    """Property-only list of material controls an AETHER bake will not enforce."""
    notes = []
    from . import object_properties
    for obj in context.scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is None or not settings.enabled or settings.role != "CLOTH":
            continue
        try:
            notes += [f"{obj.name}: {n}" for n in amat.not_enforced(object_properties.shell_settings_from(settings))]
        except Exception:  # noqa: BLE001 - invalid values are reported by validation
            continue
    return notes


def unsupported_reason(context) -> str:
    """Cheap, property-only refusal reason for the AETHER backend ('' = supported)."""
    status = engine_status()
    if not status.ready:
        return status.title
    scene = context.scene
    deformables = 0
    for obj in scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is None or not settings.enabled:
            continue
        if settings.role == "FORCE":
            return f"{obj.name}: Forces are not supported by the AETHER Engine yet (only scene gravity)"
        if settings.role in {"CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY"}:
            deformables += 1
            reason = _unsupported_object(obj, settings)
            if reason:
                return reason
            try:
                from . import object_properties
                missing = amat.unsupported_controls(object_properties.shell_settings_from(settings))
            except Exception:  # noqa: BLE001 - invalid values are reported by validation
                missing = []
            if missing:
                return f"{obj.name}: not supported by the AETHER Engine: {', '.join(missing)}"
    if any(getattr(item, "enabled", False) for item in getattr(scene, "cloth_next_object_attachments", ())):
        return "Object Attachments are not supported by the AETHER Engine yet"
    if not deformables:
        return ""
    return ""


# ------------------------------------------------------------------ capture helpers
def _evaluated(obj, depsgraph) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(local (V,3) f32, world (V,3) f32, triangles (T*3,) i32) of the evaluated mesh."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        count = len(mesh.vertices)
        local = np.empty(count * 3, np.float32)
        mesh.vertices.foreach_get("co", local)
        local = local.reshape(-1, 3)
        mesh.calc_loop_triangles()
        tris = np.empty(len(mesh.loop_triangles) * 3, np.int32)
        mesh.loop_triangles.foreach_get("vertices", tris)
        matrix = np.array(evaluated.matrix_world, dtype=np.float64)
    finally:
        evaluated.to_mesh_clear()
    world = (local.astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]).astype(np.float32)
    return local, world, tris


def _evaluated_world(obj, depsgraph, count: int) -> np.ndarray:
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        if len(mesh.vertices) != count:
            raise AetherSceneError(f"{obj.name}: the evaluated vertex count changes during the Bake range; "
                                   "keep the topology constant", "CNX-E103")
        local = np.empty(count * 3, np.float32)
        mesh.vertices.foreach_get("co", local)
        matrix = np.array(evaluated.matrix_world, dtype=np.float64)
    finally:
        evaluated.to_mesh_clear()
    local = local.reshape(-1, 3).astype(np.float64)
    return (local @ matrix[:3, :3].T + matrix[:3, 3]).astype(np.float32)


def _animated_transform(obj) -> bool:
    animation = getattr(obj, "animation_data", None)
    action = getattr(animation, "action", None) if animation else None
    if obj.parent is not None or len(obj.constraints):
        return True
    if action is None:
        return False
    names = {"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
             "delta_location", "delta_rotation_euler", "delta_scale"}
    curves = getattr(action, "fcurves", None)
    if curves is None:
        curves = [c for layer in getattr(action, "layers", ()) for strip in getattr(layer, "strips", ())
                  for bag in getattr(strip, "channelbags", ()) for c in getattr(bag, "fcurves", ())]
    return any(getattr(c, "data_path", "") in names for c in curves)


def _sewing(scene, uuids_to_index: dict, objects_by_id: dict, materials_by_index: dict, fps: float):
    from . import sewing as sewing_ui
    seams = []
    for item in getattr(scene, "cloth_next_sewing_definitions", ()):
        if not item.enabled or float(item.strength) <= 0.0:
            continue
        source = objects_by_id.get(str(item.source_persistent_id))
        target = objects_by_id.get(str(item.target_persistent_id))
        if source is None or target is None:
            raise AetherSceneError(f"{item.name}: both sewn objects must be enabled Cloth objects", "CNX-E105")
        a, b = uuids_to_index[source], uuids_to_index[target]
        side_a, side_b = sewing_ui._sides(item)
        chain_b = tuple(reversed(side_b)) if item.flipped else tuple(side_b)
        stiffness = amat.seam_stiffness(float(item.strength), materials_by_index[a], materials_by_index[b])
        seams.append(abake.SeamInput(a, b, tuple(int(v) for v in side_a), tuple(int(v) for v in chain_b),
                                     stiffness, max(1, int(round(SEAM_CLOSURE_SECONDS * fps)))))
    return tuple(seams)


def pin_stiffness(pull_strength: float, material: amat.AetherMaterial) -> float:
    """Soft Pin pull strength -> compliant attachment stiffness (N/m).

    Approximate: 1.0 pulls with ten times the cloth's membrane modulus, the
    same scale as a default seam."""
    mu = material.youngs / (2.0 * (1.0 + material.poisson))
    lam = material.youngs * material.poisson / (1.0 - material.poisson ** 2)
    return float(pull_strength) * 10.0 * (2.0 * mu + lam)


# ------------------------------------------------------------------ preparation
@dataclass
class _ClothCapture:
    obj_name: str
    uuid: str
    entry: object
    local: np.ndarray
    world: np.ndarray
    triangles: np.ndarray
    matrix: np.ndarray
    material: amat.AetherMaterial
    registered: np.ndarray
    pin_vertices: np.ndarray
    pin_stiffness: float
    follow: bool
    pin_targets: list
    pc2_path: Path
    meta: dict


def _cache_paths(obj, uuid: str) -> Path:
    configured = str(getattr(obj.cloth_next, "cache_directory", "") or "").strip()
    from .solver_test import _cache_directory
    root = Path(bpy.path.abspath(configured)) if configured else _cache_directory()
    return root / f"cn_aether_{uuid[:16]}.pc2"


class AetherPreparation:
    """Main-thread capture as a resumable state machine (one ``step()`` per tick)."""

    def __init__(self, context, *, quality: str | None = None, checkpoint_interval: int | None = None,
                 live: bool = False):
        from . import solver_test
        from .. import manifest_version
        from ..export_identity import export_uuid
        from .playback_cache import without_owned_playback
        self.context = context
        scene = context.scene
        self.scene = scene
        self.quality = quality or quality_of(scene)
        settings = getattr(scene, "cloth_next_solver", None)
        self.interval = int(checkpoint_interval or getattr(settings, "aether_checkpoint_interval", 24) or 24)
        reason = unsupported_reason(context)
        if reason:
            raise AetherSceneError(reason)
        snapshot = solver_test.validate_scene(context)
        if not live:
            solver_test._require_cache_directories(tuple(e.obj for e in snapshot.deformables))
        if any(abs(v) > 0.0 for v in snapshot.wind_blender):
            raise AetherSceneError("Wind is not supported by the AETHER Engine yet")
        sewing_ids = {str(item.identifier) for item in getattr(scene, "cloth_next_sewing_definitions", ())}
        attachments = [a for a, _s, _t in snapshot.object_attachments
                       if str(getattr(a, "identifier", "")).split(":", 1)[0] not in sewing_ids]
        self.snapshot = snapshot
        self.fps = solver_test._scene_fps(context)
        self.range = snapshot.bake_range
        self.live = live
        self.start_frame = int(scene.frame_current) if live else self.range.start
        self.original_frame = (int(scene.frame_current), float(getattr(scene, "frame_subframe", 0.0)))
        self.settings_fp = abake.settings_fingerprint(snapshot.settings_fingerprint, self.quality)
        self.geometry_fp = snapshot.geometry_fingerprint
        self.combined_fp = solver_test.bake_fingerprint(self.settings_fp, self.geometry_fp)
        self.cloth_next_version = manifest_version()
        self.blender_version = solver_test._blender_version()
        self.stack = ExitStack()
        self.cloths: list[_ClothCapture] = []
        self.notes: list[str] = []
        self.colliders: list[dict] = []
        self.frame = self.range.start + 1
        self.started = time.monotonic()
        try:
            depsgraph = context.evaluated_depsgraph_get()
            for entry in snapshot.deformables:
                self.stack.enter_context(without_owned_playback(entry.obj, update=depsgraph.update))
            scene.frame_set(self.start_frame)
            depsgraph = context.evaluated_depsgraph_get()
            object_ids = {}
            for index, entry in enumerate(snapshot.deformables):
                obj = entry.obj
                if _animated_transform(obj):
                    raise AetherSceneError(f"{obj.name}: an animated, parented or constrained Cloth object is not "
                                           "supported by the AETHER Engine; animate it through Pins instead",
                                           "CNX-E106")
                local, world, tris = _evaluated(obj, depsgraph)
                if len(local) != len(obj.data.vertices):
                    raise AetherSceneError(f"{obj.name}: modifiers change the vertex count; apply them before "
                                           "baking with the AETHER Engine", "CNX-E103")
                if not np.isfinite(world).all():
                    raise AetherSceneError(f"{obj.name}: non-finite vertex coordinates", "CNX-E109")
                try:
                    material = amat.map_material(entry.material)
                except amat.UnsupportedMaterial as exc:
                    raise AetherSceneError(f"{obj.name}: {exc}", "CNX-E104") from exc
                pins = entry.pin_membership
                registered = np.zeros(0, np.int32)
                pin_vertices = np.zeros(0, np.int32)
                stiffness = 0.0
                follow = False
                if pins.enabled:
                    indices = np.asarray(pins.vertex_indices, np.int32)
                    hard = str(getattr(pins.constraint_type, "value", pins.constraint_type)) == "HARD"
                    # the validation snapshot carries no motion samples; the mode is the object's setting
                    follow = str(getattr(obj.cloth_next, "pin_mode", "STATIC")) == "FOLLOW_ANIMATION"
                    if hard and not follow:
                        registered = indices
                    else:
                        pin_vertices = indices
                        stiffness = 0.0 if hard else pin_stiffness(pins.pull_strength, material)
                for note in amat.not_enforced(entry.material):
                    self.notes.append(f"{obj.name}: {note}")
                uuid = export_uuid(obj)
                object_ids[str(obj.cloth_next.persistent_export_id)] = obj
                matrix = np.array(obj.matrix_world, dtype=np.float64)
                pc2_path = _cache_paths(obj, uuid)
                meta = self._meta(entry, material, len(local))
                self.cloths.append(_ClothCapture(obj.name, uuid, entry, local, world, tris, matrix, material,
                                                 registered, pin_vertices, stiffness, follow, [], pc2_path, meta))
            from . import object_properties
            for obj in snapshot.collider_objs:
                local, world, tris = _evaluated(obj, depsgraph)
                animated = str(object_properties.collider_motion_from(obj.cloth_next)) == "ANIMATED"
                self.colliders.append({"name": obj.name, "obj": obj, "world": world, "triangles": tris,
                                       "animated": animated, "frames": [], "count": len(world)})
            index_of = {self.cloths[i].entry.obj: i for i in range(len(self.cloths))}
            self.seams = _sewing(scene, index_of, object_ids,
                                 {i: c.material for i, c in enumerate(self.cloths)}, self.fps)
            if attachments:
                raise AetherSceneError("Object Attachments are not supported by the AETHER Engine yet")
        except Exception:
            self.close()
            raise

    def _meta(self, entry, material: amat.AetherMaterial, vertex_count: int) -> dict:
        from . import validation_state
        object_identity = {"object_key": validation_state.object_key(entry.obj), "deformable_type": entry.role,
                           "topology_signature": entry.topology_signature,
                           "geometry_fingerprint": self.geometry_fp}
        scene_identity = {"backend": "AETHER_ENGINE", "quality": self.quality,
                          "settings_fingerprint": self.settings_fp, "geometry_fingerprint": self.geometry_fp,
                          "fps": self.fps, "frame_start": self.range.start, "frame_end": self.range.end}
        pins = entry.pin_membership
        details = {
            "backend": "AETHER_ENGINE", "preset": entry.preset_identifier, "deformable_type": entry.role,
            "material": asdict(entry.material),
            "aether": {"quality": self.quality, "mapping_version": amat.MAPPING_VERSION,
                       "material": material.as_protocol(), "not_enforced": amat.not_enforced(entry.material)},
            "blender_start_frame": self.range.start, "blender_end_frame": self.range.end,
            "output_frame_count": self.range.output_count, "solver_step_count": self.range.solver_steps,
            "fps": self.fps,
            "pinning": {"enabled": pins.enabled, "mode": str(getattr(pins.mode, "value", pins.mode)),
                        "group": pins.group_name, "count": len(pins.vertex_indices),
                        "threshold": pins.threshold, "fingerprint": pins.fingerprint},
        }
        return abake.cache_meta(settings_fp=self.settings_fp, geometry_fp=self.geometry_fp,
                                combined_fp=self.combined_fp, topology=entry.topology_signature,
                                object_identity=object_identity, scene_identity=scene_identity,
                                vertex_count=vertex_count, frame_count=self.range.output_count, details=details,
                                cloth_next_version=self.cloth_next_version, blender_version=self.blender_version)

    @property
    def needs_timeline(self) -> bool:
        return any(c["animated"] for c in self.colliders) or any(c.follow for c in self.cloths)

    @property
    def total(self) -> int:
        return self.range.solver_steps

    @property
    def done_frames(self) -> int:
        return self.frame - self.range.start - 1

    def step(self, budget: float = CAPTURE_BUDGET_S) -> bool:
        """Capture frames until the budget is used; True when the capture is complete."""
        if not self.needs_timeline:
            self.frame = self.range.end + 1
            return True
        t0 = time.monotonic()
        scene = self.scene
        while self.frame <= self.range.end:
            scene.frame_set(self.frame)
            depsgraph = self.context.evaluated_depsgraph_get()
            for c in self.colliders:
                if c["animated"]:
                    c["frames"].append(_evaluated_world(c["obj"], depsgraph, c["count"]))
            for c in self.cloths:
                if c.follow:
                    world = _evaluated_world(c.entry.obj, depsgraph, len(c.local))
                    c.pin_targets.append(world[c.pin_vertices])
            self.frame += 1
            if time.monotonic() - t0 > budget:
                break
        return self.frame > self.range.end

    def close(self) -> None:
        """Restore the timeline and the muted playback modifiers (idempotent)."""
        try:
            self.stack.close()
        finally:
            frame, sub = getattr(self, "original_frame", (None, 0.0))
            if frame is not None:
                try:
                    self.scene.frame_set(frame, subframe=sub)
                except TypeError:
                    self.scene.frame_set(frame)

    def request(self) -> abake.BakeRequest:
        cloths = []
        pins = []
        for i, c in enumerate(self.cloths):
            cloths.append(abake.ClothInput(c.uuid, c.obj_name, c.world, c.local, c.triangles, c.matrix,
                                           c.material, c.registered, c.pc2_path, c.meta))
            if len(c.pin_vertices):
                targets = (np.stack(c.pin_targets).astype(np.float32)
                           if c.follow and c.pin_targets else None)
                pins.append(abake.PinTrack(i, c.pin_vertices, c.pin_stiffness, targets))
        colliders = tuple(abake.ColliderInput(c["name"], c["triangles"], c["world"],
                                              np.stack(c["frames"]).astype(np.float32) if c["animated"] else None)
                          for c in self.colliders)
        first = self.cloths[0].pc2_path.parent
        folder = first / f"cn_aether_recovery_{self.cloths[0].uuid[:16]}"
        return abake.BakeRequest(
            tuple(cloths), colliders, tuple(pins), self.seams, self.quality, float(self.fps),
            tuple(float(v) for v in self.snapshot.gravity_blender), bool(self.snapshot.contact_enabled),
            self.range.start, self.range.end, folder, folder.with_name(folder.name + ".json"),
            checkpoint_interval=self.interval, settings_fingerprint=self.settings_fp,
            geometry_fingerprint=self.geometry_fp, cloth_next_version=self.cloth_next_version,
            blender_version=self.blender_version)


# ------------------------------------------------------------------ playback attach
def attach(request: abake.BakeRequest, headers: dict, preset_identifier: str) -> None:
    """Publish the finished caches through the shared transactional playback import."""
    from . import solver_test
    targets = tuple(solver_test.DeformablePlan(
        c.rest_local, tuple(tuple(float(v) for v in row) for row in c.world_matrix), c.object_name, c.uuid,
        c.pc2_path, c.material_meta["fingerprints"]["topology"], c.material_meta, "CLOTH")
        for c in request.cloths)
    first = targets[0]
    plan = solver_test.RunPlan(
        scene=None, resolved=None, initial_local=first.initial_local, world_matrix=first.world_matrix,
        cloth_object_name=first.object_name, work_directory=request.checkpoint_dir, pc2_path=first.pc2_path,
        frame_count=request.output_count, frame_start=request.frame_start, frame_end=request.frame_end,
        fps=request.fps, settings_fingerprint=request.settings_fingerprint,
        geometry_fingerprint=request.geometry_fingerprint, topology_signature=first.topology_signature,
        preset_identifier=preset_identifier, material_meta=first.material_meta, deformables=targets,
        backend_id="AETHER_ENGINE")
    header = headers[first.uuid] if len(targets) == 1 else headers
    solver_test._attach_playback(plan, header)


# ------------------------------------------------------------------ bake job (timer pump)
class AetherBakeJob:
    """One AETHER bake: capture (main thread) -> engine (worker) -> attach (main thread)."""

    PUBLISH_SECONDS = 0.25

    def __init__(self, context, *, resume: bool = False, quality: str | None = None,
                 checkpoint_interval: int | None = None, open_window: bool | None = None,
                 testing_env: dict | None = None):
        from ..bake.controller import shared_controller
        from ..bake.status import BakeJobKind, BakeState
        from . import modal_lock
        self.controller = shared_controller
        self.resume = resume
        self.stage = "CAPTURE"
        self.message = ""
        self.level = ""
        self.runner: abake.BakeRunner | None = None
        self.preparation: AetherPreparation | None = None
        self.request: abake.BakeRequest | None = None
        self.testing_env = testing_env
        self.started = time.monotonic()
        self._sent = 0.0
        status = engine_status(refresh=True)
        if not status.ready:
            raise AetherSceneError(status.title, status.code or "CNX-E131")
        self.executable = status.identity.path
        owner = shared_controller.snapshot()
        if owner.active:
            raise AetherSceneError("A Cloth NeXt Bake is already running", "CNX-E169")
        if owner.state is not BakeState.IDLE:
            shared_controller.reset()
        self.job_id = shared_controller.transition(
            BakeState.PREPARING, job_kind=BakeJobKind.BAKE, status_title=TITLE,
            status_message="Validating the scene for the AETHER Engine", solver_name=TITLE,
            solver_backend="AETHER_ENGINE").job_id
        if not modal_lock.reserve(self.job_id):
            shared_controller.fail("Another Cloth NeXt Bake owns the window")
            raise AetherSceneError("Another Cloth NeXt Bake owns the window", "CNX-E169")
        try:
            self.preparation = AetherPreparation(context, quality=quality, checkpoint_interval=checkpoint_interval)
        except Exception as exc:
            self._fail_preparation(exc)
            raise
        self.preset_identifier = self.preparation.snapshot.preset_identifier
        shared_controller.update(frame_start=self.preparation.range.start, frame_end=self.preparation.range.end,
                                 status_message="Capturing animated inputs for the AETHER Engine",
                                 progress_current=0, progress_total=max(1, self.preparation.total))
        self.window = _window_enabled(context) if open_window is None else open_window
        self.window_deadline = 0.0
        if self.window:
            from ..bake.transport import EnterBakeMode
            from . import companion_manager
            shared_controller.transition(BakeState.STARTING_COMPANION, status_title=TITLE)
            ok, message = companion_manager.begin_bake_mode(EnterBakeMode(
                job_id=self.job_id, blender_process_id=os.getpid(),
                frame_start=self.preparation.range.start, frame_end=self.preparation.range.end,
                preset_label=f"{TITLE} · {self.preparation.quality.title()}"))
            if not ok:
                self._fail_preparation(AetherSceneError(message, "CNX-E110"))
                raise AetherSceneError(message, "CNX-E110")
            shared_controller.transition(BakeState.WAITING_FOR_COMPANION, status_title=TITLE,
                                         status_message="Opening the Bake window")
            self.window_deadline = time.monotonic() + companion_manager.STARTUP_TIMEOUT_SECONDS
        else:
            shared_controller.transition(BakeState.STARTING_RUN, status_title=TITLE)

    # -------------------------------------------------- state
    def owns(self) -> bool:
        return self.controller.snapshot().job_id == self.job_id

    def cancel_requested(self) -> bool:
        from ..bake.status import BakeState
        s = self.controller.snapshot()
        return s.job_id != self.job_id or s.state in {BakeState.CANCELLING, BakeState.CANCELLED}

    @property
    def done(self) -> bool:
        return self.stage == "DONE"

    def cancel(self) -> None:
        from ..bake.status import BakeState
        if self.runner is not None:
            self.runner.cancel()
        if self.owns() and self.controller.snapshot().state not in {BakeState.CANCELLING, BakeState.CANCELLED}:
            self.controller.request_cancel()

    # -------------------------------------------------- pump (main thread)
    def tick(self) -> float | None:
        """Advance; returns the next timer interval, or None when finished."""
        from ..bake.status import BakeState
        try:
            if self.stage == "CAPTURE":
                if self.cancel_requested():
                    return self._end_cancelled("AETHER bake cancelled")
                if self.window and not self._window_ready():
                    return 0.1
                finished = self.preparation.step()
                self._publish(BakeState.EXPORTING, "Capturing animated Colliders and Pins",
                              self.preparation.done_frames, self.preparation.total)
                if not finished:
                    return 0.0
                self.request = self.preparation.request()
                self.preparation.close()
                if self.resume:
                    reason = abake.check_recovery(abake.load_recovery(self.request.recovery_path), self.request)
                    if reason:
                        raise AetherSceneError(reason, "CNX-E129")
                else:
                    abake.discard_recovery(self.request)
                self.controller.transition(BakeState.STARTING_SOLVER, status_title=TITLE,
                                           status_message="Starting the AETHER Engine", solver_name=TITLE,
                                           solver_backend="AETHER_ENGINE")
                log = self.request.checkpoint_dir.parent / f"{self.request.checkpoint_dir.name}.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                self.runner = abake.BakeRunner(self.request, self.executable, resume=self.resume, log_path=log,
                                               env=self.testing_env, testing=bool(self.testing_env))
                self.stage = "ENGINE"
                return 0.1
            if self.stage == "ENGINE":
                runner = self.runner
                if self.cancel_requested():
                    runner.cancel()
                if not runner.done:
                    p = runner.progress
                    self._publish(BakeState.SIMULATING, p.phase, p.frames_done, p.frames_total,
                                  detail=(f"resumed at frame {self.request.frame_start + p.resumed_from}"
                                          if p.resumed_from is not None else ""))
                    return 0.1
                if runner.cancelled:
                    return self._end_cancelled("AETHER bake cancelled · Resume continues from the last checkpoint")
                if runner.error is not None:
                    return self._end_error(runner.error)
                self.controller.transition(BakeState.IMPORTING, status_title=TITLE,
                                           status_message="Attaching the playback caches")
                attach(self.request, runner.headers, self.preset_identifier)
                return self._end_finished(runner)
        except Exception as exc:  # noqa: BLE001 - every failure ends the bake visibly
            if self.preparation is not None:
                self.preparation.close()
            message = exc.record.user_message if hasattr(exc, "record") else str(exc)
            return self._end_error(abake.AetherBakeError(message, getattr(exc, "code", "")))
        return None

    def _window_ready(self) -> bool:
        from ..bake.status import BakeState
        from . import companion_manager
        status, message = companion_manager.startup_status(self.job_id)
        if status != "READY":
            if status in {"ERROR", "CANCELLED"} or time.monotonic() >= self.window_deadline:
                raise AetherSceneError(message or "The Bake window did not open", "CNX-E114")
            return False
        if not companion_manager.consume_ready(self.job_id):
            return False
        self.controller.transition(BakeState.COMPANION_READY, status_title=TITLE)
        self.controller.transition(BakeState.STARTING_RUN, status_title=TITLE)
        self.window = False
        return True

    def _publish(self, state, phase: str, done: int, total: int, detail: str = "") -> None:
        if self.cancel_requested():
            return
        now = time.monotonic()
        current = self.controller.snapshot().state
        if state == current and now - self._sent < self.PUBLISH_SECONDS:
            return
        from ..bake.status import BakeState
        if state is BakeState.EXPORTING and current is not BakeState.EXPORTING:
            self.controller.transition(BakeState.EXPORTING, status_title=TITLE)
        if state is BakeState.SIMULATING and current is BakeState.STARTING_SOLVER:
            self.controller.transition(BakeState.SIMULATING, status_title=TITLE)
        first = self.request.frame_start if self.request else self.preparation.range.start
        label = f"{phase} · {min(done, total)} / {total}" + (f" · {detail}" if detail else "")
        self.controller.update(status_title=TITLE, status_message=phase, activity_label=label,
                               progress_current=int(done), progress_total=max(1, int(total)),
                               current_frame=first + int(done), elapsed_seconds=now - self.started)
        self._sent = now

    def _release(self) -> None:
        from . import modal_lock
        global _job
        modal_lock.release(self.job_id)
        if _job is self:
            _job = None
        self.stage = "DONE"
        _tag_redraw()

    def _fail_preparation(self, exc: Exception) -> None:
        from . import companion_manager, modal_lock
        code = getattr(exc, "code", "")  # '' lets the shared classifier pick the specific code
        message = exc.record.user_message if hasattr(exc, "record") else str(exc)
        if self.owns():
            self.controller.fail(message, "Stage: AETHER Engine scene preparation", error_code=code)
            companion_manager.persist_bake_error(self.controller.snapshot())
        modal_lock.release(self.job_id)

    def _end_finished(self, runner: abake.BakeRunner) -> None:
        from ..bake.status import BakeState
        p = runner.progress
        self.message = (f"AETHER bake finished: {self.request.output_count} frames in "
                        f"{time.monotonic() - self.started:.1f} s"
                        + (f" (resumed at frame {self.request.frame_start + p.resumed_from}, "
                           f"{p.verified} frames verified)" if p.resumed_from is not None else "")
                        + (" · Not enforced: " + "; ".join(self.preparation.notes)
                           if self.preparation is not None and self.preparation.notes else ""))
        self.level = "INFO"
        if self.owns():
            self.controller.transition(BakeState.FINISHED, status_title="AETHER bake finished",
                                       status_message=self.message, activity_label="Playback cache ready",
                                       progress_current=p.frames_total, progress_total=max(1, p.frames_total),
                                       estimated_remaining_seconds=None,
                                       elapsed_seconds=time.monotonic() - self.started)
        self._release()
        return None

    def _end_cancelled(self, message: str) -> None:
        from ..bake.status import BakeState
        from . import companion_manager
        if self.preparation is not None:
            self.preparation.close()
        self.message, self.level = message, "WARNING"
        if self.owns():
            companion_manager.cancel_startup(self.job_id, message)
            s = self.controller.snapshot()
            if s.state is not BakeState.CANCELLED:
                if s.state is not BakeState.CANCELLING:
                    self.controller.request_cancel()
                self.controller.transition(BakeState.CANCELLED, status_title="AETHER bake cancelled",
                                           status_message=message)
        self._release()
        return None

    def _end_error(self, error: abake.AetherBakeError) -> None:
        from . import companion_manager
        self.message, self.level = f"{error.code} {error}".strip(), "ERROR"
        if self.owns():
            details = "Stage: AETHER Engine bake"
            if error.details:
                details += "\n" + error.details[-1500:]
            self.controller.fail(str(error), details, error_code=error.code)
            companion_manager.persist_bake_error(self.controller.snapshot())
        self._release()
        return None


def _window_enabled(context) -> bool:
    from .addon_identity import addon_preferences
    try:
        return bool(addon_preferences(context, __package__).auto_launch_bake_window)
    except (KeyError, AttributeError):
        return True


def _tag_redraw() -> None:
    for window in getattr(getattr(bpy.context, "window_manager", None), "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if area.type in {"PROPERTIES", "VIEW_3D"}:
                area.tag_redraw()


def _pump():
    job = _job
    if job is None:
        return None
    interval = job.tick()
    if job.done:
        return None
    return interval if interval is not None else 0.1


def begin_bake(context, *, resume: bool = False) -> tuple[str, bool]:
    """Start an AETHER bake from the shared Bake button (returns job id, window?)."""
    global _job
    if _job is not None:
        raise AetherSceneError("An AETHER bake is already running", "CNX-E169")
    from .physics_ui import bake_location
    if bake_location(context) == "CLOUD":
        raise AetherSceneError("AETHER Engine bakes run on this computer; switch Bake to This Computer",
                               "CNX-E207")
    from . import aether_engine_live
    if aether_engine_live.is_running():
        raise AetherSceneError("Stop the Live Simulation before baking", "CNX-E169")
    job = AetherBakeJob(context, resume=resume)
    _job = job
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    bpy.app.timers.register(_pump, first_interval=0.0)
    return job.job_id, bool(job.window)


def bake_blocking(context, *, resume: bool = False, cancel_after_frames: int | None = None,
                  on_tick=None, **kw) -> AetherBakeJob:
    """Run a complete AETHER bake without the event loop (scripts, background Blender, tests)."""
    global _job
    job = AetherBakeJob(context, resume=resume, open_window=False, **kw)
    _job = job
    try:
        while not job.done:
            if (cancel_after_frames is not None and job.runner is not None
                    and job.runner.progress.frames_done >= cancel_after_frames):
                job.cancel()
            if on_tick is not None:
                on_tick(job)
            interval = job.tick()
            if not job.done:
                time.sleep(min(0.05, interval or 0.0))
        return job
    finally:
        if _job is job:
            _job = None


def is_running() -> bool:
    return _job is not None


def cancel_from_window() -> bool:
    """Bake-window Cancel; True when it was meant for the AETHER bake."""
    job = _job
    if job is None or not job.owns():
        return False
    job.cancel()
    return True


def recovery_available(context) -> bool:
    """Cheap: does an AETHER recovery record exist for the scene's first cloth? (stat only, cached)."""
    now = time.monotonic()
    key = id(context.scene)
    cached = _recovery_cache.get(key)
    if cached and now - cached[0] < 2.0:
        return cached[1]
    found = False
    try:
        from . import solver_test
        from ..export_identity import export_uuid
        cloths, _colliders = solver_test._enabled_objects_for_solve(context)
        first = sorted(cloths, key=export_uuid)[0]
        uuid = export_uuid(first)
        folder = _cache_paths(first, uuid).parent / f"cn_aether_recovery_{uuid[:16]}"
        found = folder.with_name(folder.name + ".json").is_file()
    except Exception:  # noqa: BLE001 - no scene, no cache folder: nothing to resume
        found = False
    _recovery_cache[key] = (now, found)
    return found


_recovery_cache: dict = {}


class CLOTHNEXT_OT_aether_resume_bake(bpy.types.Operator):
    bl_idname = "clothnext.aether_resume_bake"
    bl_label = "Resume Interrupted Bake"
    bl_description = ("Continue the interrupted AETHER Engine bake from its last checkpoint. The frames already "
                      "in the cache are recomputed, verified bit for bit and kept")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        from ..bake.controller import shared_controller
        return _job is None and not shared_controller.snapshot().active

    def execute(self, context):
        try:
            begin_bake(context, resume=True)
        except Exception as exc:  # noqa: BLE001 - shown to the artist
            self.report({"ERROR"}, f"{getattr(exc, 'code', 'CNX-E129')} {exc}")
            return {"CANCELLED"}
        _recovery_cache.clear()
        self.report({"INFO"}, "Resuming the AETHER bake")
        return {"FINISHED"}


class CLOTHNEXT_OT_aether_discard_recovery(bpy.types.Operator):
    bl_idname = "clothnext.aether_discard_recovery"
    bl_label = "Discard Interrupted Bake"
    bl_description = "Delete the AETHER recovery checkpoints and the unfinished cache. Finished caches stay"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _job is None

    def execute(self, context):
        from . import solver_test
        from ..export_identity import export_uuid
        try:
            cloths, _c = solver_test._enabled_objects_for_solve(context)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        removed = 0
        for obj in cloths:
            uuid = export_uuid(obj)
            pc2_path = _cache_paths(obj, uuid)
            folder = pc2_path.parent / f"cn_aether_recovery_{uuid[:16]}"
            record = abake.load_recovery(folder.with_name(folder.name + ".json"))
            if record is None:
                continue
            for o in record.get("objects", ()):
                Path(o.get("partial", "")).unlink(missing_ok=True) if o.get("partial") else None
            for f in folder.glob("aether_*") if folder.is_dir() else ():
                f.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError:
                pass
            folder.with_name(folder.name + ".json").unlink(missing_ok=True)
            removed += 1
        _recovery_cache.clear()
        self.report({"INFO"}, "Interrupted AETHER bake discarded" if removed else "Nothing to discard")
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_aether_resume_bake, CLOTHNEXT_OT_aether_discard_recovery)


def shutdown() -> None:
    """Add-on unregister: stop a running engine (the job object also ends it)."""
    global _job
    job = _job
    if job is not None:
        job.cancel()
        if job.runner is not None:
            job.runner.join(15)
        if job.preparation is not None:
            job.preparation.close()
    _job = None
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
