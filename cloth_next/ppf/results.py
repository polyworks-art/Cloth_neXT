# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Decode and validate PPF solver output: the per-object vertex map and the
per-frame vertex buffers.

Verified contracts (pinned commit ``7193f158`` + the shipped solver source):

- ``session/map.pickle`` is a CBOR envelope ``kind="VertexMap"`` whose
  payload maps each object UUID to the list of that object's vertex indices
  into the global per-frame vertex array, in the object's original vertex
  order (``frontend/_scene_.py:export_fixed`` producer,
  ``blender_addon/core/effect_runner.py:_decode_vertex_map_cbor`` consumer).
- ``session/output/vert_<N>.bin`` (N >= 1) is a raw little-endian float32
  array of solver world-space positions, three floats per vertex
  (``datamodel/session/frames.rs::read_vertex_bin``).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np

from .schema import envelope

MAP_PATH = "session/map.pickle"
SURFACE_MAP_PATH = "session/surface_map.pickle"
OUTPUT_DIR_PATH = "session/output"


class ResultValidationError(ValueError):
    pass


def frame_file_path(frame: int) -> str:
    if frame < 0:
        raise ValueError("frame index must be non-negative")
    return f"{OUTPUT_DIR_PATH}/vert_{frame}.bin"


@dataclass(frozen=True, slots=True)
class OutputMap:
    """Validated object-UUID -> global-vertex-index map."""

    indices_by_uuid: dict[str, tuple[int, ...]]

    def indices_for(self, uuid: str, expected_count: int) -> tuple[int, ...]:
        indices = self.indices_by_uuid.get(uuid)
        if indices is None:
            raise ResultValidationError(
                f"solver output map has no entry for object UUID {uuid}")
        if len(indices) != expected_count:
            raise ResultValidationError(
                f"solver output map for {uuid} has {len(indices)} vertices, "
                f"expected {expected_count}")
        return indices


def parse_output_map(blob: bytes) -> OutputMap:
    payload = envelope.loads_envelope(blob, envelope.KIND_VERTEX_MAP)
    if not isinstance(payload, dict) or not payload:
        raise ResultValidationError("VertexMap payload must be a non-empty map")
    result: dict[str, tuple[int, ...]] = {}
    for uuid, raw_indices in payload.items():
        if not isinstance(uuid, str) or not uuid:
            raise ResultValidationError("VertexMap key is not an object UUID")
        if not isinstance(raw_indices, list) or not raw_indices:
            raise ResultValidationError(
                f"VertexMap entry for {uuid} is not a non-empty index list")
        indices: list[int] = []
        for value in raw_indices:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ResultValidationError(
                    f"VertexMap entry for {uuid} contains an invalid index "
                    f"{value!r}")
            indices.append(value)
        result[uuid] = tuple(indices)
    return OutputMap(result)


@dataclass(frozen=True, slots=True)
class SurfaceMap:
    tri_indices: np.ndarray
    coefficients: np.ndarray
    surface_triangles: np.ndarray


def parse_surface_map(blob: bytes, uuid: str,
                      expected_count: int) -> SurfaceMap:
    raw = __import__("cloth_next.ppf.schema.cbor_codec", fromlist=["loads"]).loads(blob)
    if not isinstance(raw, dict) or raw.get("kind") != "SurfaceMap":
        raise ResultValidationError("surface map envelope kind mismatch")
    payload = raw.get("payload")
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ResultValidationError("surface map payload version mismatch")
    maps = payload.get("maps")
    if not isinstance(maps, dict) or uuid not in maps:
        raise ResultValidationError(f"surface map has no entry for {uuid}")
    try:
        tri, coefficients, surface_triangles = maps[uuid]
        tri = np.asarray(tri, dtype=np.intp)
        coefficients = np.asarray(coefficients, dtype=np.float64)
        surface_triangles = np.asarray(surface_triangles, dtype=np.intp)
    except (TypeError, ValueError) as exc:
        raise ResultValidationError("surface map arrays are invalid") from exc
    if tri.shape != (expected_count,) or coefficients.shape != (expected_count, 3):
        raise ResultValidationError("surface map does not match source vertices")
    if surface_triangles.ndim != 2 or surface_triangles.shape[1] != 3:
        raise ResultValidationError("surface map triangles have invalid shape")
    if np.any(tri < 0) or np.any(tri >= len(surface_triangles)):
        raise ResultValidationError("surface triangle index is out of range")
    return SurfaceMap(tri, coefficients, surface_triangles)


def decode_frame_payload(blob: bytes) -> tuple[tuple[float, float, float], ...]:
    """Raw ``vert_<N>.bin`` bytes -> solver world-space position triples."""
    if not blob:
        raise ResultValidationError("frame payload is empty")
    if len(blob) % 12 != 0:
        raise ResultValidationError(
            f"frame payload size {len(blob)} is not a multiple of 12 bytes "
            "(float32 XYZ)")
    count = len(blob) // 12
    flat = struct.unpack(f"<{count * 3}f", blob)
    return tuple((flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2])
                 for i in range(count))


def decode_frame_payload_numpy(blob: bytes) -> np.ndarray:
    """Zero-copy view of one raw frame for the production import path."""
    if not blob:
        raise ResultValidationError("frame payload is empty")
    if len(blob) % 12 != 0:
        raise ResultValidationError(
            f"frame payload size {len(blob)} is not a multiple of 12 bytes "
            "(float32 XYZ)")
    positions = np.frombuffer(blob, dtype=np.dtype("<f4")).reshape((-1, 3))
    if not np.isfinite(positions).all():
        raise ResultValidationError("frame payload contains non-finite positions")
    return positions


def object_index_array(indices: tuple[int, ...], *, total_vertices: int,
                       uuid: str) -> np.ndarray:
    result = np.asarray(indices, dtype=np.intp)
    if result.ndim != 1 or result.size == 0:
        raise ResultValidationError(f"output map for {uuid} has no indices")
    if np.any(result < 0) or np.any(result >= total_vertices):
        bad = int(result[(result < 0) | (result >= total_vertices)][0])
        raise ResultValidationError(
            f"vertex index {bad} for {uuid} exceeds the frame's "
            f"{total_vertices} vertices")
    return result


def extract_object_frame_numpy(frame_positions: np.ndarray,
                               indices: np.ndarray, *, frame: int,
                               uuid: str, expected_count: int) -> np.ndarray:
    if frame_positions.ndim != 2 or frame_positions.shape[1] != 3:
        raise ResultValidationError(f"frame {frame} has invalid position shape")
    if indices.shape != (expected_count,):
        raise ResultValidationError(
            f"output map for {uuid} has {indices.size} vertices, expected "
            f"{expected_count}")
    if np.any(indices < 0) or np.any(indices >= frame_positions.shape[0]):
        raise ResultValidationError(
            f"frame {frame}: output map for {uuid} is outside the frame")
    result = frame_positions[indices]
    if not np.isfinite(result).all():
        raise ResultValidationError(
            f"frame {frame}: non-finite position for {uuid}")
    return result


def extract_object_frame(
        frame_positions: tuple[tuple[float, float, float], ...],
        indices: tuple[int, ...],
        *, frame: int, uuid: str) -> tuple[tuple[float, float, float], ...]:
    """Slice one object's solver world-space positions out of a full frame,
    preserving the original vertex order, with full validation."""
    total = len(frame_positions)
    result: list[tuple[float, float, float]] = []
    for index in indices:
        if index >= total:
            raise ResultValidationError(
                f"frame {frame}: vertex index {index} for {uuid} exceeds the "
                f"frame's {total} vertices")
        position = frame_positions[index]
        if any(not math.isfinite(c) for c in position):
            raise ResultValidationError(
                f"frame {frame}: non-finite position for {uuid} at global "
                f"vertex {index}")
        result.append(position)
    return tuple(result)
