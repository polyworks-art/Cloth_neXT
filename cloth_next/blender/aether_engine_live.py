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

from ..aether_engine import bake as abake, client as aclient, engine as aengine
from . import aether_engine_backend as backend

LIVE_MARK = "cloth_next_aether_live"
TICK_SECONDS = 1.0 / 60.0
PANEL_REDRAW_SECONDS = 0.25          # the status line; the 3D views redraw on every new frame
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
                cloths=[abake.session_cloth(c) for c in request.cloths],
                meshes=[{"vertices": c.rest_world, "triangles": c.triangles} for c in request.colliders])
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

    def reset(self) -> None:
        self.pause()
        self.command("reset")
        self.last_frame_seen = -1

    def stop(self) -> None:
        self._wake(False)
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
        self.drew = False
        writes = self.session.frames.write_count
        if writes == self._last_write:
            return True                      # no new frame since the last tick
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
            self.command("set_inputs", {"pin_remove": {"ids": aclient.i32(ids), "release": "keep"}})

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


class CLOTHNEXT_OT_aether_live_grab(bpy.types.Operator):
    """Pull the cloth with the mouse while the Live Simulation runs (like grabbing in Marvelous)."""
    bl_idname = "clothnext.aether_live_grab"
    bl_label = "Grab Cloth"
    bl_description = ("Drag the cloth of the Live Simulation with the left mouse button. The grabbed point follows "
                      "the mouse in the view plane and is released when you let go. Right-click or Esc ends")
    bl_options = {"REGISTER"}

    strength: bpy.props.FloatProperty(name="Strength", default=1.0, min=0.01, max=10.0,
                                      description="Pull strength (1 = ten times the cloth's membrane stiffness)")

    @classmethod
    def poll(cls, context):
        return _session is not None and context.area is not None and context.area.type == "VIEW_3D"

    # the pinch point of the icon (assets/icons/aether_grab_cursor.png), as a fraction from bottom-left
    HOTSPOT = (0.48, 0.53)
    # the icon fills its square; the system hand cursor fills about 22 of its 32 px box
    CURSOR_FILL = 22.0 / 32.0

    def _draw_cursor(self, context):
        if context.region != self.region or self.mouse is None:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        from . import floating_simulation
        size = _system_cursor_px(context) * self.CURSOR_FILL
        x, y = self.mouse[0] - self.HOTSPOT[0] * size, self.mouse[1] - self.HOTSPOT[1] * size
        gpu.state.blend_set("ALPHA")
        floating_simulation._asset_icon(gpu, batch_for_shader, "aether_grab_cursor", x, y, size)
        gpu.state.blend_set("NONE")

    def invoke(self, context, event):
        if _session is None:
            return {"CANCELLED"}
        self.ids, self.offsets, self.depth = [], None, None
        self.region, self.mouse = context.region, (event.mouse_region_x, event.mouse_region_y)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_cursor, (context,), "WINDOW", "POST_PIXEL")
        context.window.cursor_modal_set("NONE")   # the grab icon is drawn at the mouse instead
        context.area.header_text_set("Grab Cloth: drag with the left mouse button · Right-click or Esc: done")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _ray(self, context, event):
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        hit, location, _normal, polygon, obj, _matrix = context.scene.ray_cast(
            context.evaluated_depsgraph_get(), origin, direction)
        return (location, polygon, obj) if hit else None

    def _mouse_point(self, context, event):
        from bpy_extras import view3d_utils
        return np.array(view3d_utils.region_2d_to_location_3d(
            context.region, context.region_data, (event.mouse_region_x, event.mouse_region_y), self.depth))

    def _finish(self, context):
        if _session is not None:
            _session.release(self.ids)
        self.ids = []
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
            self._handle = None
        context.window.cursor_modal_restore()
        context.area.header_text_set(None)
        context.area.tag_redraw()
        return {"FINISHED"}

    def modal(self, context, event):
        if _session is None or context.area is None:
            if context.area is not None:
                return self._finish(context)
            if getattr(self, "_handle", None) is not None:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
                self._handle = None
            context.window.cursor_modal_restore()
            return {"CANCELLED"}
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            return self._finish(context)
        if event.type == "MOUSEMOVE":
            inside = 0 <= event.mouse_region_x < context.region.width and 0 <= event.mouse_region_y < context.region.height
            self.mouse = (event.mouse_region_x, event.mouse_region_y) if inside else None
            context.window.cursor_modal_set("NONE" if inside else "DEFAULT")
            context.region.tag_redraw()
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            found = self._ray(context, event)
            hit = _session.preview_hit(found[2].original if found else None, found[1] if found else -1)
            if hit is None:
                return {"RUNNING_MODAL"}
            cloth, vertices = hit
            preview = bpy.data.objects[_session.previews[cloth][1]]
            matrix = np.array(preview.matrix_world)
            local = np.array([preview.data.vertices[v].co[:] for v in vertices])
            world = local @ matrix[:3, :3].T + matrix[:3, 3]
            self.depth = found[0]
            self.offsets = world - np.array(found[0])
            stiffness = backend.pin_stiffness(self.strength, _session.prep.cloths[cloth].material)
            self.ids, kept = _session.grab(cloth, vertices, world.astype(np.float32), stiffness)
            self.offsets = self.offsets[kept]
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE" and self.ids:
            point = self._mouse_point(context, event)
            _session.move_grab(self.ids, (point + self.offsets).astype(np.float32))
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.ids:
            _session.release(self.ids)
            self.ids = []
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}


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
           CLOTHNEXT_OT_aether_live_reset, CLOTHNEXT_OT_aether_live_grab, CLOTHNEXT_OT_aether_live_stop)


def _on_load_pre(*_args):
    stop()


_on_load_pre = getattr(getattr(bpy.app, "handlers", None), "persistent", lambda f: f)(_on_load_pre)


_keymap_items: list = []


def register_handlers() -> None:
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_pre", None)
    if handlers is not None and _on_load_pre not in handlers:
        handlers.append(_on_load_pre)
    kc = getattr(getattr(getattr(bpy.context, "window_manager", None), "keyconfigs", None), "addon", None)
    if kc is not None and not _keymap_items:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        _keymap_items.append((km, km.keymap_items.new(CLOTHNEXT_OT_aether_live_grab.bl_idname, "G", "PRESS",
                                                      ctrl=True, shift=True)))


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
