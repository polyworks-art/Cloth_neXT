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

import os
import time

import bpy
import numpy as np

from ..aether_engine import bake as abake, client as aclient, engine as aengine, grab as agrab
from . import aether_engine_backend as backend

LIVE_MARK = "cloth_next_aether_live"
TICK_SECONDS = 1.0 / 60.0
WIND_RESEND_SECONDS = 0.2           # Live: gusts / animated Wind Empties are re-sent this often
PANEL_REDRAW_SECONDS = 0.25          # the status line; the 3D views redraw on every new frame
# While the artist moves the view (orbit, pan, zoom), the preview shows at most this many new frames
# per second. Each preview frame costs Blender's main thread a mesh update and a full 3D redraw
# (NEON: ~37 ms at 28 frames/s, so the thread was busy all the time and orbiting felt sluggish).
NAVIGATING_PREVIEW_FPS = float(__import__("os").environ.get("CLOTH_NEXT_LIVE_NAV_FPS", "8"))
NAVIGATING_HOLD_SECONDS = 0.25       # a draw with a changed view matrix = the artist navigates
# The preview leaves Blender at least this share of its main thread for the interface: with heavy
# scenes (NEON with its 709k-face FLIP surface: ~50 ms per 3D draw) redrawing on every simulated frame
# kept the main thread busy all the time and every click and view change waited behind a redraw.
UI_SHARE = float(__import__("os").environ.get("CLOTH_NEXT_LIVE_UI_SHARE", "0"))   # 0: off (measured: only the navigation limit helps)
PROFILE = bool(os.environ.get("CLOTH_NEXT_AETHER_LIVE_PROFILE"))

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
        self.attached: list = []
        self.prep = prep
        self.last_frame_seen = -1
        self.last_timeline = None
        self.paused = True
        self.message = ""
        self.frames_shown = 0
        self.views: dict = {}         # last drawn view matrix per 3D region
        self.draw_ms = 0.0            # smoothed duration of one 3D view draw
        self._draw_t0 = None
        self._draw_start = bpy.types.SpaceView3D.draw_handler_add(_start_draw, (), "WINDOW", "PRE_VIEW")
        self.navigating_at = -1.0     # last draw with a changed view (the artist moved the view)
        self.shown_at = 0.0
        self._draw_watch = bpy.types.SpaceView3D.draw_handler_add(_watch_draw, (), "WINDOW", "POST_PIXEL")
        self.started = time.monotonic()
        self.targets: list = []              # per preview: (mesh name, local 3x4 float32 or None, buffer)
        self.profile: dict = {}
        self._panel_redraw = 0.0
        self._last_write = -1
        self.drew = False
        self._event_timer = None
        try:
            request = prep.request()
            self.engine = aclient.EngineProcess.start(status.identity.path)
            self.hello = self.engine.hello()
            self.session = self.engine.create_session(
                mode="interactive", quality=self.quality, fps=request.fps, gravity=request.gravity,
                self_collision=request.self_collision,
                air_damping=LIVE_AIR_DAMPING.get(str(self.quality).lower(), LIVE_AIR_DAMPING["normal"]),
                cloths=[abake.session_cloth(c) for c in request.cloths],
                meshes=[abake.session_collider(c) for c in request.colliders])
            setup: dict = {}
            adds, next_id = [], 1
            for p in request.pins:
                ids = np.arange(next_id, next_id + len(p.vertices), dtype=np.int32)
                next_id += len(p.vertices)
                adds += [{"cloth": p.cloth, "vertex": int(v), "stiffness": float(p.stiffness)}
                         for v in p.vertices.tolist()]
                if p.targets is None and prep.cloths[p.cloth].follow and p.vertices is prep.cloths[p.cloth].pin_vertices:
                    self.follow_ids.append(ids)
                attached = next((a for a in prep.attachments if a["vertices"] is p.vertices), None)
                if attached is not None:
                    self.attached.append((attached, ids))
            if adds:
                setup["pin_add"] = adds
            if request.seams:
                setup["seam_add"] = [{"cloth_a": s.cloth_a, "cloth_b": s.cloth_b, "chain_a": list(s.chain_a),
                                      "chain_b": list(s.chain_b), "stiffness": s.stiffness,
                                      "progress": 1.0 if s.closing else 0.0}
                                     for s in request.seams]
            if request.wind:   # engine API 1.4: the scene's Wind (gusts follow in tick)
                setup["wind"] = request.wind[0]
            if setup:
                self.engine.request("set_inputs", setup)
            self._wind_sent_at = time.monotonic()
            # Draped state (Marvelous style): start from the stored drape, the rest shape stays the basis.
            self.start_state = None
            if any(d is not None for d in prep.drape_world):
                self.start_state = np.concatenate([d if d is not None else c.rest_world
                                                   for d, c in zip(prep.drape_world, request.cloths)]).astype(np.float32)
                self._send_start_state()
            offset = 0
            for c, drape in zip(request.cloths, prep.drape_world):
                n = len(c.rest_local)
                self.offsets.append((offset, n))
                offset += n
                shown = c.rest_local
                if drape is not None:
                    inv = np.linalg.inv(c.world_matrix)
                    shown = (drape.astype(np.float64) @ inv[:3, :3].T + inv[:3, 3]).astype(np.float32)
                self._make_preview(bpy.data.objects[c.object_name], shown, c.world_matrix)
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
        # The preview is rewritten up to 30 times a second. Custom split normals (Marvelous
        # exports carry them) and sharp edges make Blender compute corner normals on every
        # update; the preview is shaded with plain vertex normals instead (measured on NEON).
        # Every other generic attribute (selection, tool data) is copied on each evaluation
        # too; the preview keeps only its positions, topology, UV maps and material indices.
        keep = {"position", "material_index"} | {uv.name for uv in mesh.uv_layers}
        drop = [a.name for a in mesh.attributes
                if (a.name not in keep and not a.name.startswith(".")) or a.name.startswith((".select", ".uv_select"))]
        for name in drop:
            attribute = mesh.attributes.get(name)
            try:
                if attribute is not None:
                    mesh.attributes.remove(attribute)
            except RuntimeError:     # attributes Blender requires cannot be removed
                pass
        mesh.vertices.foreach_set("co", rest_local.reshape(-1))
        mesh.update()
        preview = bpy.data.objects.new(f"{original.name} · AETHER Live", mesh)
        if mesh.shape_keys is not None:      # the copied keys would override the written positions
            preview.shape_key_clear()
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
        inv = np.linalg.inv(matrix)
        self.previews.append((original.name, preview.name, inv))
        identity = np.allclose(inv, np.eye(4), atol=0.0, rtol=0.0)
        self.targets.append((mesh.name, None if identity else inv[:3].astype(np.float32),
                             np.empty((len(rest_local), 3), np.float32)))

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
        self._wake(True)

    def pause(self) -> None:
        self.command("pause")
        self.paused = True
        self._wake(False)

    def _wake(self, on: bool) -> None:
        """While running, a window event timer keeps Blender's event loop from idling.

        Without pending events the loop sleeps about 5 ms between the Live timer and the
        redraw it tagged (measured on NEON: 23.5 instead of 25+ updates per second).
        """
        wm = getattr(bpy.context, "window_manager", None)
        if wm is None:
            return
        if self._event_timer is not None:
            try:
                wm.event_timer_remove(self._event_timer)
            except (ReferenceError, ValueError, RuntimeError):
                pass
            self._event_timer = None
        window = getattr(bpy.context, "window", None) or next(iter(wm.windows), None)
        if on and window is not None:
            self._event_timer = wm.event_timer_add(1.0 / 120.0, window=window)

    def _send_start_state(self) -> None:
        if self.start_state is None:
            return
        self.engine.request("set_state", {"positions": aclient.f32(self.start_state),
                                          "velocities": aclient.f32(np.zeros_like(self.start_state))}, timeout=60)

    def reset(self) -> None:
        self.pause()
        self.command("reset")
        if self.start_state is not None:
            try:
                self._send_start_state()
            except Exception as exc:  # noqa: BLE001 - shown on the panel
                self.message = str(exc)
        self.last_frame_seen = -1

    def keep_drape(self) -> int:
        """Store the current simulated positions as each original object's drape key (Marvelous style)."""
        from . import aether_drape
        if self.engine is None or not self.engine.alive or self.last_frame_seen < 1:
            return 0
        positions, _velocities, _frame = self.engine.get_state()
        kept = 0
        for (original, _preview, inv), (offset, n) in zip(self.previews, self.offsets):
            obj = bpy.data.objects.get(original)
            if obj is None or len(obj.data.vertices) != n:
                continue
            world = positions[offset:offset + n].astype(np.float64)
            aether_drape.store_drape(obj, (world @ inv[:3, :3].T + inv[:3, 3]).astype(np.float32))
            kept += 1
        return kept

    def navigating(self, now: float) -> bool:
        return _grab_operator is None or not _grab_operator.ids             if now - self.navigating_at < NAVIGATING_HOLD_SECONDS else False

    def stop(self, keep: bool = False) -> None:
        for attr in ("_draw_watch", "_draw_start"):
            handle = getattr(self, attr, None)
            if handle is not None:
                bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
                setattr(self, attr, None)
        self._wake(False)
        if keep:
            try:
                self.message = f"Kept the drape of {self.keep_drape()} Cloth object(s)"
            except Exception as exc:  # noqa: BLE001 - never block stopping
                self.message = f"Drape not kept: {exc}"
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
        if getattr(self.prep, "wind_animated", False) and time.monotonic() - self._wind_sent_at > WIND_RESEND_SECONDS:
            # gusts and animated Force Empties: the wind of the simulated time, a few times a second
            self._wind_sent_at = time.monotonic()
            wind = self.prep.wind_at(bpy.context, self.prep.start_frame,
                                     gust_frame=self.prep.start_frame + max(0, self.last_frame_seen))
            if wind is not None:
                self.command("set_inputs", {"wind": wind})
        self.drew = False
        writes = self.session.frames.write_count
        if writes == self._last_write:
            return True                      # no new frame since the last tick
        now = time.monotonic()
        if self.navigating(now) and now - self.shown_at < 1.0 / NAVIGATING_PREVIEW_FPS:
            return True                      # the artist moves the view: leave Blender the time to draw it
        if now - self.shown_at < self.draw_ms / 1000.0 / max(0.05, 1.0 - UI_SHARE):
            return True                      # a heavy scene: keep UI_SHARE of the main thread for the interface
        self.shown_at = now
        self._last_write = writes
        t0 = time.perf_counter()
        got = self.session.frames.latest()
        t1 = time.perf_counter()
        if got is not None and got[0] != self.last_frame_seen:
            frame, world, _t = got
            self.last_frame_seen = frame
            for (mesh_name, local, buf), (offset, n) in zip(self.targets, self.offsets):
                mesh = bpy.data.meshes.get(mesh_name)
                if mesh is None or len(mesh.vertices) != n:
                    return False
                part = world[offset:offset + n]
                if local is None:
                    buf[...] = part
                else:                        # single precision: the preview only displays the frame
                    np.matmul(part, local[:, :3].T, out=buf)
                    buf += local[:, 3]
                # vertices.co, not attributes["position"]: the attribute API sends a geometry
                # notifier that redraws every Properties editor on each frame (measured).
                mesh.vertices.foreach_set("co", buf.reshape(-1))
                mesh.update_tag()
            self.frames_shown += 1
            self.drew = True
            if PROFILE:
                self.profile = {"read_ms": (t1 - t0) * 1000.0, "write_ms": (time.perf_counter() - t1) * 1000.0}
        return True

    def _send_inputs(self) -> None:
        if not any(animated for _n, _c, animated in self.colliders) and not self.follow and not self.attached:
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
        for attachment, pin_ids in self.attached:
            ids.append(pin_ids)
            targets.append(self.prep.attachment_targets(attachment, depsgraph))
        if ids:
            inputs["pin_targets"] = {"ids": aclient.i32(np.concatenate(ids)),
                                     "targets": aclient.f32(np.concatenate(targets))}
        if inputs:
            self.command("set_inputs", inputs)

    # -------------------------------------------------- interactive grab (main thread)
    def preview_hit(self, obj, polygon: int):
        """(cloth index, local vertex indices) of a preview face, or None."""
        names = [p for _o, p, _i in self.previews]
        if obj is None or obj.name not in names:
            return None
        cloth = names.index(obj.name)
        mesh = obj.data
        if not 0 <= polygon < len(mesh.polygons):
            return None
        return cloth, list(mesh.polygons[polygon].vertices)

    def grab(self, cloth: int, vertices: list, targets: np.ndarray, stiffness: float) -> tuple[list, list]:
        """Compliant runtime pins on the grabbed vertices: (engine ids, indices into vertices)."""
        taken = set(self.prep.cloths[cloth].registered.tolist()) | set(self.prep.cloths[cloth].pin_vertices.tolist())
        for a in self.prep.attachments:
            if a["cloth"] == cloth:
                taken |= set(a["vertices"].tolist())
        kept = [k for k, v in enumerate(vertices) if int(v) not in taken]   # pins and attachments win
        adds = [{"cloth": cloth, "vertex": int(vertices[k]), "stiffness": float(stiffness),
                 "target": aclient.f32(targets[k])} for k in kept]
        if not adds:
            return [], []
        try:
            reply = self.engine.request("set_inputs", {"pin_add": adds}, timeout=10)
        except (aclient.EngineError, aclient.EngineGone, TimeoutError) as exc:
            self.message = str(exc)
            return [], []
        return [int(i) for i in (reply.get("created") or {}).get("pin_ids", [])], kept

    def move_grab(self, ids: list, targets: np.ndarray) -> None:
        if ids:
            self.command("set_inputs", {"pin_targets": {"ids": aclient.i32(ids), "targets": aclient.f32(targets)}})

    def release(self, ids: list) -> None:
        if ids:
            # the grabbed points leave with their neighbours' motion: no fling after a fast drag
            self.command("set_inputs", {"pin_remove": {"ids": aclient.i32(ids), "release": "neighbor_mean"}})

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
    _redraw(s)
    return TICK_SECONDS


def _start_draw() -> None:
    if _session is not None:
        _session._draw_t0 = time.perf_counter()


def _watch_draw() -> None:
    """Every 3D view draw: a changed view matrix means the artist moves the view (orbit, pan, zoom)."""
    s = _session
    rv3d = getattr(bpy.context, "region_data", None)
    if s is None or rv3d is None:
        return
    if s._draw_t0 is not None:
        ms = (time.perf_counter() - s._draw_t0) * 1000.0
        s.draw_ms = ms if s.draw_ms == 0.0 else 0.8 * s.draw_ms + 0.2 * ms
        s._draw_t0 = None
    try:
        m = rv3d.view_matrix
        key = (m[0][0], m[0][1], m[0][2], m[0][3], m[1][0], m[1][1], m[1][2], m[1][3],
               m[2][0], m[2][1], m[2][2], m[2][3], rv3d.view_distance)
    except (AttributeError, ReferenceError):
        return
    region = rv3d.as_pointer()
    if s.views.get(region, key) != key:
        s.navigating_at = time.monotonic()
    s.views[region] = key


def _redraw(s: LiveSession) -> None:
    """3D views on every new frame; the panels (status line) a few times a second."""
    now = time.monotonic()
    panels = now - s._panel_redraw >= PANEL_REDRAW_SECONDS
    if panels:
        s._panel_redraw = now
    if not (s.drew or panels):
        return
    for window in getattr(getattr(bpy.context, "window_manager", None), "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if area.type == "VIEW_3D" and s.drew:
                # only the 3D region: headers, toolbars and the sidebar are unchanged
                for region in area.regions:
                    if region.type == "WINDOW":
                        region.tag_redraw()
            elif area.type == "PROPERTIES" and panels:
                area.tag_redraw()


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


def stop(keep: bool = False) -> None:
    global _session
    s = _session
    _session = None
    if s is not None:
        s.stop(keep=keep)
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
        start_grab(context)   # Grab Cloth is always on while the Live Simulation runs
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


_cursor_px: float | None = None


def _system_cursor_px(context) -> float:
    """Edge of the system mouse cursor box in pixels (Windows: pointer size setting x DPI)."""
    global _cursor_px
    if _cursor_px is None:
        _cursor_px = 32.0 * context.preferences.system.pixel_size
        if os.name == "nt":
            try:
                import ctypes
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
                    base = float(winreg.QueryValueEx(key, "CursorBaseSize")[0])
            except OSError:
                base = 32.0
            try:
                dpi = float(ctypes.windll.user32.GetDpiForSystem())
            except (AttributeError, OSError):
                dpi = 96.0
            _cursor_px = base * (dpi or 96.0) / 96.0
    return _cursor_px


def _brightness_under(gpu, region, x, y, size) -> float | None:
    """Mean display brightness (0-1) of the viewport pixels the cursor icon will cover."""
    x0, y0 = max(int(x), 0), max(int(y), 0)
    w, h = min(int(x + size), region.width) - x0, min(int(y + size), region.height) - y0
    if w <= 0 or h <= 0:
        return None
    try:
        buffer = gpu.state.active_framebuffer_get().read_color(x0, y0, w, h, 4, 0, "FLOAT")
    except (RuntimeError, ValueError):
        return None
    buffer.dimensions = w * h * 4
    rgb = np.clip(np.asarray(buffer.to_list(), np.float32).reshape(-1, 4)[:, :3], 0.0, 1.0)
    return float((rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean())


def _inverted_icon(floating_simulation, name: str) -> str:
    """A colour-inverted copy of an asset icon (alpha kept), cached beside the original."""
    inverted = name + "_inverted"
    image = floating_simulation._images.get(inverted)
    try:
        if image is not None and image.name in bpy.data.images:
            return inverted
    except ReferenceError:
        pass
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "assets" / "icons" / f"{name}.png"
    source = bpy.data.images.load(str(path), check_existing=False)
    pixels = np.empty(len(source.pixels), np.float32)
    source.pixels.foreach_get(pixels)
    pixels = pixels.reshape(-1, 4)
    pixels[:, :3] = 1.0 - pixels[:, :3]
    image = bpy.data.images.new(inverted, source.size[0], source.size[1], alpha=True)
    image.pixels.foreach_set(pixels.ravel())
    bpy.data.images.remove(source)
    floating_simulation._images[inverted] = image
    return inverted


_grab_operator = None   # the running always-on grab (one per Live Simulation)


def _view3d_under(window, event):
    """(region, region_3d) of the 3D View WINDOW region under the mouse, or (None, None)."""
    for area in getattr(getattr(window, "screen", None), "areas", ()):
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if (region.type == "WINDOW" and region.x <= event.mouse_x < region.x + region.width
                    and region.y <= event.mouse_y < region.y + region.height):
                return region, area.spaces.active.region_3d
    return None, None


# Air drag [1/s] of the Live Simulation per quality (bakes have none). Marvelous (black-box, 1 s free
# fall) keeps 84 % of free fall in Normal and 57 % in Fitting; drag 0.5/s gives 85 %, 2/s 57 %. Both
# calmed the NEON vest after a pull (95 % of it within 9-10 mm of its old place 2 s after letting go,
# 17 mm without).
LIVE_AIR_DAMPING = {"normal": 0.5, "animation": 0.5, "fitting": 2.0}


def _preview_world(cloth: int) -> np.ndarray:
    """World positions of the Live preview of cloth ``cloth`` as shown now."""
    obj = bpy.data.objects[_session.previews[cloth][1]]
    co = np.empty(len(obj.data.vertices) * 3, np.float64)
    obj.data.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


def _over_toolbar(context, region, event) -> bool:
    """True if the mouse is on the floating toolbar of this 3D View region."""
    from . import floating_simulation
    try:
        area = next((a for a in context.window.screen.areas if any(r == region for r in a.regions)), None)
        with context.temp_override(area=area, region=region):
            return floating_simulation.covers(bpy.context, event.mouse_x - region.x, event.mouse_y - region.y)
    except (AttributeError, RuntimeError, TypeError):
        return False


def start_grab(context) -> None:
    """Start the always-on grab of the Live Simulation in the window of ``context``."""
    if _grab_operator is not None or _session is None:
        return
    window = getattr(context, "window", None)
    if window is None:
        windows = bpy.context.window_manager.windows
        window = windows[0] if len(windows) else None
    if window is None:
        return
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None:
            continue
        with context.temp_override(window=window, area=area, region=region):
            bpy.ops.clothnext.aether_live_grab("INVOKE_DEFAULT")
        return


class CLOTHNEXT_OT_aether_live_grab(bpy.types.Operator):
    """Pull the cloth with the mouse while the Live Simulation runs (like grabbing in Marvelous)."""
    bl_idname = "clothnext.aether_live_grab"
    bl_label = "Grab Cloth"
    bl_description = ("Always on while the Live Simulation runs: drag the cloth with the left mouse button. The "
                      "grabbed point follows the mouse in the view plane and is released when you let go. "
                      "Clicks elsewhere work as usual")
    bl_options = {"INTERNAL"}

    # 0 (default): a hard grab, the point sticks to the mouse like in Marvelous; it stops following
    # where the cloth around it would overstretch (aether_engine.grab). A spring (> 0) lagged up to
    # 10 cm behind the mouse under a Stretch Limit (NEON vest at 0.2).
    strength: bpy.props.FloatProperty(name="Strength", default=0.0, min=0.0, max=10.0,
                                      description="0 = the point sticks to the mouse; above 0 a spring "
                                                  "(1 = ten times the cloth's membrane stiffness)")

    @classmethod
    def poll(cls, context):
        return _session is not None and _grab_operator is None

    # the pinch point of the icon (assets/icons/aether_grab_cursor.png), as a fraction from bottom-left
    HOTSPOT = (0.48, 0.53)
    # the icon fills its square; the system hand cursor fills about 22 of its 32 px box
    CURSOR_FILL = 22.0 / 32.0
    HOVER_INTERVAL = 0.05   # s between hover ray casts while the mouse moves

    def _draw_cursor(self, context):
        if context.region != self.region or self.mouse is None or not (self.ids or self.over_cloth):
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        from . import floating_simulation
        size = _system_cursor_px(context) * self.CURSOR_FILL
        x, y = self.mouse[0] - self.HOTSPOT[0] * size, self.mouse[1] - self.HOTSPOT[1] * size
        # the opposite of what is under the cursor: white hand on dark, black hand on light
        brightness = _brightness_under(gpu, context.region, x, y, size)
        if brightness is not None:   # hysteresis, so the colour does not flicker at the threshold
            if self.light_bg and brightness < 0.4:
                self.light_bg = False
            elif not self.light_bg and brightness > 0.6:
                self.light_bg = True
        name = "aether_grab_cursor"
        if self.light_bg:
            name = _inverted_icon(floating_simulation, name)
        gpu.state.blend_set("ALPHA")
        floating_simulation._asset_icon(gpu, batch_for_shader, name, x, y, size)
        gpu.state.blend_set("NONE")

    def invoke(self, context, event):
        global _grab_operator
        if _session is None or _grab_operator is not None:
            return {"CANCELLED"}
        self.ids, self.offsets, self.depth = [], None, None
        self._timer, self.desired = None, None
        self.region, self.rv3d, self.mouse = None, None, None
        self.over_cloth, self.hover_at, self.cursor_hidden, self.swallow = False, 0.0, False, False
        self.light_bg = False
        self.window = context.window
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_cursor, (context,), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        _grab_operator = self
        return {"RUNNING_MODAL"}

    def _hit(self, context, region, rv3d, event):
        """(ray hit, (cloth, vertices) of the Live preview face under the mouse or None)."""
        from bpy_extras import view3d_utils
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        hit, location, _normal, polygon, obj, _matrix = context.scene.ray_cast(
            context.evaluated_depsgraph_get(), origin, direction)
        found = (location, polygon, obj) if hit else None
        return found, _session.preview_hit(found[2].original if found else None, found[1] if found else -1)

    def _mouse_point(self, event):
        from bpy_extras import view3d_utils
        return np.array(view3d_utils.region_2d_to_location_3d(
            self.region, self.rv3d, (event.mouse_x - self.region.x, event.mouse_y - self.region.y), self.depth))

    def _set_cursor(self, hidden: bool) -> None:
        if hidden != self.cursor_hidden:
            try:
                if hidden:
                    self.window.cursor_modal_set("NONE")   # the grab icon is drawn at the mouse instead
                else:
                    self.window.cursor_modal_restore()
            except (ReferenceError, AttributeError):
                pass
            self.cursor_hidden = hidden

    def _finish(self, context):
        global _grab_operator
        self._let_go()
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
            self._handle = None
        self._set_cursor(False)
        if _grab_operator is self:
            _grab_operator = None
        try:
            for area in self.window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        except (ReferenceError, AttributeError):
            pass
        return {"FINISHED"}

    def cancel(self, context):
        self._finish(context)

    def _let_go(self):
        if self.ids and _session is not None:
            _session.release(self.ids)
        self.ids = []
        self.desired = None
        if getattr(self, "_timer", None) is not None:
            self.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _follow(self):
        """Move the grab toward the mouse as far as the cloth allows (again on every tick while held:
        a grab that stopped catches up once the cloth around it has followed)."""
        if self.ids and self.desired is not None:
            _session.move_grab(self.ids, self.cap.follow(self.desired, _preview_world(self.cloth),
                                                         self.grabbed).astype(np.float32))

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:  # noqa: BLE001 - never leave the cloth held by a grab that lost its operator
            self._let_go()
            self.swallow = False
            import traceback
            traceback.print_exc()
            return {"PASS_THROUGH"}

    def _modal(self, context, event):
        if _session is None:
            return self._finish(context)
        if self.ids and (event.type in {"WINDOW_DEACTIVATE", "ESC", "RIGHTMOUSE"}
                         or (event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"})):
            # a lost release (mouse let go outside Blender, focus change): let go now, never keep holding
            self._let_go()
            self.swallow = True
            return {"RUNNING_MODAL"}
        if event.type == "TIMER" and self.ids:
            self._follow()
            return {"PASS_THROUGH"}
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            if self.ids:   # dragging: follow the mouse in the plane the grab started in
                self.mouse = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
                self.desired = self._mouse_point(event) + self.offsets
                self._follow()
                self.region.tag_redraw()
                return {"RUNNING_MODAL"}
            region, rv3d = _view3d_under(self.window, event)
            previous = self.region
            self.region, self.rv3d = region, rv3d
            self.mouse = (event.mouse_x - region.x, event.mouse_y - region.y) if region else None
            now = time.monotonic()
            if region is None:
                self.over_cloth = False
            elif now - self.hover_at >= self.HOVER_INTERVAL:
                self.hover_at = now
                self.over_cloth = (not _over_toolbar(context, region, event)
                                   and self._hit(context, region, rv3d, event)[1] is not None)
            self._set_cursor(self.over_cloth)
            for r in (previous, region):
                if r is not None:
                    r.tag_redraw()
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS" and not self.ids:
            region, rv3d = _view3d_under(self.window, event)
            if region is None or _over_toolbar(context, region, event):
                return {"PASS_THROUGH"}   # the floating toolbar's buttons win over the cloth below them
            found, hit = self._hit(context, region, rv3d, event)
            if hit is None:
                return {"PASS_THROUGH"}   # not the cloth: select, navigate etc. as usual
            self.region, self.rv3d = region, rv3d
            cloth, vertices = hit
            preview = bpy.data.objects[_session.previews[cloth][1]]
            matrix = np.array(preview.matrix_world)
            local = np.array([preview.data.vertices[v].co[:] for v in vertices])
            world = local @ matrix[:3, :3].T + matrix[:3, 3]
            self.depth = found[0]
            self.offsets = world - np.array(found[0])
            capture = _session.prep.cloths[cloth]
            stiffness = backend.pin_stiffness(self.strength, capture.material) if self.strength > 0 else 0.0
            self.ids, kept = _session.grab(cloth, vertices, world.astype(np.float32), stiffness)
            self.swallow = True
            self.offsets = self.offsets[kept]
            self.cloth, self.grabbed = cloth, [vertices[k] for k in kept]
            self.cap = agrab.GrabCap(capture.triangles, capture.world, self.grabbed, start=_preview_world(cloth))
            self.desired = None
            if self.ids:
                self.window_manager = context.window_manager
                self._timer = context.window_manager.event_timer_add(1.0 / 30.0, window=self.window)
            self._set_cursor(True)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.ids:
            self._let_go()
            self.swallow = True
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and (self.ids or (self.swallow and event.value in {"CLICK", "CLICK_DRAG"})):
            # the drag and click Blender derives from our press: no box select or move of the preview
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE":
            self.swallow = False
        return {"PASS_THROUGH"}


class CLOTHNEXT_OT_aether_live_stop(bpy.types.Operator):
    bl_idname = "clothnext.aether_live_stop"
    bl_label = "Stop Live Simulation"
    bl_description = ("Stop the Live Simulation and keep the cloth where it is (Marvelous style): the draped "
                      "positions are stored as the shape key 'Cloth NeXt Drape', the next Live Simulation or "
                      "Bake continues from them. Clear Drape returns to the rest shape")
    bl_options = {"REGISTER", "UNDO"}

    keep: bpy.props.BoolProperty(name="Keep Drape", default=True,
                                 description="Keep the simulated drape on the Cloth objects")

    @classmethod
    def poll(cls, context):
        return _session is not None

    def execute(self, context):
        stop(keep=self.keep)
        return {"FINISHED"}


class CLOTHNEXT_OT_aether_clear_drape(bpy.types.Operator):
    bl_idname = "clothnext.aether_clear_drape"
    bl_label = "Clear Drape"
    bl_description = ("Remove the kept drape (shape key 'Cloth NeXt Drape') from the Cloth objects, so they "
                      "show and simulate from their rest shape again")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import aether_drape
        return _session is None and any(aether_drape.drape_block(o) is not None for o in context.scene.objects
                                        if getattr(o, "type", "") == "MESH")

    def execute(self, context):
        from . import aether_drape
        cleared = sum(1 for o in list(context.scene.objects)
                      if getattr(o, "type", "") == "MESH" and aether_drape.clear_drape(o))
        self.report({"INFO"}, f"Cleared the drape of {cleared} object(s)")
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_aether_live_start, CLOTHNEXT_OT_aether_live_toggle_pause,
           CLOTHNEXT_OT_aether_live_reset, CLOTHNEXT_OT_aether_live_grab, CLOTHNEXT_OT_aether_live_stop,
           CLOTHNEXT_OT_aether_clear_drape)


def _on_load_pre(*_args):
    stop()


_on_load_pre = getattr(getattr(bpy.app, "handlers", None), "persistent", lambda f: f)(_on_load_pre)


_keymap_items: list = []


def register_handlers() -> None:
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_pre", None)
    if handlers is not None and _on_load_pre not in handlers:
        handlers.append(_on_load_pre)


def shutdown() -> None:
    stop()
    for km, item in _keymap_items:
        try:
            km.keymap_items.remove(item)
        except (ReferenceError, RuntimeError):
            pass
    _keymap_items.clear()
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_pre", None)
    if handlers is not None and _on_load_pre in handlers:
        handlers.remove(_on_load_pre)
