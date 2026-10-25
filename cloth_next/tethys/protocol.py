# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS Engine protocol v1: binary request, water-contact stream, wetness cache.

Large arrays travel as little-endian binary blocks, never JSON. Every
reader validates sizes, ranges and checksums and rejects unknown versions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import struct
import uuid

import numpy as np

PROTOCOL_VERSION = 2
REQUEST_MAGIC = b"TETHYSRQ"
ENV_MAGIC = b"TETHYSEV"
CACHE_MAGIC = b"TETHYSWC"
INDEX_MAGIC = b"TETHYSIX"
END_MAGIC = b"TETHYEND"

CHANNEL_SURFACE = 1 << 0     # mm of surface film per polygon
CHANNEL_SATURATION = 1 << 1  # absorbed / capacity per polygon
CHANNEL_CONTACT = 1 << 2     # mean contact fraction during the frame
CHANNEL_DRAINED = 1 << 3     # mm drained from open edges during the frame
CHANNEL_FLOW = 1 << 4        # surface flux density vector, 3 values
CHANNEL_TEXTURE = 1 << 5     # UV texture: surface mm and saturation per pixel
MATERIAL_VALUES = 10
CHANNEL_ORDER = ((CHANNEL_SURFACE, "surface", 1), (CHANNEL_SATURATION, "saturation", 1),
                 (CHANNEL_CONTACT, "contact", 1), (CHANNEL_DRAINED, "drained", 1),
                 (CHANNEL_FLOW, "flow", 3))
LEDGER_FIELDS = ("time", "initial", "injected", "surface", "absorbed", "evaporated", "drained",
                 "residual", "roundoff", "substeps", "limited_substeps", "interface_surface",
                 "interface_absorbed", "degenerate_cells", "missing_direction_cells", "reserved")
MAX_ENV_FRAME_BYTES = 512 * 1024 * 1024


class ProtocolError(ValueError):
    """Malformed or incompatible TETHYS data."""


@dataclass(frozen=True)
class EngineSettings:
    fps: float
    gravity: tuple[float, float, float]
    max_substeps: int = 64
    flow_strength: float = 1.0
    absorption: float = 1.0
    capillary: float = 1.0
    evaporation: float = 1.0
    repellency: float = 1.0
    retention: float = 1.0
    ambient_evaporation: float = 2e-7   # m/s
    contact_low: float = 0.25
    contact_high: float = 0.75
    immersion_depth: float = 1e-3       # m
    wetting_time: float = 0.1           # s
    length_scale: float = 1.0           # m per Blender unit
    debug_channels: bool = False

    def values(self) -> list[float]:
        values = [self.fps, *self.gravity, self.max_substeps, self.flow_strength, self.absorption,
                  self.capillary, self.evaporation, self.repellency, self.retention,
                  self.ambient_evaporation, self.contact_low, self.contact_high,
                  self.immersion_depth, self.wetting_time, self.length_scale,
                  1.0 if self.debug_channels else 0.0, 0.0]
        if not all(math.isfinite(float(v)) for v in values):
            raise ProtocolError("TETHYS settings contain a non-finite value")
        return [float(v) for v in values]


@dataclass
class EngineRequest:
    settings: EngineSettings
    first_frame: int
    last_frame: int
    pc2_first_sample: int
    vertex_count: int
    triangles: np.ndarray            # (T, 3) int
    polygon_of: np.ndarray           # (T,) int
    polygon_count: int
    material_of: np.ndarray          # (T,) int
    materials: list                  # rows of 9 SI values
    matrix_world: np.ndarray         # (4, 4)
    pc2_path: str
    env_path: str
    output_path: str
    cancel_path: str
    identity: str
    uv: np.ndarray | None = None     # (T, 6) float or None
    detail_level: int = 1            # virtual sub-cells per cloth triangle edge
    texture_size: int = 0            # UV wetness texture edge in pixels (0: none)
    feature_scale: float = 0.004     # m, size of the fabric's surface irregularities
    seed: int = 1


def _section(tag: bytes, payload: bytes) -> bytes:
    if len(tag) != 4:
        raise ProtocolError("invalid section tag")
    padding = (-len(payload)) % 8
    return tag + struct.pack("<IQ", 0, len(payload)) + payload + b"\0" * padding


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


def encode_request(request: EngineRequest) -> bytes:
    triangles = np.ascontiguousarray(request.triangles, dtype="<i4")
    count = len(triangles)
    if triangles.ndim != 2 or triangles.shape[1] != 3 or count == 0:
        raise ProtocolError("TETHYS needs at least one triangle")
    polygon_of = np.ascontiguousarray(request.polygon_of, dtype="<i4")
    material_of = np.ascontiguousarray(request.material_of, dtype="<u4")
    if polygon_of.shape != (count,) or material_of.shape != (count,):
        raise ProtocolError("TETHYS triangle tables have the wrong size")
    rows = np.asarray(request.materials, dtype="<f8")
    if rows.ndim != 2 or rows.shape[1] != MATERIAL_VALUES or not len(rows) or not np.isfinite(rows).all():
        raise ProtocolError("TETHYS material table is invalid")
    matrix = np.asarray(request.matrix_world, dtype="<f8")
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ProtocolError("TETHYS object transform is invalid")
    settings = np.asarray(request.settings.values(), dtype="<f8")
    sections = [
        _section(b"SETT", struct.pack("<II", len(settings), 0) + settings.tobytes()),
        _section(b"FRMS", struct.pack("<iiii", request.first_frame, request.last_frame,
                                      request.pc2_first_sample, 0)),
        _section(b"MESH", struct.pack("<IIII", request.vertex_count, count, request.polygon_count, 0)
                 + triangles.tobytes() + polygon_of.tobytes() + material_of.tobytes()),
        _section(b"MATL", struct.pack("<II", len(rows), MATERIAL_VALUES) + rows.tobytes()),
        _section(b"DETL", struct.pack("<IIdII", int(request.detail_level), int(request.texture_size),
                                      float(request.feature_scale), int(request.seed) & 0xFFFFFFFF, 0)),
        _section(b"XFRM", matrix.tobytes()),  # row-major
        _section(b"PATH", _string(request.pc2_path) + _string(request.env_path)
                 + _string(request.output_path) + _string(request.cancel_path)),
        _section(b"IDNT", _string(request.identity)),
    ]
    if request.uv is not None:
        uv = np.ascontiguousarray(request.uv, dtype="<f4").reshape(-1)
        if uv.shape != (count * 6,) or not np.isfinite(uv).all():
            raise ProtocolError("TETHYS UV table is invalid")
        sections.append(_section(b"UVCO", struct.pack("<II", len(uv), 0) + uv.tobytes()))
    return REQUEST_MAGIC + struct.pack("<II", PROTOCOL_VERSION, len(sections)) + b"".join(sections)


def write_atomic_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass
class WaterBlock:
    """Cropped GAIA nodes around the cloth for one frame (Blender world units)."""
    origin: tuple[float, float, float]
    spacing: tuple[float, float, float]
    influence: np.ndarray            # (nz, ny, nx) float32, 0..1
    velocity: np.ndarray             # (nz, ny, nx, 3) float32, m/s

    def encode(self) -> bytes:
        influence = np.ascontiguousarray(self.influence, dtype="<f4")
        velocity = np.ascontiguousarray(self.velocity, dtype="<f4")
        if (influence.ndim != 3 or min(influence.shape) < 2 or velocity.shape != influence.shape + (3,)
                or not np.isfinite(influence).all() or not np.isfinite(velocity).all()
                or influence.min() < 0 or influence.max() > 1):
            raise ProtocolError("Water contact block is invalid")
        if not all(math.isfinite(v) for v in (*self.origin, *self.spacing)) or min(self.spacing) <= 0:
            raise ProtocolError("Water contact block placement is invalid")
        nz, ny, nx = influence.shape
        return (struct.pack("<6d4I", *self.origin, *self.spacing, nx, ny, nz, 0)
                + influence.tobytes() + velocity.tobytes())


def write_env(path: Path, first_frame: int, frame_count: int, fps: float, blocks, *,
              check_cancel=None) -> None:
    """Stream `frame_count` frames; `blocks` yields WaterBlock or None (no liquid near the cloth).

    Layout: 32-byte header | frame table (24 bytes per frame) | frame blocks.
    """
    path = Path(path)
    if frame_count <= 0:
        raise ProtocolError("Water contact data is empty")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    entries = []
    try:
        with temporary.open("xb") as stream:
            stream.write(ENV_MAGIC + struct.pack("<IIiId", PROTOCOL_VERSION, frame_count, first_frame, 0,
                                                   float(fps)))
            stream.write(b"\0" * (24 * frame_count))
            for block in blocks:
                if check_cancel is not None:
                    check_cancel()
                if len(entries) >= frame_count:
                    raise ProtocolError("Water contact data has more frames than declared")
                frame = first_frame + len(entries)
                if block is None:
                    entries.append((frame, 0, 0, 0))
                    continue
                data = block.encode()
                if len(data) > MAX_ENV_FRAME_BYTES:
                    raise ProtocolError("Water contact frame is too large; lower the Fluid Cache resolution")
                entries.append((frame, 1, stream.tell(), len(data)))
                stream.write(data)
            if len(entries) != frame_count:
                raise ProtocolError("Water contact data is missing frames")
            stream.seek(32)
            stream.write(b"".join(struct.pack("<iIQQ", *entry) for entry in entries))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass
class CacheFrame:
    frame: int
    ledger: dict
    channels: dict = field(default_factory=dict)


class CacheReader:
    """Validated random access to a published TETHYS wetness cache."""

    def __init__(self, path: Path, *, verify_all: bool = False):
        self.path = Path(path)
        self._stream = self.path.open("rb")
        try:
            self._read_layout()
            if verify_all:
                for index in range(self.frame_count):
                    self._block(index)
        except Exception:
            self._stream.close()
            raise

    def _read_layout(self):
        stream = self._stream
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        header = stream.read(48)
        if len(header) != 48 or header[:8] != CACHE_MAGIC:
            raise ProtocolError("Not a TETHYS wetness cache")
        version, channels, polygons, first, last, identity_length, texture, detail, fps = struct.unpack(
            "<IIIiiIIId", header[8:48])
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"Unsupported TETHYS cache version {version}")
        if (polygons <= 0 or last < first or identity_length > (1 << 20) or not fps > 0
                or texture > 8192 or not 1 <= detail <= 16 or bool(texture) != bool(channels & CHANNEL_TEXTURE)):
            raise ProtocolError("TETHYS cache header is invalid")
        identity = stream.read(identity_length)
        if len(identity) != identity_length:
            raise ProtocolError("TETHYS cache header is truncated")
        if size < 16:
            raise ProtocolError("TETHYS cache is truncated")
        stream.seek(size - 16)
        trailer = stream.read(16)
        index_offset = struct.unpack("<Q", trailer[:8])[0]
        if trailer[8:] != END_MAGIC or index_offset >= size:
            raise ProtocolError("TETHYS cache is incomplete or corrupted")
        stream.seek(index_offset)
        head = stream.read(16)
        if head[:8] != INDEX_MAGIC:
            raise ProtocolError("TETHYS cache index is corrupted")
        count = struct.unpack("<I", head[8:12])[0]
        if count != last - first + 1 or index_offset + 16 + 48 * count + 16 != size:
            raise ProtocolError("TETHYS cache index does not match its frame range")
        self.entries = []
        for _ in range(count):
            offset, length = struct.unpack("<QQ", stream.read(16))
            digest = stream.read(32)
            if offset + length > index_offset:
                raise ProtocolError("TETHYS cache frame table is corrupted")
            self.entries.append((offset, length, digest))
        self.channels = channels
        self.polygon_count = polygons
        self.texture_size = texture
        self.detail_level = detail
        self.first_frame, self.last_frame, self.fps = first, last, fps
        self.identity = identity.decode("utf-8")
        self.frame_count = count
        self._layout = [(bit, name, width) for bit, name, width in CHANNEL_ORDER if channels & bit]
        unknown = channels & ~(sum(bit for bit, _, _ in CHANNEL_ORDER) | CHANNEL_TEXTURE)
        if unknown or not channels & CHANNEL_SURFACE or not channels & CHANNEL_SATURATION:
            raise ProtocolError("TETHYS cache channel layout is not supported")
        expected = (8 + 8 * len(LEDGER_FIELDS) + sum(2 * polygons * w for _, _, w in self._layout)
                    + 4 * texture * texture)
        expected += (-expected) % 8
        if any(length != expected for _, length, _ in self.entries):
            raise ProtocolError("TETHYS cache frame size does not match its channels")

    def _block(self, index):
        offset, length, digest = self.entries[index]
        self._stream.seek(offset)
        data = self._stream.read(length)
        if len(data) != length or hashlib.sha256(data).digest() != digest:
            raise ProtocolError(f"TETHYS cache frame {self.first_frame + index} failed its checksum")
        return data

    def read(self, frame: int) -> CacheFrame:
        if not self.first_frame <= frame <= self.last_frame:
            raise ProtocolError(f"Frame {frame} is outside the TETHYS cache")
        data = self._block(frame - self.first_frame)
        stored, _reserved = struct.unpack_from("<iI", data, 0)
        if stored != frame:
            raise ProtocolError("TETHYS cache frame order is corrupted")
        ledger_values = struct.unpack_from(f"<{len(LEDGER_FIELDS)}d", data, 8)
        result = CacheFrame(frame, dict(zip(LEDGER_FIELDS, ledger_values)))
        position = 8 + 8 * len(LEDGER_FIELDS)
        for _bit, name, width in self._layout:
            count = self.polygon_count * width
            array = np.frombuffer(data, dtype="<f2", count=count, offset=position).astype(np.float32)
            result.channels[name] = array.reshape(-1, 3) if width == 3 else array
            position += 2 * count
        if self.texture_size:
            size = self.texture_size
            pixels = np.frombuffer(data, dtype="<f2", count=size * size * 2, offset=position)
            result.channels["texture"] = pixels.astype(np.float32).reshape(size, size, 2)
        return result

    def close(self):
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
