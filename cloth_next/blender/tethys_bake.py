# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS wetness bake: prepare inputs, run the bundled engine, publish the cache.

Blender reads the authenticated cloth PC2 and the prepared GAIA water field,
writes versioned binary inputs and starts the native engine. The engine
writes the wetness cache; Blender authenticates it, converts it to a preview
image sequence and connects the materials. The cloth cache is only read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time

import bpy
import numpy as np

from ..bake import cache_metadata
from ..materials.zones import topology_signature
from ..tethys import cache as tcache
from ..tethys import contact_export, engine, profiles, protocol
from . import tethys_properties

_availability_cache = {"time": 0.0, "value": None}
_job = None  # the single running TETHYS bake (one at a time)


class TethysBakeError(RuntimeError):
    """Artist-readable refusal; nothing was started or changed."""


def tag_redraw() -> None:
    for window in getattr(getattr(bpy.context, "window_manager", None), "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if area.type in {"PROPERTIES", "VIEW_3D"}:
                area.tag_redraw()


_last_tick = 0.0  # last modal tick of the Bake Wetness operator
ORPHAN_SECONDS = 15.0


def _orphaned() -> bool:
    """A bake whose operator vanished (file load, closed window) without cancel():
    its engine thread is done and no modal tick came for a while."""
    if _job is None and _window_session is None:
        return False
    return (_job is None or _job.done) and time.monotonic() - _last_tick > ORPHAN_SECONDS


def _release_orphan():
    global _job
    if not _orphaned():
        return None
    _job = None
    if _window_session is not None:
        _window_session.end_cancelled()
    tag_redraw()
    return None


def is_running() -> bool:
    if _orphaned():
        # Never leave the panel (and every Cloth NeXt Bake) locked forever;
        # state changes leave the draw callback through a timer.
        if not bpy.app.timers.is_registered(_release_orphan):
            bpy.app.timers.register(_release_orphan, first_interval=0.0)
        return False
    return _job is not None


def availability(*, refresh: bool = False) -> engine.Availability:
    """GAIA selection + GAIA access, cached briefly for panel drawing."""
    now = time.monotonic()
    if not refresh and _availability_cache["value"] is not None and now - _availability_cache["time"] < 2.0:
        return _availability_cache["value"]
    from . import gaia_account, preferences
    registry, error = preferences._read_registry()
    if error:
        value = engine.Availability(False, "Solver registry cannot be read. Check Preferences")
    else:
        value = engine.gaia_availability(registry, gaia_account.access_granted())
    _availability_cache.update(time=now, value=value)
    return value


def cache_root(obj) -> Path:
    configured = str(getattr(obj.cloth_next, "cache_directory", "") or "").strip()
    if configured:
        return Path(bpy.path.abspath(configured))
    from .solver_test import _cache_directory
    return _cache_directory()


def object_uuid(obj) -> str:
    from ..export_identity import export_uuid
    return export_uuid(obj)


def final_paths(obj) -> tcache.CachePaths:
    return tcache.paths_for(cache_root(obj), object_uuid(obj))


def staging_paths(obj) -> tcache.CachePaths:
    final = final_paths(obj)
    return tcache.CachePaths(final.root, final.stem + "_staging")


# ----------------------------------------------------------------- inputs

@dataclass
class ClothSource:
    pc2_path: Path
    pc2_sha256: str
    first_frame: int
    frame_count: int
    vertex_count: int


def cloth_source(obj) -> ClothSource:
    from .playback_cache import has_cloth_next_playback_marker
    modifier = next((m for m in obj.modifiers if has_cloth_next_playback_marker(obj, m)), None)
    path = str(getattr(modifier, "filepath", "") or "") if modifier else ""
    if not path:
        raise TethysBakeError("Bake the cloth first. TETHYS runs on a finished cloth cache")
    pc2_path = Path(bpy.path.abspath(path))
    inspection = cache_metadata.inspect_cache(pc2_path)
    if inspection.condition is not cache_metadata.CacheCondition.READY:
        raise TethysBakeError(f"The cloth cache is not usable ({inspection.message}). Bake the cloth again")
    metadata = inspection.metadata
    vertex_count = int(metadata["expected"]["vertex_count"])
    if vertex_count != len(obj.data.vertices):
        raise TethysBakeError("The mesh no longer matches its cloth cache. Bake the cloth again")
    return ClothSource(pc2_path, str(metadata["cache_sha256"]), int(round(modifier.frame_start)),
                       int(metadata["expected"]["frame_count"]), vertex_count)


def zone_wetness(obj):
    zones = []
    for zone in getattr(obj.cloth_next, "material_zones", ()):
        overrides = tuple((o.key, float(o.value)) for o in zone.wetness_overrides)
        zones.append(profiles.ZoneWetness(int(zone.token), zone.name, bool(zone.enabled),
                                          zone.wetness_profile, overrides))
    return zones


def base_profile(obj) -> str:
    settings = obj.cloth_next.tethys
    if settings.profile == profiles.AUTO:
        return profiles.profile_for_preset(obj.cloth_next.material.preset)
    return settings.profile


def resolved_materials(obj, owners, triangle_polygons):
    settings = obj.cloth_next.tethys
    base_overrides = tuple((o.key, float(o.value)) for o in settings.overrides)
    return profiles.resolve_materials(obj.name, base_profile(obj), base_overrides, zone_wetness(obj),
                                      owners, triangle_polygons)


def engine_settings(context, obj) -> protocol.EngineSettings:
    s = obj.cloth_next.tethys
    if not s.contact_low < s.contact_high:
        raise TethysBakeError("Contact Start must be below Full Contact")
    from .water_flow import _scene_gravity
    try:
        gravity = tuple(float(v) for v in _scene_gravity(context))
    except Exception:
        gravity = tuple(context.scene.gravity) if context.scene.use_gravity else (0.0, 0.0, 0.0)
    fps = context.scene.render.fps / context.scene.render.fps_base
    return protocol.EngineSettings(
        fps=float(fps), gravity=gravity, max_substeps=tethys_properties.QUALITY_SUBSTEPS[s.quality],
        flow_strength=s.flow_strength, absorption=s.absorption, capillary=s.capillary,
        evaporation=s.evaporation, repellency=s.repellency, retention=s.retention,
        ambient_evaporation=s.ambient_drying * 1e-3 / 3600.0, contact_low=s.contact_low,
        contact_high=s.contact_high, immersion_depth=s.immersion_film * 1e-3,
        wetting_time=s.wetting_time, length_scale=1.0, debug_channels=s.debug_channels)


def mesh_topology(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    count = len(mesh.loop_triangles)
    if not count:
        raise TethysBakeError("The cloth mesh has no faces")
    triangles = np.empty(count * 3, np.int32)
    polygons = np.empty(count, np.int32)
    loops = np.empty(count * 3, np.int32)
    mesh.loop_triangles.foreach_get("vertices", triangles)
    mesh.loop_triangles.foreach_get("polygon_index", polygons)
    mesh.loop_triangles.foreach_get("loops", loops)
    uv = None
    layer = next((layer for layer in mesh.uv_layers if layer.active_render), None) or mesh.uv_layers.active
    if layer is not None:
        coordinates = np.empty(len(mesh.loops) * 2, np.float32)
        layer.data.foreach_get("uv", coordinates)
        uv = coordinates.reshape(-1, 2)[loops].reshape(count, 6)
    return triangles.reshape(-1, 3), polygons, uv


def zone_owners(obj):
    if not getattr(obj.cloth_next, "material_zones", ()):
        return (0,) * len(obj.data.polygons)
    from . import material_zones
    try:
        return material_zones.storage_owners(obj)
    except material_zones.ZoneError as exc:
        raise TethysBakeError(str(exc)) from exc


def settings_record(obj, context=None) -> dict:
    """Cheap record of everything wetness-only (no mesh or file reads)."""
    s = obj.cloth_next.tethys
    zones = sorted((z.token, z.enabled, z.profile, sorted(z.overrides)) for z in zone_wetness(obj))
    fps = (context or bpy.context).scene.render.fps / (context or bpy.context).scene.render.fps_base
    return {"profile": base_profile(obj), "overrides": sorted((o.key, round(o.value, 9)) for o in s.overrides),
            "zones": zones, "ownership": obj.cloth_next.material_zone_digest,
            "multipliers": [s.flow_strength, s.absorption, s.capillary, s.evaporation, s.repellency,
                            s.retention, s.ambient_drying, s.quality, s.immersion_film, s.wetting_time,
                            s.contact_low, s.contact_high, bool(s.debug_channels)],
            "detail": [s.surface_detail, s.texture_resolution, round(s.feature_size, 6)],
            "fps": fps}


@dataclass
class BakePlan:
    object_name: str
    request: protocol.EngineRequest
    contact: contact_export.ContactSource
    cloth: ClothSource
    final: tcache.CachePaths
    staging: tcache.CachePaths
    record: dict
    executable: Path
    polygon_count: int
    image_size: tuple[int, int]
    started: float = field(default_factory=time.monotonic)


def image_size(polygons: int) -> tuple[int, int]:
    width = max(1, min(4096, math.ceil(math.sqrt(polygons))))
    return width, max(1, math.ceil(polygons / width))


def _static_transform(obj) -> None:
    animation = getattr(obj, "animation_data", None)
    action = getattr(animation, "action", None) if animation else None
    transform = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                 "delta_location", "delta_rotation_euler", "delta_scale")
    curves = getattr(action, "fcurves", ()) if action else ()
    if any(getattr(c, "data_path", "") in transform for c in curves) or obj.parent or obj.constraints:
        raise TethysBakeError("TETHYS needs a cloth object without animated transform, parent or constraints")


def prepare(context, obj) -> BakePlan:
    """Main-thread snapshot of everything the engine needs. Raises TethysBakeError."""
    settings = obj.cloth_next
    if obj.type != "MESH" or settings.role != "CLOTH" or not settings.enabled:
        raise TethysBakeError("TETHYS works on Cloth objects")
    if not settings.tethys.enabled:
        raise TethysBakeError("Enable TETHYS first")
    state = availability(refresh=True)
    if not state.available:
        raise TethysBakeError(f"TETHYS is unavailable: {state.reason}")
    from ..bake.controller import shared_controller
    if shared_controller.snapshot().active:
        raise TethysBakeError("Wait until the cloth Bake has finished")
    try:
        executable = engine.locate()
        engine.probe(executable)
    except engine.EngineUnavailable as exc:
        raise TethysBakeError(str(exc)) from exc
    _static_transform(obj)
    cloth = cloth_source(obj)
    if not settings.water_flow_container:
        raise TethysBakeError("Prepare the GAIA Fluid Cache first; TETHYS reads its water field")
    from .water_flow import water_container_destination
    try:
        contact = contact_export.inspect_source(water_container_destination(settings.water_flow_container, obj.name))
    except contact_export.ContactSourceError as exc:
        raise TethysBakeError(str(exc)) from exc
    fps = context.scene.render.fps / context.scene.render.fps_base
    if abs(contact.fps - fps) > 1e-6:
        raise TethysBakeError("The GAIA Fluid Cache frame rate differs from the scene. Prepare it again")
    first, last = cloth.first_frame, cloth.first_frame + cloth.frame_count - 1
    if first < contact.frames[0] or last > contact.frames[1]:
        raise TethysBakeError(
            f"The GAIA Fluid Cache covers frames {contact.frames[0]}–{contact.frames[1]} but the cloth "
            f"cache covers {first}–{last}. Prepare the Fluid Cache for the cloth's frame range")
    triangles, polygons, uv = mesh_topology(obj)
    owners = zone_owners(obj)
    try:
        rows, material_of = resolved_materials(obj, owners, polygons.tolist())
        engine_values = engine_settings(context, obj)
    except profiles.WetnessError as exc:
        raise TethysBakeError(str(exc)) from exc
    topology = topology_signature(len(obj.data.vertices), (tuple(p.vertices) for p in obj.data.polygons))
    record = settings_record(obj, context)
    identity = {
        "object": object_uuid(obj),
        "fingerprints": tcache.fingerprints(
            settings=record, sources={"pc2": cloth.pc2_sha256, "water": contact.fingerprint,
                                      "frames": [first, last], "contact": contact.recipe,
                                      "surfaces": contact.surface_stamps(first, last)},
            materials={"rows": rows, "engine": engine_values.values()}, topology=topology),
        "frames": [first, last], "protocol": protocol.PROTOCOL_VERSION,
    }
    final, staging = final_paths(obj), staging_paths(obj)
    final.root.mkdir(parents=True, exist_ok=True)
    identity_json = tcache.canonical_json(identity)
    request = protocol.EngineRequest(
        settings=engine_values, first_frame=first, last_frame=last, pc2_first_sample=0,
        vertex_count=cloth.vertex_count, triangles=triangles, polygon_of=polygons,
        polygon_count=len(obj.data.polygons), material_of=np.asarray(material_of),
        materials=rows, matrix_world=np.array(obj.matrix_world, dtype=float), pc2_path=str(cloth.pc2_path),
        env_path=str(staging.environment), output_path=str(staging.cache), cancel_path=str(staging.cancel),
        identity=identity_json, uv=uv, detail_level=int(settings.tethys.surface_detail),
        texture_size=int(settings.tethys.texture_resolution) if uv is not None else 0,
        feature_scale=settings.tethys.feature_size * 1e-3, seed=_seed(obj))
    partial = {"object": identity["object"], "object_name": obj.name, "identity_json": identity_json,
               "fingerprints": identity["fingerprints"], "settings_fingerprint": tcache.fingerprints(
                   settings=record)["combined"], "frames": [first, last],
               "sources": {"pc2_file": cloth.pc2_path.name, "water_file": contact.path.name}}
    size = (request.texture_size, request.texture_size) if request.texture_size else image_size(len(obj.data.polygons))
    return BakePlan(obj.name, request, contact, cloth, final, staging, partial, executable,
                    len(obj.data.polygons), size)


def _seed(obj) -> int:
    import zlib
    return zlib.crc32(object_uuid(obj).encode()) or 1


def settings_fingerprint(obj, context=None) -> str:
    return tcache.fingerprints(settings=settings_record(obj, context))["combined"]


# ------------------------------------------------------------------- job

class BakeJob:
    """Background preparation + engine process; the modal operator polls it."""

    def __init__(self, plan: BakePlan, threads: int | None = None):
        self.plan = plan
        self.threads = threads
        self.phase = "Preparing water contact"
        self.progress = 0.0
        self.error: str | None = None
        self.code = ""
        self.result: dict | None = None
        self.cancelled = False
        self.engine: engine.EngineJob | None = None
        self.wet_frames = 0
        self.resuming = False
        self.stopped_at = None   # frame an engine cancel stopped before (bake can continue there)
        self.gaia_entitlement = ""
        # Panel progress: the stage the artist sees and its own done/total.
        self.stage = "CONTACT"
        self.done_steps = 0
        self.total_steps = max(1, plan.request.last_frame - plan.request.first_frame + 1)
        self.stage_started = time.monotonic()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="TETHYS bake", daemon=True)
        self._thread.start()

    def _check_cancel(self):
        if self._cancel.is_set():
            raise _Cancelled()

    def _contact_step(self):
        self._check_cancel()
        self.done_steps = min(self.total_steps, self.done_steps + 1)
        self.progress = self.done_steps / self.total_steps

    def enter_stage(self, stage: str, phase: str, total: int) -> None:
        self.stage, self.phase = stage, phase
        self.done_steps, self.total_steps = 0, max(1, total)
        self.progress = 0.0
        self.stage_started = time.monotonic()

    def overall(self) -> float:
        """0..1 over the whole bake: contact 10 %, engine 80 %, preview images 10 %."""
        start, width = _STAGE_SPAN.get(self.stage, (0.0, 1.0))
        return min(1.0, start + width * min(1.0, self.progress))

    def remaining_seconds(self) -> float | None:
        """Rough time left in the current stage, once a few steps have run."""
        if self.done_steps < 2 or self.progress >= 1.0:
            return None
        elapsed = time.monotonic() - self.stage_started
        return elapsed / self.done_steps * (self.total_steps - self.done_steps)

    def _run(self):
        plan = self.plan
        try:
            encoded = protocol.encode_request(plan.request)
            if can_resume(plan.staging, encoded):
                # An interrupted bake of exactly this request: the engine
                # continues after its last saved frame, water stream unchanged.
                plan.staging.cancel.unlink(missing_ok=True)
                self.resuming = True
            else:
                tcache.remove(plan.staging, stage="TETHYS_STAGING")
                plan.staging.root.mkdir(parents=True, exist_ok=True)
                self.wet_frames = contact_export.export_contact(
                    plan.staging.environment, plan.contact, plan.cloth.pc2_path, plan.cloth.vertex_count,
                    plan.request.matrix_world, plan.request.first_frame, plan.request.last_frame, 0,
                    check_cancel=self._contact_step)
                protocol.write_atomic_bytes(plan.staging.request, encoded)
            self._check_cancel()
            total = max(1, plan.request.last_frame - plan.request.first_frame)
            self.enter_stage("ENGINE", "Simulating wetness", total)
            self.engine = engine.EngineJob(plan.executable, plan.staging.request, plan.staging.cancel,
                                           threads=self.threads)
            while True:
                for event in self.engine.take_events():
                    kind = event.get("event")
                    if kind == "start":
                        # Native GAIA entitlement verdict (reported; not enforced yet).
                        self.gaia_entitlement = str(event.get("gaia_entitlement", ""))
                    elif kind == "progress":
                        self.done_steps = int(event.get("done", 0))
                        self.progress = self.done_steps / total
                    elif kind == "complete":
                        self.result = event
                    elif kind == "error":
                        self.code = str(event.get("code", "TETHYS-E999"))
                        self.error = str(event.get("message", "The TETHYS Engine failed"))
                    elif kind == "cancelled":
                        self.cancelled = True
                        self.stopped_at = event.get("frame")
                code = self.engine.poll()
                if code is not None:
                    break
                if self._cancel.is_set():
                    self.engine.cancel()
                    self.cancelled = True
                    break
                time.sleep(0.05)
            self.engine.close()
            for event in self.engine.take_events():
                if event.get("event") == "cancelled":
                    self.stopped_at = event.get("frame")
                elif event.get("event") == "complete":
                    self.result = event
                elif event.get("event") == "error":
                    self.code = str(event.get("code", "TETHYS-E999"))
                    self.error = str(event.get("message", "The TETHYS Engine failed"))
            if self.result is None and not self.cancelled and self.error is None:
                tail = " ".join(self.engine.stderr_tail[-3:])
                self.error = f"The TETHYS Engine stopped unexpectedly (exit code {code}). {tail}".strip()
                self.code = "TETHYS-E998"
        except _Cancelled:
            self.cancelled = True
        except (contact_export.ContactSourceError, protocol.ProtocolError, engine.EngineUnavailable) as exc:
            self.error = str(exc)
        except OSError as exc:
            self.error = f"TETHYS could not write its files: {exc.strerror or exc}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the artist, never swallowed
            self.error = f"TETHYS failed unexpectedly: {exc}"
        finally:
            self.phase = "Finished"

    def cancel(self):
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()


class _Cancelled(Exception):
    pass


# Engines that run at once in Bake All. The water solver is limited by memory
# bandwidth, so more would only share it; each extra one also holds its mesh
# and texture in memory.
MAX_PARALLEL = 3


def plan_weight(plan: BakePlan) -> float:
    """Water cells of a bake, the measure of its engine work."""
    return float(len(plan.request.triangles)) * plan.request.detail_level ** 2


class BakeGroup:
    """Several wetness bakes at once, one engine per object; looks like one job.

    The largest objects start first, at most ``MAX_PARALLEL`` at a time; the
    next starts when one finishes. Every engine takes its usual cores and the
    system shares them: a small object is done quickly and leaves them to the
    large one, which decides how long the whole group takes.
    """

    def __init__(self, plans: list[BakePlan], threads: int | None = None, parallel: int = MAX_PARALLEL):
        self.threads, self.parallel = threads, max(1, parallel)
        self.pending = sorted(plans, key=plan_weight, reverse=True)
        self.count = len(plans)
        self.jobs: list[BakeJob] = []
        self.published: list[BakeJob] = []   # finished jobs already handed to the publisher
        self._cancelled = False
        from types import SimpleNamespace
        # The Bake window reads the frame range from here.
        self.plan = SimpleNamespace(request=SimpleNamespace(
            first_frame=min(p.request.first_frame for p in plans), last_frame=max(p.request.last_frame for p in plans)))
        self.started = time.monotonic()
        self.fill()

    def fill(self) -> None:
        """Start waiting objects while fewer than ``parallel`` engines run."""
        while self.pending and not self._cancelled and sum(not job.done for job in self.jobs) < self.parallel:
            self.jobs.append(BakeJob(self.pending.pop(0), self.threads))

    @property
    def stage(self) -> str:
        stages = [job.stage for job in self.jobs if not job.done]
        if not stages:
            return self.jobs[-1].stage
        return "CONTACT" if "CONTACT" in stages else stages[0]

    @property
    def phase(self) -> str:
        finished = sum(job.done for job in self.jobs)
        base = "Preparing water contact" if self.stage == "CONTACT" else "Simulating wetness"
        return f"{base} · {finished} of {self.count} objects done"

    @property
    def done_steps(self) -> int:
        return sum(job.done_steps if not job.done else job.total_steps for job in self.jobs)

    @property
    def total_steps(self) -> int:
        return sum(job.total_steps for job in self.jobs) + sum(
            p.request.last_frame - p.request.first_frame + 1 for p in self.pending)

    @property
    def progress(self) -> float:
        return self.overall()

    def overall(self) -> float:
        return sum(1.0 if job.done else job.overall() for job in self.jobs) / self.count

    def remaining_seconds(self) -> float | None:
        if self.pending:
            return None
        values = [job.remaining_seconds() for job in self.jobs if not job.done]
        if not values or any(v is None for v in values):
            return None
        return max(values)

    def cancel(self) -> None:
        self._cancelled = True
        for job in self.jobs:
            job.cancel()

    @property
    def cancel_requested(self) -> bool:
        return self._cancelled or any(job.cancel_requested for job in self.jobs)

    @property
    def done(self) -> bool:
        self.fill()
        return not self.pending and all(job.done for job in self.jobs) or (
            self._cancelled and all(job.done for job in self.jobs))

    @property
    def cancelled(self) -> bool:
        return self._cancelled or any(job.cancelled for job in self.jobs)

    @property
    def errors(self) -> list[str]:
        return [f"{job.plan.object_name}: {job.code} {job.error}".replace("  ", " ")
                for job in self.jobs if job.error]


_STAGE_SPAN = {"CONTACT": (0.0, 0.1), "ENGINE": (0.1, 0.8), "IMAGES": (0.9, 0.1)}


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def progress_display() -> tuple[float, str] | None:
    """(overall fraction, label) of the running wetness bake, or None when idle."""
    job = _job
    if job is None:
        return None
    if job.cancel_requested:
        return job.overall(), "Cancelling…"
    text = f"{job.phase} {min(job.done_steps, job.total_steps)}/{job.total_steps}"
    left = job.remaining_seconds()
    if left is not None:
        text += f" · about {_duration(left)} left"
    return job.overall(), text


# ---------------------------------------------------------------- publish

def write_images(plan: BakePlan, frames, *, image_name="__CNX_TETHYS_EXPORT"):
    """Convert cache frames to EXR images. UV layout: R water height mm, G absorbed
    mm, plus a second sequence with the tangent-space water normal as a
    normal-map colour (alpha is not lossless in Blender's EXR path, so it stays
    1). Per face: R surface mm, G absorbed mm, B contact."""
    width, height = plan.image_size
    textured = bool(plan.request.texture_size)
    # Each polygon owns one wetness material: capacity in mm per polygon.
    capacity_mm = np.zeros(plan.polygon_count, np.float32)
    rows = np.asarray(plan.request.materials, float)
    capacity_mm[np.asarray(plan.request.polygon_of)] = rows[np.asarray(plan.request.material_of), 1] * 1000.0
    pixels = np.zeros((height * width, 4), np.float32)
    pixels[:, 3] = 1.0
    normals = np.ones((height * width, 4), np.float32)
    image = bpy.data.images.new(image_name, width, height, alpha=True, float_buffer=True)
    normal_image = bpy.data.images.new(image_name + " N", width, height, alpha=True, float_buffer=True) \
        if textured else None
    # Image.save() is lossy for EXR in Blender 5.2; save_render with an owned
    # scene writes lossless 16-bit ZIP without any view transform.
    exporter = bpy.data.scenes.new("__CNX_TETHYS_EXPORT")
    settings = exporter.render.image_settings
    settings.file_format = "OPEN_EXR"
    settings.color_depth = "16"
    settings.exr_codec = "ZIP"
    settings.color_mode = "RGBA"
    settings.color_management = "OVERRIDE"
    settings.view_settings.view_transform = "Standard"
    settings.view_settings.look = "None"
    settings.view_settings.exposure = 0.0
    settings.view_settings.gamma = 1.0
    try:
        for target in (image, normal_image):
            if target is not None:
                target.colorspace_settings.name = "Non-Color"
                target.file_format = "OPEN_EXR"
        with protocol.CacheReader(plan.staging.cache) as reader:
            for frame in frames:
                data = reader.read(frame)
                if textured:
                    texture = data.channels["texture"]
                    parts = texture.shape[-1]
                    flat = texture.reshape(-1, parts)
                    pixels[:, 0] = flat[:, 0]
                    pixels[:, 1] = flat[:, 1]
                    if parts == 4:
                        nx, ny = flat[:, 2], flat[:, 3]
                        nz = np.sqrt(np.maximum(1.0 - nx * nx - ny * ny, 0.0))
                        normals[:, 0], normals[:, 1], normals[:, 2] = 0.5 * nx + 0.5, 0.5 * ny + 0.5, 0.5 * nz + 0.5
                    else:
                        normals[:, :3] = (0.5, 0.5, 1.0)
                    normal_image.pixels.foreach_set(normals.reshape(-1))
                    normal_image.save_render(str(plan.staging.normal_image(frame)), scene=exporter)
                else:
                    count = plan.polygon_count
                    pixels[:count, 0] = data.channels["surface"]
                    pixels[:count, 1] = data.channels["saturation"] * capacity_mm
                    pixels[:count, 2] = data.channels.get("contact", 0.0)
                image.pixels.foreach_set(pixels.reshape(-1))
                path = plan.staging.frame_image(frame)
                image.save_render(str(path), scene=exporter)
    finally:
        bpy.data.images.remove(image)
        if normal_image is not None:
            bpy.data.images.remove(normal_image)
        bpy.data.scenes.remove(exporter)


def publish(plan: BakePlan) -> dict:
    """Move the staged bake into place and seal its sidecar (main thread)."""
    final, staging = plan.final, plan.staging
    partial = tcache.write_partial(staging, plan.record)
    images = {"directory": final.frames.name, "width": plan.image_size[0], "height": plan.image_size[1],
              "format": "OPEN_EXR", "layout": "UV" if plan.request.texture_size else "FACE",
              "channels": ["water_height_mm", "absorbed_mm"] if plan.request.texture_size
              else ["surface_mm", "absorbed_mm", "contact"],
              "normal_map": bool(plan.request.texture_size)}
    staged = tcache.write_complete(staging, partial, images=images, result=_ledger(plan))
    # Replace only this object's owned files; the cloth cache is untouched.
    tcache.remove(final, stage="TETHYS_REPLACE")
    os.replace(staging.cache, final.cache)
    final.frames.mkdir(parents=True, exist_ok=True)
    for image in sorted(staging.frames.glob(f"{staging.stem}_*.exr")):
        frame = int(image.stem.rsplit("_", 1)[1])
        normal = image.stem.startswith(f"{staging.stem}_normal_")
        os.replace(image, final.normal_image(frame) if normal else final.frame_image(frame))
    shutil.rmtree(staging.frames, ignore_errors=True)
    sealed = {k: v for k, v in staged.items() if k not in {"metadata_digest", "cache_file"}}
    sealed["cache_file"] = final.cache.name
    sealed["metadata_digest"] = tcache._digest(sealed)
    cache_metadata.write_atomic(final.sidecar, sealed)
    tcache.remove(staging, stage="TETHYS_STAGING")
    return sealed


def _ledger(plan: BakePlan) -> dict:
    with protocol.CacheReader(plan.staging.cache) as reader:
        last = reader.read(reader.last_frame).ledger
    return {k: last[k] for k in protocol.LEDGER_FIELDS if k != "reserved"}


def finalize(plan: BakePlan, obj, job: BakeJob) -> str:
    """Publish a finished engine run, connect materials and record it on the object."""
    plan.record["gaia_entitlement"] = job.gaia_entitlement
    sealed = publish(plan)
    from . import tethys_overlay, tethys_shader
    tethys_shader.detach(obj, keep_attribute=True)   # the layout (UV or per face) may have changed
    notes = tethys_shader.attach(obj, plan.final, plan.image_size, plan.request.first_frame,
                                 plan.request.last_frame, uv_layout=bool(plan.request.texture_size))
    s = obj.cloth_next.tethys
    s.cache_stem, s.cache_root = plan.final.stem, str(plan.final.root)
    s.baked_fingerprint = plan.record["settings_fingerprint"]
    s.image_width, s.image_height = plan.image_size
    s.baked_first, s.baked_last = plan.request.first_frame, plan.request.last_frame
    tethys_overlay.invalidate()
    result = sealed.get("result", {})
    seconds = (job.result or {}).get("seconds", 0.0)
    water = result.get("surface", 0.0) + result.get("absorbed", 0.0)
    message = (f"Wetness baked: {plan.request.last_frame - plan.request.first_frame + 1} frames in "
               f"{seconds:.2f} s, {water * 1e6:.1f} ml on the cloth")
    if notes:
        message += " · Materials not connected: " + "; ".join(notes)
    return message


def can_resume(staging: tcache.CachePaths, encoded_request: bytes) -> bool:
    """True when an interrupted bake of this exact request can continue."""
    try:
        return (staging.checkpoint.is_file() and staging.partial.is_file() and staging.environment.is_file()
                and staging.request.is_file() and staging.request.read_bytes() == encoded_request)
    except OSError:
        return False


def _stop(plan: BakePlan, job: "BakeJob") -> str:
    """After a cancel: keep an engine checkpoint for the next bake, drop anything else."""
    if plan.staging.checkpoint.is_file() and plan.staging.partial.is_file():
        plan.staging.cancel.unlink(missing_ok=True)
        where = f" before frame {job.stopped_at}" if job.stopped_at is not None else ""
        return f"Wetness bake paused{where}. Bake again to continue"
    tcache.remove(plan.staging, stage="TETHYS_ABORT")
    return "Wetness bake cancelled"


def bake_blocking(context, obj, *, cancel_after: float | None = None,
                  on_tick=None) -> tuple[str, BakeJob]:
    """Run a whole wetness bake without the modal operator (scripts and tests)."""
    global _job
    plan = prepare(context, obj)
    job = BakeJob(plan)
    _job = job
    try:
        started = time.monotonic()
        while not job.done:
            if cancel_after is not None and time.monotonic() - started >= cancel_after:
                job.cancel()
            if on_tick is not None:
                on_tick(job)
            time.sleep(0.02)
        if job.cancelled and not job.error:
            return _stop(plan, job), job
        if job.error:
            tcache.remove(plan.staging, stage="TETHYS_ABORT")
            return f"{job.code} {job.error}".strip(), job
        plan.staging.frames.mkdir(parents=True, exist_ok=True)
        write_images(plan, range(plan.request.first_frame, plan.request.last_frame + 1))
        message = finalize(plan, obj, job)
        obj.cloth_next.tethys.status = message
        return message, job
    except Exception:
        tcache.remove(plan.staging, stage="TETHYS_ABORT")
        raise
    finally:
        _job = None


# --------------------------------------------------------------- operators

class _WindowSession:
    """Mirrors one wetness bake into the shared Bake state and the Bake window.

    The window shows phase, frames, time left and Cancel exactly like a cloth
    bake; its Cancel arrives through ``cancel_from_window``.
    """

    PUBLISH_SECONDS = 0.25

    def __init__(self, context, obj, plan: BakePlan):
        from ..bake.controller import shared_controller
        from ..bake.status import BakeJobKind, BakeState
        from . import modal_lock
        self.controller = shared_controller
        owner = shared_controller.snapshot()
        if owner.active:
            raise TethysBakeError("A Cloth NeXt Bake is already running")
        if owner.state is not BakeState.IDLE:
            shared_controller.reset()
        first, last = plan.request.first_frame, plan.request.last_frame
        self.job_id = shared_controller.transition(
            BakeState.PREPARING, job_kind=BakeJobKind.WETNESS, status_title=TITLE,
            status_message="Preparing wetness bake", active_object_name=obj.name,
            frame_start=first, frame_end=last).job_id
        if not modal_lock.reserve(self.job_id):
            shared_controller.fail("Another Cloth NeXt Bake owns the window")
            raise TethysBakeError("Another Cloth NeXt Bake owns the window")
        self.started = time.monotonic()
        self.waiting = _bake_window_enabled(context)
        self.deadline = 0.0
        self._sent = 0.0
        self._published_stage = ""
        if self.waiting:
            from ..bake.transport import EnterBakeMode
            from . import companion_manager
            shared_controller.transition(BakeState.STARTING_COMPANION, status_title=TITLE)
            ok, message = companion_manager.begin_bake_mode(EnterBakeMode(
                job_id=self.job_id, blender_process_id=os.getpid(), frame_start=first,
                frame_end=last, preset_label=TITLE))
            if not ok:
                self.end_error(message)
                raise TethysBakeError(message)
            shared_controller.transition(BakeState.WAITING_FOR_COMPANION, status_title=TITLE,
                                         status_message="Opening the Bake window")
            self.deadline = time.monotonic() + companion_manager.STARTUP_TIMEOUT_SECONDS
        else:
            shared_controller.transition(BakeState.STARTING_RUN, status_title=TITLE)

    def owns(self) -> bool:
        return self.controller.snapshot().job_id == self.job_id

    def cancel_requested(self) -> bool:
        from ..bake.status import BakeState
        snapshot = self.controller.snapshot()
        return snapshot.job_id != self.job_id or snapshot.state in {BakeState.CANCELLING,
                                                                    BakeState.CANCELLED}

    def window_ready(self) -> bool:
        """True once the window answered; raises TethysBakeError on a failed start."""
        from ..bake.status import BakeState
        from . import companion_manager
        if not self.waiting:
            return True
        status, message = companion_manager.startup_status(self.job_id)
        if status != "READY":
            if status in {"ERROR", "CANCELLED"} or time.monotonic() >= self.deadline:
                raise TethysBakeError(message or "The Bake window did not open")
            return False
        if not companion_manager.consume_ready(self.job_id):
            return False
        self.controller.transition(BakeState.COMPANION_READY, status_title=TITLE)
        self.controller.transition(BakeState.STARTING_RUN, status_title=TITLE)
        self.waiting = False
        return True

    def publish(self, job: BakeJob, *, force=False) -> None:
        from ..bake.status import BakeState
        if self.cancel_requested():
            return
        state = {"CONTACT": BakeState.EXPORTING, "ENGINE": BakeState.SIMULATING,
                 "IMAGES": BakeState.IMPORTING}[job.stage]
        now = time.monotonic()
        if job.stage == self._published_stage and not force and now - self._sent < self.PUBLISH_SECONDS:
            return
        if job.stage != self._published_stage:
            if state is BakeState.SIMULATING and self.controller.snapshot().state is BakeState.EXPORTING:
                self.controller.transition(BakeState.STARTING_SOLVER, status_title=TITLE)
            self._published_stage = job.stage
        first = job.plan.request.first_frame
        frame = first + job.done_steps if job.stage != "CONTACT" else None
        left = job.remaining_seconds()
        label = f"{job.phase} · {min(job.done_steps, job.total_steps)} / {job.total_steps}"
        if left is not None:
            label += f" · {_duration(left)} left"
        self.controller.transition(
            state, status_title=TITLE, status_message=job.phase, activity_label=label,
            progress_current=int(round(job.overall() * 1000)), progress_total=1000,
            current_frame=frame, estimated_remaining_seconds=left,
            elapsed_seconds=now - self.started)
        self._sent = now

    def end_finished(self, message: str) -> None:
        from ..bake.status import BakeState
        if not self.cancel_requested():
            self.controller.transition(
                BakeState.FINISHED, status_title="Wetness baked", status_message=message,
                activity_label="Wetness baked", progress_current=1000, progress_total=1000,
                estimated_remaining_seconds=None, elapsed_seconds=time.monotonic() - self.started)
        self._release()

    def end_cancelled(self) -> None:
        from ..bake.status import BakeState
        from . import companion_manager
        if self.owns():
            companion_manager.cancel_startup(self.job_id, "Wetness bake cancelled")
            snapshot = self.controller.snapshot()
            if snapshot.state is not BakeState.CANCELLED:
                if snapshot.state is not BakeState.CANCELLING:
                    self.controller.request_cancel()
                self.controller.transition(BakeState.CANCELLED, status_title="Wetness bake cancelled",
                                           status_message="The previous wetness result stays",
                                           activity_label="Wetness cancelled")
        self._release()

    def end_error(self, message: str) -> None:
        from ..bake.status import BakeState
        from . import companion_manager
        if self.owns() and self.controller.snapshot().state is not BakeState.ERROR:
            self.controller.fail(message, "Stage: TETHYS wetness bake\n"
                                 "What to do: Check the message, adjust the wetness settings, then bake again.")
            companion_manager.persist_bake_error(self.controller.snapshot())
        self._release()

    def _release(self) -> None:
        global _window_session
        from . import modal_lock
        modal_lock.release(self.job_id)
        if _window_session is self:
            _window_session = None


TITLE = "TETHYS Wetness"
_window_session: _WindowSession | None = None


def _bake_window_enabled(context) -> bool:
    from .addon_identity import addon_preferences
    try:
        return bool(addon_preferences(context, __package__).auto_launch_bake_window)
    except (KeyError, AttributeError):
        return True


def cancel_from_window() -> bool:
    """Cancel pressed in the Bake window; True when it was meant for TETHYS."""
    session = _window_session
    if session is None or not session.owns():
        return False
    if _job is not None:
        _job.cancel()
    from ..bake.status import BakeState
    if session.controller.snapshot().state not in {BakeState.CANCELLING, BakeState.CANCELLED}:
        session.controller.request_cancel()
    return True


class CLOTHNEXT_OT_tethys_bake(bpy.types.Operator):
    bl_idname = "clothnext.tethys_bake"
    bl_label = "Bake Wetness"
    bl_description = ("Simulate surface water and absorbed moisture on the baked cloth with the "
                      "TETHYS Engine. The cloth cache is only read")
    bl_options = {"REGISTER"}

    _timer = None
    _job: BakeJob | None = None
    _session: _WindowSession | None = None
    _frames: list
    _stage = ""

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and getattr(obj, "cloth_next", None) and obj.cloth_next.role == "CLOTH"
                    and obj.cloth_next.tethys.enabled and _job is None and _window_session is None)

    def invoke(self, context, _event):
        return self.execute(context)

    def execute(self, context):
        global _window_session
        obj = context.object
        try:
            self._plan = prepare(context, obj)
            self._session = _window_session = _WindowSession(context, obj, self._plan)
        except TethysBakeError as exc:
            obj.cloth_next.tethys.status = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._object_name = obj.name
        self._stage = "WINDOW"
        global _last_tick
        _last_tick = time.monotonic()
        obj.cloth_next.tethys.status = "Baking wetness…"
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _finish(self, context, message, level="INFO"):
        global _job
        _job = None
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if level == "INFO":
            self._session.end_finished(message)
        elif level == "WARNING":
            self._session.end_cancelled()
        else:
            self._session.end_error(message)
        obj = bpy.data.objects.get(self._object_name)
        if obj is not None:
            obj.cloth_next.tethys.status = message
        context.workspace.status_text_set(None)
        tag_redraw()
        self.report({level}, message)
        return {"FINISHED"} if level == "INFO" else {"CANCELLED"}

    def _abort(self, context, message, level):
        try:
            tcache.remove(self._plan.staging, stage="TETHYS_ABORT")
        except OSError:
            pass
        return self._finish(context, message, level)

    def modal(self, context, event):
        global _job, _last_tick
        _last_tick = time.monotonic()
        job = self._job
        cancel = ((event.type == "ESC" and event.value == "PRESS")
                  or bpy.data.objects.get(self._object_name) is None
                  or self._session.cancel_requested())
        if cancel and job is not None:
            job.cancel()
        if event.type != "TIMER" and not cancel:
            return {"PASS_THROUGH"}
        obj = bpy.data.objects.get(self._object_name)
        tag_redraw()
        if self._stage == "WINDOW":
            if cancel:
                return self._finish(context, "Wetness bake cancelled", "WARNING")
            try:
                if not self._session.window_ready():
                    return {"RUNNING_MODAL"}
            except TethysBakeError as exc:
                return self._finish(context, str(exc), "ERROR")
            self._job = _job = BakeJob(self._plan)
            self._stage = "ENGINE"
            return {"RUNNING_MODAL"}
        if self._stage == "ENGINE":
            context.workspace.status_text_set(
                f"TETHYS · {job.phase} · {job.overall() * 100:.0f}% · Esc to cancel")
            if not job.done:
                self._session.publish(job)
                return {"RUNNING_MODAL"}
            plan = job.plan
            if job.cancelled and not job.error:
                try:
                    message = _stop(plan, job)
                except OSError:
                    message = "Wetness bake cancelled"
                return self._finish(context, message, "WARNING")
            if job.error:
                return self._abort(context, f"{job.code} {job.error}".strip(), "ERROR")
            self._frames = list(range(plan.request.first_frame, plan.request.last_frame + 1))
            self._stage = "IMAGES"
            job.enter_stage("IMAGES", "Writing wetness frames", len(self._frames))
            self._session.publish(job, force=True)
            plan.staging.frames.mkdir(parents=True, exist_ok=True)
            return {"RUNNING_MODAL"}
        if self._stage == "IMAGES":
            plan = job.plan
            if job.cancel_requested:
                return self._abort(context, "Wetness bake cancelled", "WARNING")
            try:
                batch, self._frames = self._frames[:8], self._frames[8:]
                write_images(plan, batch)
                job.done_steps = job.total_steps - len(self._frames)
                job.progress = job.done_steps / job.total_steps
                self._session.publish(job)
                context.workspace.status_text_set(
                    f"TETHYS · Writing wetness frames {job.done_steps}/{job.total_steps} · Esc to cancel")
                if self._frames:
                    return {"RUNNING_MODAL"}
                return self._finish(context, finalize(plan, obj, job))
            except Exception as exc:  # noqa: BLE001 - keep the previous result, report clearly
                return self._abort(context, f"Wetness bake could not be published: {exc}", "ERROR")
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._job is not None:
            self._job.cancel()
        global _job
        _job = None
        if self._session is not None:
            self._session.end_cancelled()
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


class CLOTHNEXT_OT_tethys_clear(bpy.types.Operator):
    bl_idname = "clothnext.tethys_clear"
    bl_label = "Clear Wetness Cache"
    bl_description = "Remove this object's TETHYS wetness cache and its material connection. The cloth cache stays"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and getattr(obj, "cloth_next", None) and _job is None)

    def execute(self, context):
        obj = context.object
        from . import tethys_overlay, tethys_shader
        tethys_shader.detach(obj)
        try:
            tcache.remove(final_paths(obj))
            tcache.remove(staging_paths(obj), stage="TETHYS_STAGING")
        except OSError as exc:
            self.report({"ERROR"}, f"Wetness cache files are in use: {exc}. Close other programs and retry")
            return {"CANCELLED"}
        s = obj.cloth_next.tethys
        s.cache_stem = s.baked_fingerprint = ""
        s.status = "Wetness cache cleared"
        tethys_overlay.invalidate()
        return {"FINISHED"}


def cache_status(obj) -> tuple[str, str]:
    """(state, message) for the panel; reads only the small sidecar."""
    s = obj.cloth_next.tethys
    if not s.cache_stem:
        return "NONE", "Not baked"
    paths = tcache.CachePaths(Path(s.cache_root), s.cache_stem)
    record = tcache.load_sidecar(paths)
    if record is None:
        return "MISSING", "Wetness cache is missing · Bake Wetness"
    if record.get("completion_state") != "complete":
        return "PARTIAL", "Wetness bake did not complete · Bake Wetness"
    if record.get("settings_fingerprint") != settings_fingerprint(obj):
        return "STALE", "Wetness settings changed · Bake Wetness"
    return "READY", f"Wetness frames {s.baked_first}–{s.baked_last}"


def _span(first: int, last: int):
    """Frame range of a group, shaped like a plan for the Bake window."""
    from types import SimpleNamespace
    return SimpleNamespace(request=SimpleNamespace(first_frame=first, last_frame=last))


def bake_all_targets(context) -> list:
    """Cloth objects of the scene with TETHYS switched on, in name order."""
    return sorted((obj for obj in context.scene.objects
                   if obj.type == "MESH" and getattr(obj, "cloth_next", None) is not None
                   and obj.cloth_next.role == "CLOTH"
                   and obj.cloth_next.tethys.enabled), key=lambda obj: obj.name)


def prepare_all(context) -> tuple[list[BakePlan], list[str]]:
    """Plans for every TETHYS object that can bake, and why the others cannot."""
    plans, skipped = [], []
    for obj in bake_all_targets(context):
        try:
            plans.append(prepare(context, obj))
        except TethysBakeError as exc:
            skipped.append(f"{obj.name}: {exc}")
    return plans, skipped


def _summary(baked: list[str], stopped: list[str], failed: list[str], skipped: list[str]) -> str:
    parts = []
    if baked:
        parts.append(f"Wetness baked for {len(baked)} object{'s' if len(baked) != 1 else ''}")
    if stopped:
        parts.append(f"{len(stopped)} paused (Bake again to continue)")
    if failed:
        parts.append("failed: " + "; ".join(failed))
    if skipped:
        parts.append("skipped: " + "; ".join(skipped))
    return " · ".join(parts) or "Nothing to bake"


def bake_all_blocking(context, *, cancel_after: float | None = None, threads: int | None = None,
                      on_tick=None) -> tuple[str, BakeGroup | None]:
    """Bake every TETHYS object at once without the modal operator (scripts and tests)."""
    global _job
    plans, skipped = prepare_all(context)
    if not plans:
        return _summary([], [], [], skipped), None
    group = BakeGroup(plans, threads)
    publisher = GroupPublisher(group, skipped)
    _job = group
    try:
        started = time.monotonic()
        while not publisher.step():
            if cancel_after is not None and time.monotonic() - started >= cancel_after:
                group.cancel()
            if on_tick is not None:
                on_tick(group)
            if publisher._current is None:
                time.sleep(0.02)
        return publisher.summary(), group
    finally:
        _job = None


class GroupPublisher:
    """Publishes each object of a group as soon as its engine is done, a few
    frames per call, while the other engines keep running."""

    FRAMES_PER_STEP = 8

    def __init__(self, group: BakeGroup, skipped: list[str]):
        self.group, self.skipped = group, skipped
        self.baked, self.stopped, self.failed = [], [], []
        self._current: BakeJob | None = None
        self._frames: list[int] = []

    def _start(self, job: BakeJob) -> None:
        """Settle a finished job; queue its frames when it has a result to publish."""
        plan = job.plan
        obj = bpy.data.objects.get(plan.object_name)
        message = None
        try:
            if job.error:
                tcache.remove(plan.staging, stage="TETHYS_ABORT")
                self.failed.append(f"{plan.object_name}: {job.code} {job.error}".replace("  ", " "))
                message = f"{job.code} {job.error}".strip()
            elif job.cancelled or job.result is None:
                message = _stop(plan, job)
                self.stopped.append(plan.object_name)
            elif obj is None:
                tcache.remove(plan.staging, stage="TETHYS_ABORT")
                self.failed.append(f"{plan.object_name}: the object was removed")
            else:
                plan.staging.frames.mkdir(parents=True, exist_ok=True)
                self._current = job
                self._frames = list(range(plan.request.first_frame, plan.request.last_frame + 1))
        except Exception as exc:  # noqa: BLE001 - one object never takes the others down
            self._fail(job, exc)
        if message is not None and obj is not None:
            obj.cloth_next.tethys.status = message

    def _fail(self, job: BakeJob, exc: Exception) -> None:
        try:
            tcache.remove(job.plan.staging, stage="TETHYS_ABORT")
        except OSError:
            pass
        self.failed.append(f"{job.plan.object_name}: could not be published: {exc}")
        obj = bpy.data.objects.get(job.plan.object_name)
        if obj is not None:
            obj.cloth_next.tethys.status = f"Wetness bake could not be published: {exc}"
        self._current, self._frames = None, []

    def step(self) -> bool:
        """Do a little publishing work; True once every object is settled."""
        if self._current is None:
            self.group.fill()
            ready = [job for job in self.group.jobs if job.done and job not in self.group.published]
            if not ready:
                if self.group.cancelled and all(job.done for job in self.group.jobs):
                    for plan in self.group.pending:   # never started: nothing to keep
                        self.stopped.append(plan.object_name)
                    self.group.pending = []
                    return len(self.group.published) == len(self.group.jobs)
                return not self.group.pending and len(self.group.published) == len(self.group.jobs)
            job = ready[0]
            self.group.published.append(job)
            self._start(job)
            return False
        job = self._current
        if job.cancel_requested:
            # Cancelled while publishing: the engine result is complete, but
            # nothing half-written is left behind.
            self._fail(job, RuntimeError("cancelled"))
            self.failed.pop()
            self.stopped.append(job.plan.object_name)
            return False
        try:
            batch, self._frames = self._frames[:self.FRAMES_PER_STEP], self._frames[self.FRAMES_PER_STEP:]
            write_images(job.plan, batch)
            if not self._frames:
                obj = bpy.data.objects[job.plan.object_name]
                obj.cloth_next.tethys.status = finalize(job.plan, obj, job)
                self.baked.append(job.plan.object_name)
                self._current = None
        except Exception as exc:  # noqa: BLE001
            self._fail(job, exc)
        return False

    def summary(self) -> str:
        return _summary(self.baked, self.stopped, self.failed, self.skipped)


class CLOTHNEXT_OT_tethys_bake_all(bpy.types.Operator):
    bl_idname = "clothnext.tethys_bake_all"
    bl_label = "Bake All Wetness"
    bl_description = ("Bake the wetness of every cloth with TETHYS switched on, all at the same time "
                      "(the CPU cores are shared). Cloth caches are only read")
    bl_options = {"REGISTER"}

    _timer = None
    _group: BakeGroup | None = None
    _session: _WindowSession | None = None
    _stage = ""

    @classmethod
    def poll(cls, context):
        return (_job is None and _window_session is None and getattr(context, "scene", None) is not None
                and len(bake_all_targets(context)) > 0)

    def invoke(self, context, _event):
        return self.execute(context)

    def execute(self, context):
        global _window_session, _last_tick
        self._plans, self._skipped = prepare_all(context)
        if not self._plans:
            message = _summary([], [], [], self._skipped)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        first = min(plan.request.first_frame for plan in self._plans)
        last = max(plan.request.last_frame for plan in self._plans)
        lead = bpy.data.objects[self._plans[0].object_name]
        try:
            # The Bake window shows the whole group as one wetness bake.
            self._session = _window_session = _WindowSession(context, lead, _span(first, last))
        except TethysBakeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for plan in self._plans:
            bpy.data.objects[plan.object_name].cloth_next.tethys.status = "Baking wetness…"
        self._stage = "WINDOW"
        _last_tick = time.monotonic()
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _finish(self, context, message, level="INFO"):
        global _job
        _job = None
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if level == "INFO":
            self._session.end_finished(message)
        elif level == "WARNING":
            self._session.end_cancelled()
        else:
            self._session.end_error(message)
        context.workspace.status_text_set(None)
        tag_redraw()
        self.report({level}, message)
        return {"FINISHED"} if level == "INFO" else {"CANCELLED"}

    def modal(self, context, event):
        global _job, _last_tick
        _last_tick = time.monotonic()
        group = self._group
        cancel = (event.type == "ESC" and event.value == "PRESS") or self._session.cancel_requested()
        if cancel and group is not None:
            group.cancel()
        if event.type != "TIMER" and not cancel:
            return {"PASS_THROUGH"}
        tag_redraw()
        if self._stage == "WINDOW":
            if cancel:
                return self._finish(context, "Wetness bake cancelled", "WARNING")
            try:
                if not self._session.window_ready():
                    return {"RUNNING_MODAL"}
            except TethysBakeError as exc:
                return self._finish(context, str(exc), "ERROR")
            self._group = _job = BakeGroup(self._plans)
            self._publisher = GroupPublisher(self._group, self._skipped)
            self._stage = "ENGINE"
            return {"RUNNING_MODAL"}
        context.workspace.status_text_set(
            f"TETHYS · {group.phase} · {group.overall() * 100:.0f}% · Esc to cancel")
        if not group.done:
            self._session.publish(group)
        if not self._publisher.step():
            return {"RUNNING_MODAL"}
        publisher = self._publisher
        message = publisher.summary()
        if publisher.stopped:
            return self._finish(context, message, "WARNING")
        if publisher.failed and not publisher.baked:
            return self._finish(context, message, "ERROR")
        return self._finish(context, message, "INFO")

    def cancel(self, context):
        global _job
        if self._group is not None:
            self._group.cancel()
        _job = None
        if self._session is not None:
            self._session.end_cancelled()
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


class CLOTHNEXT_OT_tethys_cancel(bpy.types.Operator):
    bl_idname = "clothnext.tethys_cancel"
    bl_label = "Cancel Wetness Bake"
    bl_description = "Stop the running wetness bake. The previous wetness result stays"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return _job is not None and not _job.cancel_requested

    def execute(self, _context):
        if _job is not None:
            _job.cancel()
        tag_redraw()
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_tethys_bake, CLOTHNEXT_OT_tethys_bake_all, CLOTHNEXT_OT_tethys_clear,
           CLOTHNEXT_OT_tethys_cancel)


def shutdown() -> None:
    """Add-on unregister: stop a running engine and release files."""
    global _job
    if _job is not None:
        _job.cancel()
        deadline = time.monotonic() + 10
        while not _job.done and time.monotonic() < deadline:
            time.sleep(0.05)
        _job = None
