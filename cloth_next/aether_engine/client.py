# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# Vendored unchanged from the AETHER Engine repository
# (client/aether_engine_client.py at d74cb33): the reference implementation
# of the AETHER Engine.exe host protocol. Do not edit here; update it there.
"""AETHER Engine host client (Milestone 4.6).

The reference implementation of the protocol between Cloth NeXt and
``AETHER Engine.exe``; the future Blender adapter uses this module (or a copy)
unchanged. Standard library plus numpy only (both ship with Blender).

    engine = EngineProcess.start(exe_path)
    info = engine.hello()
    session = engine.create_session(mode="bake", quality="fitting", fps=24, cloths=[...])
    ...
    engine.shutdown()

Control: framed messages over the child's stdin/stdout
(u32 'AEH1' | u32 json_bytes | u64 blob_bytes | JSON | blob). Positions: a
named shared-memory mapping, read with ``FrameReader``. See
docs/MILESTONE4_6_INTEGRATION_FOUNDATIONS.md.
"""

from __future__ import annotations

import ctypes
import json
import mmap
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

PROTOCOL_VERSION = 1
API_VERSION = (1, 6)   # 1.4: wind; 1.5: shrink (warp / weft), surface offset; 1.6: plasticity, flat bending rest, pressure
FRAME_MAGIC = 0x31484541  # "AEH1"
SHM_MAGIC = b"AETHSHM1"
SHM_HEADER_BYTES = 4096
SLOT_HEADER = struct.Struct("<qqQQd24x")  # seq, frame, checksum, publish_index, publish_time
HEADER = struct.Struct("<8sIIIIQII")      # magic, version, header_bytes, slots, vertices, slot_bytes, mode, pad
OFF_WRITE, OFF_READ, OFF_SESSION, OFF_ALIVE = 40, 48, 56, 64

EXIT_CODES = {0: "ok", 1: "usage", 2: "protocol error", 3: "device lost",
              4: "fatal engine error", 5: "owner gone"}


class EngineError(RuntimeError):
    """An error reply or error event from the engine."""

    def __init__(self, error: Mapping[str, Any]):
        self.code = int(error.get("code", -1))
        self.name = str(error.get("name", "AE_UNKNOWN_STATUS"))
        self.fatal = bool(error.get("fatal", False))
        super().__init__(f"{self.name}: {error.get('message', '')}")


class EngineGone(RuntimeError):
    """The engine process ended (crash, device loss, kill)."""

    def __init__(self, exit_code: int | None, last_error: Mapping[str, Any] | None):
        self.exit_code = exit_code
        self.last_error = last_error
        reason = EXIT_CODES.get(exit_code, "terminated") if exit_code is not None else "unknown"
        super().__init__(f"AETHER Engine process ended (exit {exit_code}: {reason})"
                         + (f": {last_error.get('message')}" if last_error else ""))


class Blob:
    """Marks an array that travels in the binary part of a message."""

    def __init__(self, array, dtype):
        self.array = np.ascontiguousarray(array, dtype=dtype).reshape(-1)


def f32(a) -> Blob:
    return Blob(a, "<f4")


def i32(a) -> Blob:
    return Blob(a, "<i4")


def _encode(obj: Mapping[str, Any]) -> tuple[bytes, bytes]:
    parts: list[bytes] = []
    size = 0

    def walk(v):
        nonlocal size
        if isinstance(v, Blob):
            raw = v.array.tobytes()
            ref = {"$blob": size, "count": int(v.array.size)}
            parts.append(raw)
            size += len(raw)
            return ref
        if isinstance(v, Mapping):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        if isinstance(v, np.generic):
            return v.item()
        return v

    js = json.dumps(walk(obj), separators=(",", ":")).encode("utf-8")
    return js, b"".join(parts)


def blob_array(blob: bytes, ref: Mapping[str, Any], dtype="<f4") -> np.ndarray:
    off, count = int(ref["$blob"]), int(ref["count"])
    return np.frombuffer(blob, dtype=dtype, count=count, offset=off)


# ---------------------------------------------------------------- Windows job object
class _Job:
    """Kill-on-close job object: the engine never outlives this client process,
    even when the client crashes (the engine also watches its owner pid)."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class IOC(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(ctypes.c_void_p(h), 9, ctypes.byref(info), ctypes.sizeof(info))
        self.handle = h

    def assign(self, pid: int) -> None:
        if not self.handle:
            return
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        ph = k32.OpenProcess(0x1F0FFF, False, pid)
        if ph:
            k32.AssignProcessToJobObject(ctypes.c_void_p(self.handle), ctypes.c_void_p(ph))
            k32.CloseHandle(ctypes.c_void_p(ph))


# ---------------------------------------------------------------- shared memory frames
class FrameReader:
    """Reads simulated positions from the engine's shared memory.

    Interactive sessions: ``latest()`` returns the newest complete frame without
    ever blocking the engine. Bake sessions: ``next()`` returns every frame in
    order and frees its ring slot (the engine waits while the ring is full).
    """

    def __init__(self, name: str, size: int):
        self._mm = mmap.mmap(-1, size, tagname=name, access=mmap.ACCESS_WRITE)
        magic, version, hb, slots, verts, slot_bytes, mode, _ = HEADER.unpack_from(self._mm, 0)
        if magic != SHM_MAGIC or version != 1 or hb != SHM_HEADER_BYTES:
            raise RuntimeError(f"{name} is not an AETHER Engine frame buffer")
        self.slots, self.vertices, self.slot_bytes, self.mode = slots, verts, slot_bytes, mode
        self.retries = 0

    def close(self) -> None:
        self._mm.close()

    def _i64(self, off: int) -> int:
        return struct.unpack_from("<q", self._mm, off)[0]

    @property
    def write_count(self) -> int:
        return self._i64(OFF_WRITE)

    @property
    def read_count(self) -> int:
        return self._i64(OFF_READ)

    @property
    def heartbeat(self) -> int:
        return self._i64(OFF_ALIVE)

    def _copy_slot(self, slot: int, expect_index: int | None):
        """Seqlock read of one slot; None if it changed while copied."""
        hoff = 128 + slot * 64
        seq1 = struct.unpack_from("<q", self._mm, hoff)[0]
        if seq1 & 1:
            return None
        start = SHM_HEADER_BYTES + slot * self.slot_bytes
        data = np.frombuffer(self._mm, dtype="<f4", count=self.vertices * 3, offset=start).copy()
        seq, frame, checksum, index, t = SLOT_HEADER.unpack_from(self._mm, hoff)
        if seq != seq1 or (expect_index is not None and index != expect_index):
            return None
        if int(data.view("<u4").sum(dtype=np.uint64)) != checksum:
            raise RuntimeError(f"frame {frame}: shared-memory checksum mismatch")
        return frame, data.reshape(-1, 3), t

    def latest(self):
        """(frame, positions[V,3], publish_time) of the newest frame, or None."""
        for _ in range(100):
            w = self.write_count
            if w == 0:
                return None
            got = self._copy_slot((w - 1) % self.slots, w)
            if got is not None:
                return got
            self.retries += 1
        raise RuntimeError("could not read a stable frame")

    def next(self, timeout: float | None = None, alive: Callable[[], bool] | None = None):
        """Bake: the next frame in order (blocks until published), then frees its slot."""
        r = self.read_count
        deadline = None if timeout is None else time.perf_counter() + timeout
        spins = 0
        while self.write_count <= r:
            if alive is not None and not alive():
                raise EngineGone(None, None)
            if deadline is not None and time.perf_counter() > deadline:
                return None
            spins += 1
            time.sleep(0 if spins < 50 else 0.001)
        got = self._copy_slot(r % self.slots, r + 1)
        if got is None:
            raise RuntimeError("bake ring slot changed while it was read (protocol violation)")
        struct.pack_into("<q", self._mm, OFF_READ, r + 1)
        return got


# ---------------------------------------------------------------- engine process
@dataclass
class Session:
    vertex_count: int
    cloth_offsets: list[int]
    shm_name: str
    shm_bytes: int
    slot_count: int
    scene_hash: str
    mode: str
    quality: str
    fps: float
    frames: FrameReader = field(repr=False, default=None)

    def cloth_slice(self, positions: np.ndarray, cloth: int) -> np.ndarray:
        return positions[self.cloth_offsets[cloth]:self.cloth_offsets[cloth + 1]]


class EngineProcess:
    """One owned ``AETHER Engine.exe`` process (one session)."""

    def __init__(self, proc: subprocess.Popen, job: _Job, log_path: Path | None):
        self.proc = proc
        self._job = job
        self.log_path = log_path
        self._next_id = 1
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.last_error: dict | None = None
        self.session: Session | None = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -------------------------------------------------- lifecycle
    @classmethod
    def start(cls, exe: str | os.PathLike, *, testing: bool = False, env: Mapping[str, str] | None = None,
              log_path: str | os.PathLike | None = None) -> "EngineProcess":
        args = [str(exe), "--owner-pid", str(os.getpid())]
        if testing:
            args.append("--testing")
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        log = open(log_path, "ab") if log_path else subprocess.DEVNULL
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                env=full_env, creationflags=flags, bufsize=0)
        if log_path:
            log.close()
        job = _Job()
        job.assign(proc.pid)
        return cls(proc, job, Path(log_path) if log_path else None)

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        """Hard termination (crash test / unresponsive engine)."""
        if self.alive:
            self.proc.kill()
        self.proc.wait(10)
        self._close_frames()
        self._close_pipes()

    def shutdown(self, timeout: float = 30.0) -> int:
        if self.alive:
            try:
                self.request("shutdown", timeout=timeout)
            except (EngineGone, EngineError, TimeoutError):
                pass
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        self._close_frames()
        self._close_pipes()
        return self.proc.returncode

    def _close_pipes(self) -> None:
        self._reader.join(5)
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass

    def _close_frames(self) -> None:
        if self.session and self.session.frames:
            try:
                self.session.frames.close()
            except Exception:
                pass
            self.session.frames = None

    # -------------------------------------------------- transport
    def _read_exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _read_loop(self) -> None:
        while True:
            hdr = self._read_exact(16)
            if hdr is None:
                break
            magic, jl, bl = struct.unpack("<IIQ", hdr)
            if magic != FRAME_MAGIC:
                break
            js = self._read_exact(jl)
            blob = self._read_exact(bl) if bl else b""
            if js is None or blob is None:
                break
            msg = json.loads(js.decode("utf-8"))
            if msg.get("type") == "reply":
                with self._lock:
                    q = self._pending.pop(int(msg.get("id", 0)), None)
                if q is not None:
                    q.put((msg, blob))
            else:
                if msg.get("event") == "error":
                    self.last_error = msg.get("error")
                self.events.put(msg)
        # the process ended: wake every waiter
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for q in pending:
            q.put(None)
        self.events.put({"type": "event", "event": "process_ended"})

    def send(self, type_: str, fields: Mapping[str, Any] | None = None) -> queue.Queue:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            q: queue.Queue = queue.Queue()
            self._pending[rid] = q
        js, blob = _encode({"type": type_, "id": rid, **(fields or {})})
        try:
            self.proc.stdin.write(struct.pack("<IIQ", FRAME_MAGIC, len(js), len(blob)) + js + blob)
            self.proc.stdin.flush()
        except OSError:
            with self._lock:
                self._pending.pop(rid, None)
            raise EngineGone(self.proc.poll(), self.last_error)
        return q

    def request(self, type_: str, fields: Mapping[str, Any] | None = None, *, timeout: float = 120.0):
        q = self.send(type_, fields)
        try:
            got = q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"no reply to {type_} within {timeout} s")
        if got is None:
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
            raise EngineGone(self.proc.poll(), self.last_error)
        msg, blob = got
        if not msg.get("ok"):
            raise EngineError(msg.get("error", {}))
        msg["_blob"] = blob
        return msg

    # -------------------------------------------------- commands
    def hello(self) -> dict:
        return self.request("hello", {"protocol": PROTOCOL_VERSION, "api": list(API_VERSION),
                                      "client": "aether_engine_client.py"})

    def create_session(self, *, mode: str, quality: str, fps: float, cloths: Sequence[Mapping[str, Any]],
                       spheres=(), planes=(), meshes=(), gravity=(0.0, 0.0, -9.81), air_damping: float | None = None,
                       self_collision: bool = True, ring_slots: int | None = None,
                       allow_experimental: Sequence[str] = (), timeout: float = 300.0,
                       start_positions=None) -> Session:
        fields: dict[str, Any] = {
            "mode": mode, "quality": quality, "fps": fps, "gravity": list(gravity),
            "self_collision": self_collision,
            "cloths": [{"positions": f32(c["positions"]), "triangles": i32(c["triangles"]),
                        "material": c.get("material", {}),
                        **({"pins": i32(c["pins"])} if c.get("pins") is not None else {}),
                        **({"zones": list(c["zones"]), "triangle_zones": i32(c["triangle_zones"])}
                           if c.get("triangle_zones") is not None else {}),
                        # engine API 1.5
                        **({"shrink": f32(c["shrink"])} if c.get("shrink") is not None else {}),
                        **({"shrink_uv": f32(c["shrink_uv"])} if c.get("shrink_uv") is not None else {}),
                        **({"surface_offset": float(c["surface_offset"])} if c.get("surface_offset") else {}),
                        **({"flat_bending_rest": True} if c.get("flat_bending_rest") else {}),
                        **({"pressure": float(c["pressure"])} if c.get("pressure") else {})} for c in cloths],
            "spheres": [{"center": list(map(float, s["center"])), "radius": float(s["radius"])} for s in spheres],
            "planes": [{"point": list(map(float, p["point"])), "normal": list(map(float, p["normal"]))} for p in planes],
            "meshes": [{"vertices": f32(m["vertices"]), "triangles": i32(m["triangles"]),
                        **({"surface_offset": float(m["surface_offset"])} if m.get("surface_offset") else {})}
                       for m in meshes],
        }
        if air_damping is not None:
            fields["air_damping"] = air_damping
        if ring_slots is not None:
            fields["ring_slots"] = ring_slots
        if allow_experimental:
            fields["allow_experimental"] = list(allow_experimental)
        if start_positions is not None:   # e.g. a kept drape (the cloths' positions stay the rest shape)
            fields["start_positions"] = f32(start_positions)
        r = self.request("create_session", fields, timeout=timeout)
        s = Session(r["vertex_count"], r["cloth_offsets"], r["shm_name"], r["shm_bytes"], r["slot_count"],
                    r["scene_hash"], r["mode"], r["quality"], r["fps"])
        s.frames = FrameReader(s.shm_name, s.shm_bytes)
        self.session = s
        return s

    def get_state(self) -> tuple[np.ndarray, np.ndarray, int]:
        r = self.request("get_state")
        b = r["_blob"]
        return (blob_array(b, r["positions"]).reshape(-1, 3).copy(),
                blob_array(b, r["velocities"]).reshape(-1, 3).copy(), int(r["frame"]))

    def wait_event(self, name: str, timeout: float = 60.0, predicate: Callable[[dict], bool] | None = None) -> dict:
        """Next event called `name` (other events are skipped)."""
        deadline = time.perf_counter() + timeout
        while True:
            left = deadline - time.perf_counter()
            if left <= 0:
                raise TimeoutError(f"no {name} event within {timeout} s")
            try:
                ev = self.events.get(timeout=left)
            except queue.Empty:
                raise TimeoutError(f"no {name} event within {timeout} s")
            if ev.get("event") == "process_ended":
                self.events.put(ev)
                raise EngineGone(self.proc.poll(), self.last_error)
            if ev.get("event") == name and (predicate is None or predicate(ev)):
                return ev


# ---------------------------------------------------------------- bake output boundary
class Pc2BakeSink:
    """Feeds bake frames into one PC2 writer per cloth object.

    `writers[c]` must provide ``write_frame(positions)`` (e.g. Cloth NeXt's
    StreamingPc2Writer) and, for resume verification, ``frames_written`` plus a
    ``read_frame(index)`` callable. After an engine restart the bake resumes from
    a checkpoint at frame K <= frames already written: frames below the written
    count are recomputed bit-identically, verified against the cache and skipped,
    so the cache never contains a frame twice and no frame is lost.
    """

    def __init__(self, session: Session, writers: Sequence[Any], first_frame: int = 0,
                 read_frame: Callable[[int, int], np.ndarray] | None = None):
        self.session = session
        self.writers = list(writers)
        self.first_frame = first_frame
        self.read_frame = read_frame
        self.verified = 0
        self.written = 0

    def frames_on_disk(self) -> int:
        return min(w.frames_written for w in self.writers)

    def accept(self, frame: int, positions: np.ndarray) -> None:
        index = frame - 1 - self.first_frame  # engine frame f holds the state after f steps
        on_disk = self.frames_on_disk()
        if index < on_disk:
            for c in range(len(self.writers)):
                expect = self.read_frame(c, index)
                if not np.array_equal(expect.view("<u4"), self.session.cloth_slice(positions, c).view("<u4")):
                    raise RuntimeError(f"resumed frame {frame} differs from the cached frame (not bit-identical)")
            self.verified += 1
            return
        if index != on_disk:
            raise RuntimeError(f"frame {frame} would leave a gap in the cache (cache has {on_disk} frames)")
        for c, w in enumerate(self.writers):
            w.write_frame(self.session.cloth_slice(positions, c))
        self.written += 1
