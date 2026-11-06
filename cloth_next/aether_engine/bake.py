# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine bake on a worker thread (pure: no bpy).

Blender prepares an immutable :class:`BakeRequest` on the main thread (rest
geometry, materials, pins, seams and every per-frame input already sampled
from the timeline). The runner owns one ``AETHER Engine.exe`` for the bake,
streams the frames from the shared-memory ring into Cloth NeXt's
``StreamingPc2Writer`` (one PC2 per cloth object) and writes the standard
cache sidecars. Blender attaches the published caches afterwards.

Frame mapping (identical to the PPF bakes): PC2 sample 0 = Blender frame
``frame_start`` = the rest state; PC2 sample n = Blender frame
``frame_start + n`` = engine frame index n (the state after n steps). The
inputs of engine step n-1 are the poses at Blender frame ``frame_start + n``
(the engine reaches them at the end of the step).

Recovery: the engine checkpoints every ``checkpoint_interval`` frames into an
AETHER-only folder; a ``recovery.json`` next to it records everything a
resume must match (engine build, GPU, preset, fps, range, scene and input
digests, partial PC2 files). A resume restarts the engine from the newest
valid checkpoint at or below the frames already in the partial PC2, verifies
the recomputed frames bit for bit against the cache, then appends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable, Sequence

import numpy as np

from ..bake import cache_metadata, pc2
from . import client, engine as engine_mod
from .materials import AetherMaterial

RECOVERY_FORMAT = "cloth-next-aether-recovery"
RECOVERY_VERSION = 1
QUALITIES = ("normal", "animation", "fitting")
INPUT_LOOKAHEAD = 12  # frames of inputs queued ahead of the engine


class AetherBakeError(RuntimeError):
    def __init__(self, message: str, code: str = "CNX-E160", details: str = ""):
        super().__init__(message)
        self.code = code
        self.details = details


# ---------------------------------------------------------------- request
@dataclass(frozen=True)
class ClothInput:
    uuid: str
    object_name: str
    rest_world: np.ndarray          # (V, 3) float32, Blender world space (Z up)
    rest_local: np.ndarray          # (V, 3) float32, object local (PC2 sample 0)
    triangles: np.ndarray           # (T*3,) int32
    world_matrix: np.ndarray        # (4, 4) float64 at the bake start
    material: AetherMaterial
    registered_pins: np.ndarray     # int32, hard static pins
    pc2_path: Path
    material_meta: dict             # cache sidecar seed (fingerprints, identities, details)
    zones: tuple[AetherMaterial, ...] = ()       # Material Zones (engine API 1.2), zone k at [k - 1]
    triangle_zones: np.ndarray | None = None     # (T,) int32, 0 = base material
    start_world: np.ndarray | None = None        # (V, 3) float32: kept drape the simulation starts from
    start_local: np.ndarray | None = None        # the same in object space (PC2 sample 0)
    shrink_uv: np.ndarray | None = None          # (T*6,) float32 UV of each triangle's corners (engine API 1.5)


@dataclass(frozen=True)
class ColliderInput:
    name: str
    triangles: np.ndarray           # (T*3,) int32
    rest_world: np.ndarray          # (V, 3) float32 at frame_start
    frames: np.ndarray | None       # (F, V, 3) float32 at frame_start+1 .. frame_end; None: static
    surface_offset: float = 0.0     # m, the collider's Surface Offset (engine API 1.5)


@dataclass(frozen=True)
class PinTrack:
    """Runtime pins of one cloth: hard (stiffness 0) or compliant (N/m)."""
    cloth: int
    vertices: np.ndarray            # (P,) int32 local vertex indices
    stiffness: float
    targets: np.ndarray | None      # (F, P, 3) world targets at frame_start+1..end; None: hold


@dataclass(frozen=True)
class SeamInput:
    cloth_a: int
    cloth_b: int
    chain_a: tuple[int, ...]
    chain_b: tuple[int, ...]
    stiffness: float
    closure_frames: int             # progress 0 -> 1 over these frames
    closing: bool = True            # False: hold the separation at creation (attachments)


@dataclass(frozen=True)
class BakeRequest:
    cloths: tuple[ClothInput, ...]
    colliders: tuple[ColliderInput, ...]
    pins: tuple[PinTrack, ...]
    seams: tuple[SeamInput, ...]
    quality: str
    fps: float
    gravity: tuple[float, float, float]
    self_collision: bool
    frame_start: int
    frame_end: int
    checkpoint_dir: Path
    recovery_path: Path
    checkpoint_interval: int = 24
    settings_fingerprint: str = ""
    geometry_fingerprint: str = ""
    cloth_next_version: str = ""
    blender_version: str = ""
    # Milestone 5.2, experimental (CLOTH_NEXT_AETHER_EXPERIMENTAL_RECOVERY=1, off by default): the
    # engine analyses every frame for intersecting cloth and runs its E4 recovery when it finds some.
    experimental_recovery: bool = False
    # Production: cloth that starts inside a collider or inside other cloth (a garment modelled for
    # another pose or body) is pulled out at the start of the bake by the engine's recovery; it stops on
    # its own once nothing intersects. Without it the cloth stayed caught and tore when the body moved
    # (SW04_reworked: 1212 body intersections at frame 1, 12x stretch); with it 0 from frame 26 on.
    # Clean scenes pay one analysis per frame of the start. CLOTH_NEXT_AETHER_START_RECOVERY=0: off.
    start_recovery: bool = False
    # Engine API 1.4: the wind input of every step (one entry: the same for all steps; empty: no wind).
    # Sent every frame, so a resumed bake (a new engine process) blows the same wind.
    wind: tuple = ()

    @property
    def steps(self) -> int:
        return self.frame_end - self.frame_start

    @property
    def output_count(self) -> int:
        return self.frame_end - self.frame_start + 1


def _digest_arrays(h, *arrays) -> None:
    for a in arrays:
        if a is None:
            h.update(b"none")
            continue
        a = np.ascontiguousarray(a)
        h.update(str(a.dtype).encode())
        h.update(str(a.shape).encode())
        h.update(a.tobytes())


def scene_digest(request: BakeRequest) -> str:
    """Scene identity: geometry, materials, pins, seams, colliders' rest, settings."""
    h = hashlib.sha256()
    h.update(json.dumps({"quality": request.quality, "fps": request.fps, "gravity": request.gravity,
                         **({"experimental_recovery": True} if request.experimental_recovery else {}),
                         **({"start_recovery": START_RECOVERY_FRAMES} if request.start_recovery else {}),
                         "self_collision": request.self_collision, "range": [request.frame_start,
                                                                              request.frame_end]},
                        sort_keys=True).encode())
    for c in request.cloths:
        h.update(c.uuid.encode())
        _digest_arrays(h, c.rest_world, c.triangles, c.registered_pins)
        h.update(json.dumps(c.material.as_protocol(), sort_keys=True).encode())
        if c.triangle_zones is not None:
            h.update(json.dumps([z.as_protocol() for z in c.zones], sort_keys=True).encode())
            _digest_arrays(h, c.triangle_zones)
        if c.start_world is not None:
            h.update(b"drape")
            _digest_arrays(h, c.start_world)
        factors = shrink_factors(c)   # engine API 1.5 (only when set: older bakes keep their digest)
        if factors is not None:
            h.update(b"shrink")
            _digest_arrays(h, factors)
            if c.shrink_uv is not None and needs_grain(c):
                _digest_arrays(h, c.shrink_uv)
        if surface_offset(c) > 0.0:
            h.update(f"offset {surface_offset(c)!r}".encode())
    for c in request.colliders:
        _digest_arrays(h, c.triangles, c.rest_world)
        if c.surface_offset > 0.0:
            h.update(f"offset {c.surface_offset!r}".encode())
    for s in request.seams:
        h.update(json.dumps([s.cloth_a, s.cloth_b, s.chain_a, s.chain_b, s.stiffness, s.closure_frames,
                             s.closing]).encode())
    for p in request.pins:
        h.update(json.dumps([p.cloth, p.stiffness]).encode())
        _digest_arrays(h, p.vertices)
    return h.hexdigest()


def input_digest(request: BakeRequest) -> str:
    """The saved per-frame input sequence (animated colliders and pin targets)."""
    h = hashlib.sha256()
    for c in request.colliders:
        _digest_arrays(h, c.frames)
    for p in request.pins:
        _digest_arrays(h, p.targets)
    return h.hexdigest()


# ---------------------------------------------------------------- protocol messages
START_RECOVERY_FRAMES = 48   # frames from the start in which the recovery runs


def frame_inputs(request: BakeRequest, step: int) -> dict:
    """Inputs for engine step ``step`` (0-based): poses at Blender frame start+step+1."""
    inp: dict = {"frame": step}
    # The engine refuses a checkpoint while a recovery runs, which failed the bake (CNX-E169) when the
    # cloth still intersected at the first checkpoint: the frame that ends at one runs without it.
    interval = request.checkpoint_interval
    if interval > 0 and ((step + 1) % interval == 0 or step + 1 == request.steps):
        pass
    elif request.experimental_recovery:
        inp["untangle"] = {"frames": 1}   # analysis this frame; recovery only where intersections exist
    elif request.start_recovery and step < START_RECOVERY_FRAMES:
        # analysis every frame of the start, recovery only where intersections exist (one request for
        # all frames stopped at 18 left: the engine gives up after 12 frames without a new minimum)
        inp["untangle"] = {"frames": 1}
    if step == 0:
        adds = []
        for p in request.pins:
            for v in p.vertices.tolist():
                adds.append({"cloth": p.cloth, "vertex": int(v), "stiffness": float(p.stiffness)})
        if adds:
            inp["pin_add"] = adds
        if request.seams:
            # from a kept drape the seams are already sewn
            inp["seam_add"] = [{"cloth_a": s.cloth_a, "cloth_b": s.cloth_b, "chain_a": list(s.chain_a),
                                "chain_b": list(s.chain_b), "stiffness": s.stiffness,
                                "progress": 1.0 if s.closing and draped(request) else 0.0}
                               for s in request.seams]
    if request.wind:
        inp["wind"] = request.wind[min(step, len(request.wind) - 1)]
    closing = [{"id": i + 1, "progress": min(1.0, (step + 1) / max(1, s.closure_frames))}
               for i, s in enumerate(request.seams) if s.closing and not draped(request)]
    if closing:
        inp["seam_progress"] = closing
    # runtime pin ids are 1.. in pin_add order (engine ids are deterministic)
    ids, targets = [], []
    next_id = 1
    for p in request.pins:
        n = len(p.vertices)
        if p.targets is not None:
            ids.extend(range(next_id, next_id + n))
            targets.append(p.targets[step])
        next_id += n
    if ids:
        inp["pin_targets"] = {"ids": client.i32(ids), "targets": client.f32(np.concatenate(targets))}
    meshes = [{"index": i, "vertices": client.f32(c.frames[step])}
              for i, c in enumerate(request.colliders) if c.frames is not None]
    if meshes:
        inp["mesh_colliders"] = meshes
    return inp


def draped(request: BakeRequest) -> bool:
    return any(c.start_world is not None for c in request.cloths)


def start_positions(request: BakeRequest) -> np.ndarray | None:
    """Concatenated start state (world) when some cloth starts from a kept drape, else None."""
    if not draped(request):
        return None
    return np.concatenate([c.start_world if c.start_world is not None else c.rest_world
                           for c in request.cloths]).astype(np.float32)


def shrink_factors(c: ClothInput) -> np.ndarray | None:
    """(T*2,) float32 (weft, warp) rest-size factors per triangle (engine API 1.5); None: no shrink.

    Material Zones carry their own Shrink; triangles outside every zone use the cloth's."""
    mats = (c.material,) + tuple(c.zones)
    if not any(m.shrinks for m in mats):
        return None
    table = np.array([[m.shrink_weft, m.shrink_warp] for m in mats], np.float32)
    count = len(c.triangles) // 3
    index = (np.zeros(count, np.int64) if c.triangle_zones is None
             else np.asarray(c.triangle_zones, np.int64))
    return np.ascontiguousarray(table[index].reshape(-1))


def needs_grain(c: ClothInput) -> bool:
    """Some triangle shrinks differently along warp and weft (the UV map gives the grain)."""
    return any(m.shrink_weft != m.shrink_warp for m in (c.material,) + tuple(c.zones))


def surface_offset(c: ClothInput) -> float:
    """The cloth's Surface Offset (engine API 1.5: one per cloth; with zones the largest)."""
    return max(m.surface_offset for m in (c.material,) + tuple(c.zones))


def session_cloth(c: ClothInput) -> dict:
    """One cloth of a create_session request (bake and Live)."""
    out = {"positions": c.rest_world, "triangles": c.triangles, "material": c.material.as_protocol(),
           "pins": c.registered_pins if len(c.registered_pins) else None}
    if c.triangle_zones is not None:
        out["zones"] = [z.as_protocol() for z in c.zones]
        out["triangle_zones"] = c.triangle_zones
    factors = shrink_factors(c)
    if factors is not None:
        out["shrink"] = factors
        if c.shrink_uv is not None and needs_grain(c):
            out["shrink_uv"] = np.ascontiguousarray(c.shrink_uv, np.float32).reshape(-1)
    offset = surface_offset(c)
    if offset > 0.0:
        out["surface_offset"] = float(offset)
    return out


def session_collider(c: ColliderInput) -> dict:
    """One mesh collider of a create_session request."""
    out = {"vertices": c.rest_world, "triangles": c.triangles}
    if c.surface_offset > 0.0:
        out["surface_offset"] = float(c.surface_offset)
    return out


# ---------------------------------------------------------------- recovery record
def _atomic_json(path: Path, payload: dict) -> None:
    cache_metadata.write_atomic(path, payload)


def recovery_record(request: BakeRequest, hello: dict, session: client.Session) -> dict:
    return {
        "format": RECOVERY_FORMAT, "format_version": RECOVERY_VERSION, "engine": "AETHER Engine",
        "engine_version": hello.get("engine_version"), "build_id": hello.get("build_id"),
        "device_uuid": hello.get("device", {}).get("uuid"), "device_name": hello.get("device", {}).get("name"),
        "quality": request.quality, "fps": request.fps,
        "frame_start": request.frame_start, "frame_end": request.frame_end,
        "scene_digest": scene_digest(request), "input_digest": input_digest(request),
        "engine_scene_hash": session.scene_hash, "settings_fingerprint": request.settings_fingerprint,
        "geometry_fingerprint": request.geometry_fingerprint,
        "checkpoint_dir": str(request.checkpoint_dir),
        "objects": [{"uuid": c.uuid, "name": c.object_name, "pc2": str(c.pc2_path),
                     "partial": str(partial_path(c.pc2_path)), "vertex_count": int(len(c.rest_local))}
                    for c in request.cloths],
        "updated": time.time(),
    }


def partial_path(final: Path) -> Path:
    """Deterministic private PC2 temp of an AETHER bake (resumable)."""
    return final.with_name(f".{final.name}.aether-partial.tmp")


def load_recovery(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("format") != RECOVERY_FORMAT \
            or value.get("engine") != "AETHER Engine":
        return None
    return value


def check_recovery(record: dict | None, request: BakeRequest) -> str:
    """'' when the record belongs to exactly this bake; else the reason."""
    if record is None:
        return "No AETHER recovery data exists for this bake"
    if record.get("format_version") != RECOVERY_VERSION:
        return "The recovery data was written by another Cloth NeXt version"
    checks = (("quality", request.quality, "the quality preset changed (presets are never changed silently)"),
              ("fps", request.fps, "the scene frame rate changed"),
              ("frame_start", request.frame_start, "the Bake Start changed"),
              ("frame_end", request.frame_end, "the Bake End changed"),
              ("scene_digest", scene_digest(request), "the scene changed (geometry, materials, pins, seams or colliders)"),
              ("input_digest", input_digest(request), "the animated inputs changed (collider or pin animation)"))
    for key, value, reason in checks:
        if record.get(key) != value:
            return f"The interrupted bake cannot be resumed: {reason}"
    names = [o.get("uuid") for o in record.get("objects", ())]
    if names != [c.uuid for c in request.cloths]:
        return "The interrupted bake cannot be resumed: the set of cloth objects changed"
    return ""


def discard_recovery(request: BakeRequest) -> None:
    """Remove this bake's AETHER recovery data and partial caches (owned files only)."""
    for c in request.cloths:
        p = partial_path(c.pc2_path)
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    d = Path(request.checkpoint_dir)
    if d.is_dir():
        for f in d.iterdir():
            if f.name.startswith("aether_") and f.suffix in {".aeckpt", ".json", ".tmp"}:
                f.unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        Path(request.recovery_path).unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- runner
@dataclass
class Progress:
    phase: str = "Starting the AETHER Engine"
    frames_done: int = 0          # PC2 samples written or verified (without sample 0)
    frames_total: int = 0
    resumed_from: int | None = None
    verified: int = 0
    engine_ms: float = 0.0
    checkpoint_frame: int | None = None
    step_ms: list = field(default_factory=list)   # (engine frame, ms, contacts) per simulated frame
    recovery: list = field(default_factory=list)  # (engine frame, E4 report) with experimental recovery
    started: float = field(default_factory=time.monotonic)


class BakeRunner:
    """Worker thread: one AETHER bake. Poll ``done``; ``cancel()`` is safe anytime."""

    def __init__(self, request: BakeRequest, executable: Path, *, resume: bool = False,
                 log_path: Path | None = None, env: dict | None = None, testing: bool = False,
                 on_process: Callable[[client.EngineProcess], None] | None = None):
        self.request = request
        self.executable = Path(executable)
        self.resume = resume
        self.log_path = log_path
        self.env = env
        self.testing = testing
        self.on_process = on_process
        self.progress = Progress(frames_total=request.steps)
        self.error: AetherBakeError | None = None
        self.cancelled = False
        self.headers: dict[str, pc2.Pc2Header] = {}
        self.hello: dict = {}
        self.engine: client.EngineProcess | None = None
        self.timings: dict[str, float] = {}
        self.warnings: list[str] = []
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="AETHER bake", daemon=True)
        self._thread.start()

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    # -------------------------------------------------- writers
    def _open_writers(self, resume_frames: int | None) -> list[pc2.StreamingPc2Writer]:
        writers = []
        for c in self.request.cloths:
            temp = partial_path(c.pc2_path)
            if not self.resume and temp.exists():
                temp.unlink()
            c.pc2_path.parent.mkdir(parents=True, exist_ok=True)
            w = pc2.StreamingPc2Writer(c.pc2_path, vertex_count=len(c.rest_local),
                                       frame_count=self.request.output_count, start_frame=0.0,
                                       sample_rate=1.0, resume_path=temp)
            if w.frames_written == 0:
                w.write_frame(c.start_local if c.start_local is not None else c.rest_local)
            writers.append(w)
        return writers

    def _to_local(self, c: ClothInput, world: np.ndarray) -> np.ndarray:
        inv = np.linalg.inv(c.world_matrix)
        out = world.astype(np.float64) @ inv[:3, :3].T + inv[:3, 3]
        return out.astype(np.float32)

    # -------------------------------------------------- thread body
    def _run(self) -> None:
        req = self.request
        writers: list[pc2.StreamingPc2Writer] = []
        e = None
        try:
            t0 = time.monotonic()
            e = self.engine = client.EngineProcess.start(self.executable, log_path=self.log_path, env=self.env,
                                                         testing=self.testing)
            if self.on_process:
                self.on_process(e)
            self.hello = e.hello()
            self.progress.phase = "Building the AETHER scene"
            session = e.create_session(mode="bake", quality=req.quality, fps=req.fps, gravity=req.gravity,
                                       self_collision=req.self_collision,
                                       cloths=[session_cloth(c) for c in req.cloths],
                                       meshes=[session_collider(c) for c in req.colliders],
                                       start_positions=start_positions(req))
            self.timings["engine_startup_s"] = time.monotonic() - t0
            if session.vertex_count != sum(len(c.rest_world) for c in req.cloths):
                raise AetherBakeError("The AETHER scene does not match the exported cloth", "CNX-E176")
            req.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            writers = self._open_writers(None)
            on_disk = min(w.frames_written for w in writers) - 1   # engine frames already in the cache
            start_step = 0
            if self.resume:
                # The engine runs ahead of the PC2 by up to the ring size, so a
                # checkpoint can be newer than the cache on disk. Only checkpoints
                # the cache already covers are usable (no gap, no duplicate);
                # without one, the bake restarts at its first frame and verifies
                # the cached frames instead of rewriting them.
                usable = _drop_checkpoints_after(req.checkpoint_dir, on_disk)
                if usable:
                    r = e.request("bake_resume", {"end_frame": req.steps,
                                                  "checkpoint_dir": str(req.checkpoint_dir),
                                                  "checkpoint_interval": req.checkpoint_interval,
                                                  "checkpoint_keep": 2})
                    start_step = int(r["start_frame"])
                else:
                    e.request("bake_start", {"end_frame": req.steps, "checkpoint_dir": str(req.checkpoint_dir),
                                             "checkpoint_interval": req.checkpoint_interval,
                                             "checkpoint_keep": 2})
                if start_step > on_disk:
                    raise AetherBakeError(
                        f"The AETHER checkpoint (frame {start_step}) is newer than the cache on disk "
                        f"({on_disk} frames); the bake cannot be resumed without a gap", "CNX-E129")
                self.progress.resumed_from = start_step
            else:
                for f in req.checkpoint_dir.glob("aether_*"):
                    f.unlink(missing_ok=True)
                e.request("bake_start", {"end_frame": req.steps, "checkpoint_dir": str(req.checkpoint_dir),
                                         "checkpoint_interval": req.checkpoint_interval, "checkpoint_keep": 2})
            _atomic_json(req.recovery_path, recovery_record(req, self.hello, session))
            self.progress.phase = "Simulating with the AETHER Engine"
            sent = start_step
            for step in range(start_step, req.steps):
                if self._cancel.is_set():
                    break
                while sent < min(req.steps, step + INPUT_LOOKAHEAD):
                    e.request("set_inputs", frame_inputs(req, sent), timeout=60)
                    sent += 1
                got = None
                while got is None:
                    if self._cancel.is_set():
                        break
                    got = session.frames.next(timeout=0.25, alive=lambda: e.alive)
                    self._drain_events()
                if got is None:
                    break
                frame, world, _t = got
                if frame != step + 1:
                    raise AetherBakeError(f"The AETHER Engine delivered frame {frame}, expected {step + 1}",
                                          "CNX-E174")
                offset = 0
                verify = frame <= on_disk
                for c, w in zip(req.cloths, writers):
                    n = len(c.rest_local)
                    local = self._to_local(c, world[offset:offset + n])
                    offset += n
                    if verify:
                        expect = _read_pc2_sample(w, frame)
                        if not np.array_equal(expect.view("<u4"), local.view("<u4")):
                            raise AetherBakeError(
                                f"The resumed frame {req.frame_start + frame} differs from the cached frame; "
                                "the bake is not bit-identical and was stopped", "CNX-E129")
                    else:
                        if not np.isfinite(local).all():
                            raise AetherBakeError(f"Non-finite positions at frame {req.frame_start + frame}",
                                                  "CNX-E165")
                        w.write_frame(local)
                if verify:
                    self.progress.verified += 1
                self.progress.frames_done = frame
            if self._cancel.is_set() and self.progress.frames_done < req.steps:
                self.cancelled = True
                try:
                    e.request("cancel", timeout=30)
                except (client.EngineError, client.EngineGone, TimeoutError):
                    pass
                for w in writers:
                    w.preserve()  # resumable; Start Fresh discards it
                writers = []
                return
            self._drain_events()
            self.progress.phase = "Publishing the playback caches"
            t1 = time.monotonic()
            for c, w in zip(req.cloths, writers):
                self.headers[c.uuid] = w.finalize()
                self._write_sidecar(c)
            writers = []
            self.timings["publish_s"] = time.monotonic() - t1
            self.timings["total_s"] = time.monotonic() - t0
            try:
                e.shutdown(timeout=30)
            except Exception:  # noqa: BLE001 - the caches are already published
                pass
            discard_recovery(req)
        except client.EngineGone as exc:
            code, message = engine_mod.exit_reason(exc.exit_code)
            last = exc.last_error or {}
            if last.get("name"):
                code, message = engine_mod.map_error(last["name"], str(last.get("message", "")))
            self.error = AetherBakeError(message + ". The bake can be resumed from its last checkpoint",
                                         code, self._stderr_tail())
        except client.EngineError as exc:
            code, message = engine_mod.map_error(exc.name, str(exc).split(": ", 1)[-1])
            self.error = AetherBakeError(message, code)
        except AetherBakeError as exc:
            self.error = exc
        except pc2.Pc2Error as exc:
            self.error = AetherBakeError(f"The playback cache could not be written: {exc}", "CNX-E181")
        except OSError as exc:
            self.error = AetherBakeError(f"The cache folder cannot be written: {exc}", "CNX-E121")
        except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
            import traceback
            self.error = AetherBakeError(f"The AETHER bake failed unexpectedly: {exc}", "CNX-E199",
                                         traceback.format_exc())
        finally:
            for w in writers:   # failure: keep the frames for a resume
                try:
                    w.preserve()
                except Exception:  # noqa: BLE001
                    pass
            if e is not None and e.alive:
                try:
                    e.shutdown(timeout=15)
                except Exception:  # noqa: BLE001
                    e.kill()
            self.progress.phase = "Finished"

    def _drain_events(self) -> None:
        e = self.engine
        while e is not None:
            try:
                ev = e.events.get_nowait()
            except Exception:  # noqa: BLE001 - queue.Empty
                return
            name = ev.get("event")
            if name == "frame" and "step_ms" in ev:
                self.progress.engine_ms += float(ev["step_ms"])
                self.progress.step_ms.append((int(ev.get("frame", 0)), float(ev["step_ms"]),
                                              int(ev.get("contacts", 0))))
                if "untangle" in ev:
                    self.progress.recovery.append((int(ev.get("frame", 0)), dict(ev["untangle"])))
            elif name == "checkpoint":
                self.progress.checkpoint_frame = int(ev.get("frame", 0))
            elif name == "error":
                err = ev.get("error", {})
                text = str(err.get("message", ""))
                if err.get("fatal"):
                    continue  # the process ends; EngineGone reports it with the exit code
                if "checkpoint write failed" in text:
                    self.warnings.append(text)  # the previous checkpoint stays valid
                    continue
                code, message = engine_mod.map_error(err.get("name", ""), text)
                raise AetherBakeError(message, code)
            elif name == "process_ended":
                e.events.put(ev)
                return

    def _stderr_tail(self) -> str:
        if self.log_path is None:
            return ""
        try:
            return Path(self.log_path).read_text("utf-8", "replace")[-2000:]
        except OSError:
            return ""

    def _write_sidecar(self, c: ClothInput) -> None:
        meta = dict(c.material_meta)
        identities = dict(meta.get("identities", {}))
        identities["solver"] = {
            "backend": "AETHER_ENGINE", "engine": "AETHER Engine",
            "engine_version": self.hello.get("engine_version", "unknown"),
            "build_id": self.hello.get("build_id", "unknown"),
            "build_commit": self.hello.get("build_commit", "unknown"),
            "device": self.hello.get("device", {}).get("name", "unknown"),
            "device_uuid": self.hello.get("device", {}).get("uuid", "unknown"),
            "quality": self.request.quality, "fps": self.request.fps,
            "material_mapping_version": meta.get("details", {}).get("aether", {}).get("mapping_version", 0),
        }
        partial = cache_metadata.partial_metadata(
            cache_path=c.pc2_path, fingerprints=meta["fingerprints"], identities=identities,
            expected=meta["expected"], details=meta["details"])
        complete = cache_metadata.completed_metadata(partial, cache_path=c.pc2_path, timings=dict(self.timings))
        cache_metadata.write_atomic(cache_metadata.sidecar_path(c.pc2_path), complete)


def _drop_checkpoints_after(directory: Path, frame: int) -> bool:
    """Uncommit (manifest first) AETHER checkpoints newer than ``frame``; True if one remains."""
    remaining = False
    for manifest in sorted(Path(directory).glob("aether_*.json")):
        try:
            value = int(manifest.stem.split("_", 1)[1])
        except ValueError:
            continue
        if value > frame:
            manifest.unlink(missing_ok=True)
            manifest.with_suffix(".aeckpt").unlink(missing_ok=True)
        else:
            remaining = True
    return remaining


def _read_pc2_sample(writer: pc2.StreamingPc2Writer, sample: int) -> np.ndarray:
    path = writer.expose_live()
    n = writer.header.vertex_count
    with open(path, "rb") as f:
        f.seek(pc2.PC2_HEADER_SIZE + sample * n * 12)
        return np.frombuffer(f.read(n * 12), dtype="<f4").reshape(-1, 3)


def settings_fingerprint(base_settings: str, quality: str) -> str:
    """AETHER settings fingerprint: the scene's settings plus engine, preset and mapping.

    Derived from the PPF settings fingerprint without changing it, so GAIA
    caches keep their fingerprints and an AETHER cache is stale as soon as the
    preset or the material mapping version changes."""
    from .materials import MAPPING_VERSION
    return hashlib.sha256(f"AETHER_ENGINE\0{quality}\0{MAPPING_VERSION}\0{base_settings}".encode()).hexdigest()


def cache_meta(*, settings_fp: str, geometry_fp: str, combined_fp: str, topology: str, object_identity: dict,
               scene_identity: dict, vertex_count: int, frame_count: int, details: dict,
               cloth_next_version: str, blender_version: str) -> dict:
    """Sidecar seed of one AETHER cloth cache (same schema as every Cloth NeXt cache)."""
    return {
        "fingerprints": {"settings": settings_fp, "geometry": geometry_fp, "combined": combined_fp,
                         "topology": topology, "object": cache_metadata.deterministic_hash(object_identity),
                         "scene": cache_metadata.deterministic_hash(scene_identity)},
        "identities": {"cloth_next_version": cloth_next_version, "blender_version": blender_version,
                       "object": object_identity, "solver": {"backend": "AETHER_ENGINE"}},
        "expected": {"vertex_count": int(vertex_count), "frame_count": int(frame_count),
                     "start_frame": 0.0, "sample_rate": 1.0},
        "details": details,
    }
