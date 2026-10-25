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


def is_running() -> bool:
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
                                      "frames": [first, last]},
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
        identity=identity_json, uv=uv)
    partial = {"object": identity["object"], "object_name": obj.name, "identity_json": identity_json,
               "fingerprints": identity["fingerprints"], "settings_fingerprint": tcache.fingerprints(
                   settings=record)["combined"], "frames": [first, last],
               "sources": {"pc2_file": cloth.pc2_path.name, "water_file": contact.path.name}}
    return BakePlan(obj.name, request, contact, cloth, final, staging, partial, executable,
                    len(obj.data.polygons), image_size(len(obj.data.polygons)))


def settings_fingerprint(obj, context=None) -> str:
    return tcache.fingerprints(settings=settings_record(obj, context))["combined"]


# ------------------------------------------------------------------- job

class BakeJob:
    """Background preparation + engine process; the modal operator polls it."""

    def __init__(self, plan: BakePlan):
        self.plan = plan
        self.phase = "Preparing water contact"
        self.progress = 0.0
        self.error: str | None = None
        self.code = ""
        self.result: dict | None = None
        self.cancelled = False
        self.engine: engine.EngineJob | None = None
        self.wet_frames = 0
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="TETHYS bake", daemon=True)
        self._thread.start()

    def _check_cancel(self):
        if self._cancel.is_set():
            raise _Cancelled()

    def _run(self):
        plan = self.plan
        try:
            tcache.remove(plan.staging, stage="TETHYS_STAGING")
            plan.staging.root.mkdir(parents=True, exist_ok=True)
            self.wet_frames = contact_export.export_contact(
                plan.staging.environment, plan.contact, plan.cloth.pc2_path, plan.cloth.vertex_count,
                plan.request.matrix_world, plan.request.first_frame, plan.request.last_frame, 0,
                check_cancel=self._check_cancel)
            protocol.write_atomic_bytes(plan.staging.request, protocol.encode_request(plan.request))
            self._check_cancel()
            self.phase = "Simulating wetness"
            self.engine = engine.EngineJob(plan.executable, plan.staging.request, plan.staging.cancel)
            total = max(1, plan.request.last_frame - plan.request.first_frame)
            while True:
                for event in self.engine.take_events():
                    kind = event.get("event")
                    if kind == "progress":
                        self.progress = float(event.get("done", 0)) / total
                    elif kind == "complete":
                        self.result = event
                    elif kind == "error":
                        self.code = str(event.get("code", "TETHYS-E999"))
                        self.error = str(event.get("message", "The TETHYS Engine failed"))
                    elif kind == "cancelled":
                        self.cancelled = True
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
                if event.get("event") == "complete":
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
    def done(self) -> bool:
        return not self._thread.is_alive()


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------- publish

def write_images(plan: BakePlan, frames, *, image_name="__CNX_TETHYS_EXPORT"):
    """Convert cache frames to EXR images (R surface mm, G saturation, B contact)."""
    width, height = plan.image_size
    pixels = np.zeros((height * width, 4), np.float32)
    pixels[:, 3] = 1.0
    image = bpy.data.images.new(image_name, width, height, alpha=True, float_buffer=True)
    try:
        image.colorspace_settings.name = "Non-Color"
        image.file_format = "OPEN_EXR"
        with protocol.CacheReader(plan.staging.cache) as reader:
            for frame in frames:
                data = reader.read(frame)
                count = plan.polygon_count
                pixels[:count, 0] = data.channels["surface"]
                pixels[:count, 1] = data.channels["saturation"]
                pixels[:count, 2] = data.channels.get("contact", 0.0)
                image.pixels.foreach_set(pixels.reshape(-1))
                path = plan.staging.frame_image(frame)
                image.filepath_raw = str(path)
                image.save()
    finally:
        bpy.data.images.remove(image)


def publish(plan: BakePlan) -> dict:
    """Move the staged bake into place and seal its sidecar (main thread)."""
    final, staging = plan.final, plan.staging
    partial = tcache.write_partial(staging, plan.record)
    images = {"directory": final.frames.name, "width": plan.image_size[0], "height": plan.image_size[1],
              "format": "OPEN_EXR", "channels": ["surface_mm", "saturation", "contact"]}
    staged = tcache.write_complete(staging, partial, images=images, result=_ledger(plan))
    # Replace only this object's owned files; the cloth cache is untouched.
    tcache.remove(final, stage="TETHYS_REPLACE")
    os.replace(staging.cache, final.cache)
    final.frames.mkdir(parents=True, exist_ok=True)
    for image in sorted(staging.frames.glob(f"{staging.stem}_*.exr")):
        frame = int(image.stem.rsplit("_", 1)[1])
        os.replace(image, final.frame_image(frame))
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
    sealed = publish(plan)
    from . import tethys_overlay, tethys_shader
    notes = tethys_shader.attach(obj, plan.final, plan.image_size, plan.request.first_frame,
                                 plan.request.last_frame)
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


def bake_blocking(context, obj, *, cancel_after: float | None = None) -> tuple[str, BakeJob]:
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
            time.sleep(0.02)
        if job.cancelled or job.error:
            tcache.remove(plan.staging, stage="TETHYS_ABORT")
            return ("Wetness bake cancelled" if job.cancelled else f"{job.code} {job.error}".strip()), job
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

class CLOTHNEXT_OT_tethys_bake(bpy.types.Operator):
    bl_idname = "clothnext.tethys_bake"
    bl_label = "Bake Wetness"
    bl_description = ("Simulate surface water and absorbed moisture on the baked cloth with the "
                      "TETHYS Engine. The cloth cache is only read")
    bl_options = {"REGISTER"}

    _timer = None
    _job: BakeJob | None = None
    _frames: list
    _stage = ""

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and getattr(obj, "cloth_next", None) and obj.cloth_next.role == "CLOTH"
                    and obj.cloth_next.tethys.enabled and _job is None)

    def invoke(self, context, _event):
        return self.execute(context)

    def execute(self, context):
        global _job
        obj = context.object
        try:
            plan = prepare(context, obj)
        except TethysBakeError as exc:
            obj.cloth_next.tethys.status = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._job = BakeJob(plan)
        _job = self._job
        self._object_name = obj.name
        self._stage = "ENGINE"
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
        obj = bpy.data.objects.get(self._object_name)
        if obj is not None:
            obj.cloth_next.tethys.status = message
        context.workspace.status_text_set(None)
        tag_redraw()
        self.report({level}, message)
        return {"FINISHED"} if level == "INFO" else {"CANCELLED"}

    def modal(self, context, event):
        job = self._job
        if event.type == "ESC" and event.value == "PRESS":
            job.cancel()
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        obj = bpy.data.objects.get(self._object_name)
        if obj is None:
            job.cancel()
        if self._stage == "ENGINE":
            context.workspace.status_text_set(
                f"TETHYS · {job.phase} · {job.progress * 100:.0f}% · Esc to cancel")
            if not job.done:
                return {"RUNNING_MODAL"}
            plan = job.plan
            if job.cancelled or job.error:
                message = "Wetness bake cancelled" if job.cancelled else f"{job.code} {job.error}".strip()
                try:
                    tcache.remove(plan.staging, stage="TETHYS_ABORT")
                except OSError:
                    pass
                return self._finish(context, message, "WARNING" if job.cancelled else "ERROR")
            self._frames = list(range(plan.request.first_frame, plan.request.last_frame + 1))
            self._stage = "IMAGES"
            plan.staging.frames.mkdir(parents=True, exist_ok=True)
            return {"RUNNING_MODAL"}
        if self._stage == "IMAGES":
            plan = job.plan
            try:
                batch, self._frames = self._frames[:8], self._frames[8:]
                write_images(plan, batch)
                done = plan.request.last_frame - plan.request.first_frame + 1 - len(self._frames)
                context.workspace.status_text_set(f"TETHYS · Writing wetness frames {done}")
                if self._frames:
                    return {"RUNNING_MODAL"}
                return self._finish(context, finalize(plan, obj, job))
            except Exception as exc:  # noqa: BLE001 - keep the previous result, report clearly
                try:
                    tcache.remove(plan.staging, stage="TETHYS_ABORT")
                except OSError:
                    pass
                return self._finish(context, f"Wetness bake could not be published: {exc}", "ERROR")
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._job is not None:
            self._job.cancel()
        global _job
        _job = None
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


CLASSES = (CLOTHNEXT_OT_tethys_bake, CLOTHNEXT_OT_tethys_clear)


def shutdown() -> None:
    """Add-on unregister: stop a running engine and release files."""
    global _job
    if _job is not None:
        _job.cancel()
        deadline = time.monotonic() + 10
        while not _job.done and time.monotonic() < deadline:
            time.sleep(0.05)
        _job = None
