# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine Live Simulation (experimental).

One interactive ``AETHER Engine.exe`` session simulates the enabled Cloth
objects while Blender stays responsive. The artist's meshes are never
written: each Cloth gets a temporary preview object (a copy of its evaluated
mesh, marked ``cloth_next_aether_live``) that shows the simulation, and the
original is hidden until the Live Simulation stops. Stopping removes the
previews and restores visibility, the timeline frame and every muted
playback modifier.

A main-thread timer reads the newest complete frame from shared memory (a
seqlocked double buffer: frames may be skipped, never torn) and writes it to
the preview meshes. When the timeline frame changes it samples the animated
Colliders and animation-following Pins and sends them as one batched input.
No ``bpy`` access ever happens on another thread.
"""
from __future__ import annotations

import time

import bpy
import numpy as np

from ..aether_engine import client as aclient, engine as aengine
from . import aether_engine_backend as backend

LIVE_MARK = "cloth_next_aether_live"
TICK_SECONDS = 1.0 / 60.0

_session: "LiveSession | None" = None


class LiveSession:
    def __init__(self, context):
        status = backend.engine_status(refresh=True)
        if not status.ready:
            raise backend.AetherSceneError(status.title, status.code or "CNX-E131")
        scene = context.scene
        self.scene_name = scene.name
        self.quality = backend.quality_of(scene, live=True)
        prep = backend.AetherPreparation(context, quality=self.quality, live=True)
        self.engine: aclient.EngineProcess | None = None
        self.previews: list[tuple[str, str, np.ndarray]] = []   # (original, preview, inverse matrix 4x4)
        self.hidden: list[tuple[str, bool]] = []
        self.offsets: list[tuple[int, int]] = []
        self.colliders = [(c["name"], c["count"], c["animated"]) for c in prep.colliders]
        self.follow = [(c.obj_name, c.pin_vertices, len(c.local)) for c in prep.cloths if c.follow]
        self.follow_ids: list[np.ndarray] = []
        self.last_frame_seen = -1
        self.last_timeline = None
        self.paused = True
        self.message = ""
        self.frames_shown = 0
        self.started = time.monotonic()
        try:
            request = prep.request()
            self.engine = aclient.EngineProcess.start(status.identity.path)
            self.hello = self.engine.hello()
            self.session = self.engine.create_session(
                mode="interactive", quality=self.quality, fps=request.fps, gravity=request.gravity,
                self_collision=request.self_collision,
                cloths=[{"positions": c.rest_world, "triangles": c.triangles,
                         "material": c.material.as_protocol(),
                         "pins": c.registered_pins if len(c.registered_pins) else None}
                        for c in request.cloths],
                meshes=[{"vertices": c.rest_world, "triangles": c.triangles} for c in request.colliders])
            setup: dict = {}
            adds, next_id = [], 1
            for p in request.pins:
                ids = np.arange(next_id, next_id + len(p.vertices), dtype=np.int32)
                next_id += len(p.vertices)
                adds += [{"cloth": p.cloth, "vertex": int(v), "stiffness": float(p.stiffness)}
                         for v in p.vertices.tolist()]
                if p.targets is not None or prep.cloths[p.cloth].follow:
                    self.follow_ids.append(ids)
            if adds:
                setup["pin_add"] = adds
            if request.seams:
                setup["seam_add"] = [{"cloth_a": s.cloth_a, "cloth_b": s.cloth_b, "chain_a": list(s.chain_a),
                                      "chain_b": list(s.chain_b), "stiffness": s.stiffness, "progress": 1.0}
                                     for s in request.seams]
            if setup:
                self.engine.request("set_inputs", setup)
            offset = 0
            for c in request.cloths:
                n = len(c.rest_local)
                self.offsets.append((offset, n))
                offset += n
                self._make_preview(bpy.data.objects[c.object_name], c.rest_local, c.world_matrix)
        except Exception:
            prep.close()
            self.stop()
            raise
        prep.close()

    # -------------------------------------------------- previews
    def _make_preview(self, original, rest_local: np.ndarray, matrix: np.ndarray) -> None:
        mesh = original.data.copy()
        mesh.name = f"{original.name} · AETHER Live"
        if len(mesh.vertices) != len(rest_local):
            raise backend.AetherSceneError(f"{original.name}: the preview mesh does not match the simulation",
                                           "CNX-E176")
        mesh.vertices.foreach_set("co", rest_local.reshape(-1))
        mesh.update()
        preview = bpy.data.objects.new(f"{original.name} · AETHER Live", mesh)
        preview[LIVE_MARK] = original.name
        preview.matrix_world = original.matrix_world.copy()
        for slot_index, slot in enumerate(original.material_slots):
            if slot.material is not None and slot_index < len(mesh.materials):
                mesh.materials[slot_index] = slot.material
        collections = list(original.users_collection) or [bpy.context.scene.collection]
        for collection in collections:
            collection.objects.link(preview)
        self.hidden.append((original.name, bool(original.hide_get())))
        original.hide_set(True)
        self.previews.append((original.name, preview.name, np.linalg.inv(matrix)))

    def _remove_previews(self) -> None:
        for _original, preview_name, _inv in self.previews:
            preview = bpy.data.objects.get(preview_name)
            if preview is not None and preview.get(LIVE_MARK):
                mesh = preview.data
                bpy.data.objects.remove(preview, do_unlink=True)
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
        self.previews = []
        for name, was_hidden in self.hidden:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                try:
                    obj.hide_set(was_hidden)
                except RuntimeError:  # not in the active view layer anymore
                    pass
        self.hidden = []

    # -------------------------------------------------- control
    def command(self, name: str, fields: dict | None = None) -> None:
        try:
            self.engine.request(name, fields or {}, timeout=30)
        except aclient.EngineError as exc:
            code, message = aengine.map_error(exc.name, str(exc).split(": ", 1)[-1])
            self.message = f"{code} {message}"
        except (aclient.EngineGone, TimeoutError) as exc:
            self.message = str(exc)

    def play(self) -> None:
        self.command("run", {"realtime": True})
        self.paused = False

    def pause(self) -> None:
        self.command("pause")
        self.paused = True

    def reset(self) -> None:
        self.pause()
        self.command("reset")
        self.last_frame_seen = -1

    def stop(self) -> None:
        engine = self.engine
        self.engine = None
        if engine is not None:
            try:
                engine.shutdown(timeout=15)
            except Exception:  # noqa: BLE001 - the job object ends it anyway
                engine.kill()
        self._remove_previews()

    # -------------------------------------------------- timer (main thread)
    def tick(self) -> bool:
        """False when the session ended (engine gone or objects removed)."""
        engine = self.engine
        if engine is None:
            return False
        if not engine.alive:
            code, message = aengine.exit_reason(engine.proc.returncode)
            last = engine.last_error or {}
            if last.get("name"):
                code, message = aengine.map_error(last["name"], str(last.get("message", "")))
            self.message = f"{code} {message}"
            return False
        self._drain_events()
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is None:
            return False
        if scene.frame_current != self.last_timeline:
            self.last_timeline = scene.frame_current
            self._send_inputs()
        got = self.session.frames.latest()
        if got is not None and got[0] != self.last_frame_seen:
            frame, world, _t = got
            self.last_frame_seen = frame
            for (original, preview_name, inv), (offset, n) in zip(self.previews, self.offsets):
                preview = bpy.data.objects.get(preview_name)
                if preview is None:
                    return False
                local = world[offset:offset + n].astype(np.float64) @ inv[:3, :3].T + inv[:3, 3]
                preview.data.vertices.foreach_set("co", local.astype(np.float32).reshape(-1))
                preview.data.update()
            self.frames_shown += 1
        return True

    def _send_inputs(self) -> None:
        if not any(animated for _n, _c, animated in self.colliders) and not self.follow:
            return
        depsgraph = bpy.context.evaluated_depsgraph_get()
        inputs: dict = {}
        meshes = []
        for index, (name, count, animated) in enumerate(self.colliders):
            obj = bpy.data.objects.get(name)
            if animated and obj is not None:
                meshes.append({"index": index, "vertices": aclient.f32(
                    backend._evaluated_world(obj, depsgraph, count))})
        if meshes:
            inputs["mesh_colliders"] = meshes
        ids, targets = [], []
        for (name, vertices, count), pin_ids in zip(self.follow, self.follow_ids):
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            world = backend._evaluated_world(obj, depsgraph, count)
            ids.append(pin_ids)
            targets.append(world[vertices])
        if ids:
            inputs["pin_targets"] = {"ids": aclient.i32(np.concatenate(ids)),
                                     "targets": aclient.f32(np.concatenate(targets))}
        if inputs:
            self.command("set_inputs", inputs)

    def _drain_events(self) -> None:
        while True:
            try:
                ev = self.engine.events.get_nowait()
            except Exception:  # noqa: BLE001 - queue empty
                return
            if ev.get("event") in {"error", "warning"}:
                err = ev.get("error") or {}
                self.message = str(err.get("message") or ev.get("message") or "")
                if ev.get("event") == "error":
                    self.paused = True


# ------------------------------------------------------------------ module API
def is_running() -> bool:
    return _session is not None


def is_paused() -> bool:
    return _session is None or _session.paused


def status_line() -> str:
    s = _session
    if s is None:
        return ""
    if s.message:
        return s.message
    state = "paused" if s.paused else "running"
    return f"Live {s.quality.title()} · {state} · frame {max(0, s.last_frame_seen)}"


def _timer():
    global _session
    s = _session
    if s is None:
        return None
    try:
        alive = s.tick()
    except Exception as exc:  # noqa: BLE001 - the live session must never wedge Blender
        s.message = f"Live Simulation stopped: {exc}"
        alive = False
    if not alive:
        message = s.message
        s.stop()
        _session = None
        backend._tag_redraw()
        if message:
            print(f"Cloth NeXt AETHER Live: {message}")
        return None
    backend._tag_redraw()
    return TICK_SECONDS


def start(context) -> LiveSession:
    global _session
    if _session is not None:
        raise backend.AetherSceneError("The Live Simulation is already running", "CNX-E169")
    from ..bake.controller import shared_controller
    if shared_controller.snapshot().active or backend.is_running():
        raise backend.AetherSceneError("Wait until the Bake has finished", "CNX-E169")
    _session = LiveSession(context)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK_SECONDS)
    return _session


def stop() -> None:
    global _session
    s = _session
    _session = None
    if s is not None:
        s.stop()
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)


def cleanup_leftovers() -> int:
    """Remove preview objects a crashed session left behind (marked objects only)."""
    removed = 0
    for obj in list(bpy.data.objects):
        name = obj.get(LIVE_MARK) if hasattr(obj, "get") else None
        if name and (_session is None or obj.name not in {p for _o, p, _i in _session.previews}):
            original = bpy.data.objects.get(str(name))
            if original is not None:
                try:
                    original.hide_set(False)
                except RuntimeError:
                    pass
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            removed += 1
    return removed


class CLOTHNEXT_OT_aether_live_start(bpy.types.Operator):
    bl_idname = "clothnext.aether_live_start"
    bl_label = "Start Live Simulation"
    bl_description = ("Simulate the Cloth objects interactively with the AETHER Engine. Your meshes are not "
                      "changed: temporary preview objects show the result until you stop")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _session is None and not backend.is_running()

    def execute(self, context):
        cleanup_leftovers()
        try:
            session = start(context)
        except Exception as exc:  # noqa: BLE001 - shown to the artist
            message = exc.record.user_message if hasattr(exc, "record") else str(exc)
            self.report({"ERROR"}, f"{getattr(exc, 'code', '')} {message}".strip())
            return {"CANCELLED"}
        session.play()
        return {"FINISHED"}


class CLOTHNEXT_OT_aether_live_toggle_pause(bpy.types.Operator):
    bl_idname = "clothnext.aether_live_toggle_pause"
    bl_label = "Pause or Resume Live Simulation"
    bl_description = "Pause or continue the AETHER Engine Live Simulation"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _session is not None

    def execute(self, context):
        if _session.paused:
            _session.play()
        else:
            _session.pause()
        return {"FINISHED"}


class CLOTHNEXT_OT_aether_live_reset(bpy.types.Operator):
    bl_idname = "clothnext.aether_live_reset"
    bl_label = "Reset Live Simulation"
    bl_description = "Return the Live Simulation to the state it started from"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _session is not None

    def execute(self, context):
        _session.reset()
        return {"FINISHED"}


class CLOTHNEXT_OT_aether_live_stop(bpy.types.Operator):
    bl_idname = "clothnext.aether_live_stop"
    bl_label = "Stop Live Simulation"
    bl_description = "Stop the Live Simulation, remove the preview objects and show the original Cloth objects"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _session is not None

    def execute(self, context):
        stop()
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_aether_live_start, CLOTHNEXT_OT_aether_live_toggle_pause,
           CLOTHNEXT_OT_aether_live_reset, CLOTHNEXT_OT_aether_live_stop)


def _on_load_pre(*_args):
    stop()


_on_load_pre = getattr(getattr(bpy.app, "handlers", None), "persistent", lambda f: f)(_on_load_pre)


def register_handlers() -> None:
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_pre", None)
    if handlers is not None and _on_load_pre not in handlers:
        handlers.append(_on_load_pre)


def shutdown() -> None:
    stop()
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_pre", None)
    if handlers is not None and _on_load_pre in handlers:
        handlers.remove(_on_load_pre)
