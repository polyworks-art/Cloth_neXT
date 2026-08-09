# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PPF 0.11 Scene ("Data") payload for the Phase-3A vertical slice.

Exact reproduction of the subset of ``kinds/scene.rs`` /
``encoder/mesh.py`` (pinned commit ``7193f158``) that one triangulated
cloth SHELL plus one static triangulated collider needs:

``[{"type": "SHELL",  "object": [<cloth info>]},
  {"type": "STATIC", "object": [<collider info>]}]``

Each object info carries exactly ``name``, ``uuid``, ``vert`` (object-local
float32-precision positions), ``transform`` (4x4 row-major float64,
``Z2Y @ matrix_world``), and ``face`` (uint32 triangles). Shell UVs and loose
Sewing edges are emitted through upstream's optional ``uv`` and ``stitch``
fields. ``mesh_ref`` and unsupported animation fields remain absent.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..coordinates import Mat4
from ...materials.friction import (PPF_FRICTION_SCALE,
                                   artist_friction_to_ppf)
from . import envelope

GROUP_SHELL = "SHELL"
GROUP_STATIC = "STATIC"
GROUP_ROD = "ROD"
GROUP_SOLID = "SOLID"
GROUP_PDRD = "PDRD"
INTERNAL_STATIC_NAME = "__cloth_next_solver_static__"
INTERNAL_STATIC_UUID = "cloth-next-internal-static-v1"


class SceneEncodeError(ValueError):
    pass


def _float32(value: float) -> float:
    """Round-trip through IEEE float32 so the wire carries exactly the
    precision the upstream encoder ships (numpy float32 -> CBOR double)."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _schema2_full_frame_indices(name: str, animation: dict):
    """Select the one captured pose that belongs to each Blender frame.

    Protocol 0.13 removed per-sample times from the DATA payload. Row ``i`` of
    ``static_deform_animation`` is therefore frame offset ``i``; dense Cloth
    NeXt sub-frame samples must not be serialized as additional full frames.
    """
    sample_offsets = animation.get("_sample_frame_offset")
    logical_count = animation.get("_logical_frame_count")
    samples_per_frame = animation.get("_samples_per_frame")
    capture_fps = animation.get("_capture_fps")
    times = animation.get("time")
    if any(value is None for value in (
            sample_offsets, logical_count, samples_per_frame,
            capture_fps, times)):
        raise SceneEncodeError(
            f"{name}: Schema 2 animated Collider is missing canonical "
            "timeline metadata")
    logical_count = int(logical_count)
    samples_per_frame = int(samples_per_frame)
    capture_fps = float(capture_fps)
    if logical_count < 2 or not 1 <= samples_per_frame <= 32:
        raise SceneEncodeError(
            f"{name}: invalid animated Collider logical frame/sample count")
    if not math.isfinite(capture_fps) or capture_fps <= 0.0:
        raise SceneEncodeError(
            f"{name}: animated Collider capture FPS must be positive")
    offsets = [float(value) for value in sample_offsets]
    times = [float(value) for value in times]
    expected_samples = (logical_count - 1) * samples_per_frame + 1
    if len(offsets) != expected_samples or len(times) != expected_samples:
        raise SceneEncodeError(
            f"{name}: animated Collider timeline has {len(offsets)} offsets "
            f"and {len(times)} times; expected {expected_samples}")
    for index, (offset, time_value) in enumerate(zip(offsets, times)):
        expected_offset = index / float(samples_per_frame)
        expected_time = expected_offset / capture_fps
        if (not math.isfinite(offset)
                or abs(offset - expected_offset) > 1e-9):
            raise SceneEncodeError(
                f"{name}: animated Collider sample {index} has frame offset "
                f"{offset!r}; expected {expected_offset!r}")
        if (not math.isfinite(time_value)
                or abs(time_value - expected_time) > 1e-9):
            raise SceneEncodeError(
                f"{name}: animated Collider sample {index} has time "
                f"{time_value!r}; expected {expected_time!r}")
    selected = tuple(
        frame * samples_per_frame for frame in range(logical_count))
    return selected, list(range(logical_count))


@dataclass(frozen=True, slots=True)
class SceneObject:
    """Immutable, pure-Python description of one exported mesh object."""

    name: str
    uuid: str
    vertices_local: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    transform: Mat4  # solver-space world matrix (Z2Y @ matrix_world)
    pin_indices: tuple[int, ...] = ()
    transform_animation: dict | None = None
    static_deform_animation: dict | None = None
    static_operations: tuple[dict, ...] = ()
    edges: tuple[tuple[int, int], ...] = ()
    stitch_pairs: tuple[tuple[int, int], ...] = ()
    uv_faces: tuple[tuple[tuple[float, float], ...], ...] = ()
    face_friction: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SceneEncodeError("object name must not be empty")
        if not self.uuid.strip():
            raise SceneEncodeError("object uuid must not be empty")
        if len(self.vertices_local) == 0:
            raise SceneEncodeError(f"{self.name}: mesh has no vertices")
        if len(self.triangles) == 0 and len(self.edges) == 0:
            raise SceneEncodeError(f"{self.name}: object has no elements")
        count = len(self.vertices_local)
        for vertex in self.vertices_local:
            if len(vertex) != 3 or any(not math.isfinite(c) for c in vertex):
                raise SceneEncodeError(f"{self.name}: non-finite vertex")
        for tri in self.triangles:
            if len(tri) != 3:
                raise SceneEncodeError(f"{self.name}: non-triangle face")
            if len(set(tri)) != 3:
                raise SceneEncodeError(f"{self.name}: degenerate triangle {tri}")
            for index in tri:
                if not 0 <= index < count:
                    raise SceneEncodeError(
                        f"{self.name}: triangle index {index} out of range")
        for edge in self.edges:
            if len(edge) != 2 or edge[0] == edge[1] or any(
                    not 0 <= index < count for index in edge):
                raise SceneEncodeError(f"{self.name}: invalid edge {edge}")
        for pair in self.stitch_pairs:
            if len(pair) != 2 or pair[0] == pair[1] or any(
                    not 0 <= index < count for index in pair):
                raise SceneEncodeError(f"{self.name}: invalid stitch {pair}")
        if len(self.uv_faces):
            if len(self.uv_faces) != len(self.triangles):
                raise SceneEncodeError(
                    f"{self.name}: UV face count does not match triangles")
            for face in self.uv_faces:
                if (len(face) != 3 or any(len(uv) != 2 for uv in face)
                        or any(not math.isfinite(c) for uv in face for c in uv)):
                    raise SceneEncodeError(f"{self.name}: invalid face UVs")
        if len(self.face_friction):
            if len(self.face_friction) != len(self.triangles):
                raise SceneEncodeError(
                    f"{self.name}: face friction count does not match triangles")
            if any(not math.isfinite(value) or not 0.0 <= value <= 1.0
                   for value in self.face_friction):
                raise SceneEncodeError(f"{self.name}: invalid face friction")
        if tuple(sorted(set(self.pin_indices))) != self.pin_indices or any(
                not 0 <= index < count for index in self.pin_indices):
            raise SceneEncodeError(f"{self.name}: invalid pin indices")
        if len(self.transform) != 4 or any(len(r) != 4 for r in self.transform):
            raise SceneEncodeError(f"{self.name}: transform must be 4x4")
        if any(not math.isfinite(c) for row in self.transform for c in row):
            raise SceneEncodeError(f"{self.name}: non-finite transform")
        if sum((
                self.transform_animation is not None,
                self.static_deform_animation is not None,
                bool(self.static_operations))) > 1:
            raise SceneEncodeError(
                f"{self.name}: collider motion sources are mutually exclusive")
        if self.transform_animation is not None:
            animation = self.transform_animation
            required = ("time", "translation", "quaternion", "scale")
            if any(key not in animation for key in required):
                raise SceneEncodeError(
                    f"{self.name}: incomplete transform animation")
            frame_count = len(animation["time"])
            if frame_count < 2 or any(len(animation[key]) != frame_count
                                      for key in required[1:]):
                raise SceneEncodeError(
                    f"{self.name}: inconsistent transform animation")
            segments = animation.get("segments")
            if segments is None or len(segments) != frame_count - 1:
                raise SceneEncodeError(
                    f"{self.name}: transform animation requires one segment "
                    "per frame interval")
        if self.static_deform_animation is not None:
            animation = self.static_deform_animation
            frames = animation.get("vert_frames")
            times = animation.get("time")
            shape = tuple(getattr(frames, "shape", ()))
            if (times is None or len(times) < 2 or len(shape) != 3
                    or shape[0] != len(times) or shape[1] != count
                    or shape[2] != 3):
                raise SceneEncodeError(
                    f"{self.name}: inconsistent static deformation animation")

    def info_dict(self, *, schema_version: int = 1) -> dict:
        numpy_vertices = type(self.vertices_local).__module__.split(".", 1)[0] == "numpy"
        numpy_triangles = type(self.triangles).__module__.split(".", 1)[0] == "numpy"
        info = {
            "name": self.name,
            "uuid": self.uuid,
            "vert": (self.vertices_local
                     if numpy_vertices else
                     [[_float32(c) for c in vertex]
                      for vertex in self.vertices_local]),
            "transform": [list(row) for row in self.transform],
        }
        if len(self.triangles):
            info["face"] = (self.triangles if numpy_triangles else
                            [list(tri) for tri in self.triangles])
        if len(self.uv_faces):
            is_numpy = (type(self.uv_faces).__module__.split(".", 1)[0]
                        == "numpy")
            info["uv"] = (self.uv_faces if is_numpy else
                          [[[_float32(c) for c in uv] for uv in face]
                           for face in self.uv_faces])
        if len(self.face_friction):
            is_numpy = (type(self.face_friction).__module__.split(".", 1)[0]
                        == "numpy")
            info["face_friction"] = (
                self.face_friction * PPF_FRICTION_SCALE if is_numpy else
                [_float32(artist_friction_to_ppf(value))
                 for value in self.face_friction])
        if len(self.edges):
            is_numpy = (type(self.edges).__module__.split(".", 1)[0]
                        == "numpy")
            info["edge"] = (self.edges if is_numpy else
                            [list(edge) for edge in self.edges])
        if self.stitch_pairs:
            # Official PPF loose-edge representation. Each source vertex is
            # constrained directly to the target vertex; duplicated target
            # slots carry zero weight and retain the canonical 4-wide shape.
            indices = [[source, target, target, target]
                       for source, target in self.stitch_pairs]
            weights = [[1.0, 1.0, 0.0, 0.0]
                       for _pair in self.stitch_pairs]
            info["stitch"] = [indices, weights]
        if len(self.pin_indices):
            is_numpy = (type(self.pin_indices).__module__.split(".", 1)[0]
                        == "numpy")
            info["pin"] = (self.pin_indices if is_numpy else
                           list(self.pin_indices))
        if self.transform_animation is not None:
            animation = dict(self.transform_animation)
            if schema_version == 2:
                indices, frame_offsets = _schema2_full_frame_indices(
                    self.name, animation)
                sample_count = indices[-1] + 1
                for key in ("translation", "quaternion", "scale"):
                    if len(animation.get(key, ())) != sample_count:
                        raise SceneEncodeError(
                            f"{self.name}: animated Collider {key} has "
                            f"{len(animation.get(key, ()))} samples; expected "
                            f"{sample_count}")
                if len(animation.get("segments", ())) != sample_count - 1:
                    raise SceneEncodeError(
                        f"{self.name}: animated Collider segments has "
                        f"{len(animation.get('segments', ()))} entries; "
                        f"expected {sample_count - 1}")
                animation.pop("time")
                for key in ("translation", "quaternion", "scale"):
                    animation[key] = [animation[key][index]
                                      for index in indices]
                source_segments = animation["segments"]
                animation["segments"] = [
                    source_segments[indices[index]]
                    for index in range(len(indices) - 1)]
                animation["frame_offset"] = frame_offsets
            for key in (
                    "_sample_frame_offset", "_logical_frame_count",
                    "_samples_per_frame", "_capture_fps"):
                animation.pop(key, None)
            info["transform_animation"] = animation
        if self.static_deform_animation is not None:
            animation = dict(self.static_deform_animation)
            if schema_version == 2:
                indices, _frame_offsets = _schema2_full_frame_indices(
                    self.name, animation)
                animation.pop("time", None)
                frames = animation["vert_frames"]
                if len(frames) != indices[-1] + 1:
                    raise SceneEncodeError(
                        f"{self.name}: animated Collider vert_frames has "
                        f"{len(frames)} samples; expected {indices[-1] + 1}")
                try:
                    animation["vert_frames"] = frames[list(indices)]
                except TypeError:
                    animation["vert_frames"] = [frames[index]
                                                for index in indices]
            for key in (
                    "_sample_frame_offset", "_logical_frame_count",
                    "_samples_per_frame", "_capture_fps"):
                animation.pop(key, None)
            info["static_deform_animation"] = animation
        if self.static_operations:
            operations = []
            for operation in self.static_operations:
                value = dict(operation)
                if schema_version == 2:
                    fps = float(value.pop("fps", 1.0))
                    if "time_start" in value:
                        value["frame_offset_start"] = int(round(
                            float(value.pop("time_start"))
                            * fps))
                    if "time_end" in value:
                        value["frame_offset_end"] = int(round(
                            float(value.pop("time_end"))
                            * fps))
                    if "angular_velocity" in value:
                        value["angular_velocity_anim"] = float(
                            value.pop("angular_velocity"))
                operations.append(value)
            info["static_ops"] = operations
        return info


def internal_static_sentinel() -> SceneObject:
    """Tiny remote tetrahedron for PPF builds that require a STATIC group.

    PPF 0.11 remains BUSY when building a scene with no STATIC group.  The
    sentinel is an implementation detail, far outside practical scene space,
    and is added only when the artist supplied no Collider.
    """
    return SceneObject(
        INTERNAL_STATIC_NAME, INTERNAL_STATIC_UUID,
        ((0.0,0.0,0.1),(0.1,0.0,-0.1),(-0.05,0.0866,-0.1),
         (-0.05,-0.0866,-0.1)),
        ((0,1,2),(0,2,3),(0,3,1),(1,3,2)),
        ((1.0,0.0,0.0,1_000_000.0),(0.0,1.0,0.0,1_000_000.0),
         (0.0,0.0,1.0,1_000_000.0),(0.0,0.0,0.0,1.0)))


def _collider_sequence(collider) -> tuple[SceneObject, ...]:
    return (() if collider is None else
            (collider,) if isinstance(collider, SceneObject)
            else tuple(collider))


def build_scene_payload(cloth: SceneObject, collider, *,
                        schema_version: int = 1) -> list:
    """One SHELL group and, when present, one STATIC collider group."""
    return build_multi_deformable_scene_payload(
        ((cloth, GROUP_SHELL),), collider, schema_version=schema_version)


def build_deformable_scene_payload(deformable: SceneObject, collider, *,
                                   group_type: str,
                                   schema_version: int = 1) -> list:
    return build_multi_deformable_scene_payload(
        ((deformable, group_type),), collider,
        schema_version=schema_version)


def build_multi_deformable_scene_payload(deformables, collider, *,
                                         schema_version: int = 1) -> list:
    """Build one scene containing every dynamic object and shared colliders.

    Dynamic objects are grouped by PPF element type while preserving their
    input order within each group.  The group order is fixed so payload hashes
    stay deterministic across runs.
    """
    entries = tuple(deformables)
    if not entries:
        raise SceneEncodeError("at least one deformable is required")
    grouped = {GROUP_SHELL: [], GROUP_ROD: [], GROUP_SOLID: [], GROUP_PDRD: []}
    for deformable, group_type in entries:
        if not isinstance(deformable, SceneObject):
            raise SceneEncodeError("deformables must be SceneObject values")
        if group_type not in grouped:
            raise SceneEncodeError(f"unsupported deformable group: {group_type}")
        grouped[group_type].append(deformable)
    colliders = _collider_sequence(collider)
    uuids = [item.uuid for item, _group in entries]
    uuids.extend(item.uuid for item in colliders)
    if len(set(uuids)) != len(uuids):
        raise SceneEncodeError("deformables and colliders need distinct UUIDs")
    payload = [
        {"object": [item.info_dict(schema_version=schema_version)
                    for item in grouped[kind]], "type": kind}
        for kind in (GROUP_SHELL, GROUP_ROD, GROUP_SOLID, GROUP_PDRD)
        if grouped[kind]
    ]
    if colliders:
        payload.append({"object": [
            item.info_dict(schema_version=schema_version) for item in colliders],
                        "type": GROUP_STATIC})
    return payload


def encode_deformable_scene(deformable: SceneObject, collider, *,
                            group_type: str,
                            schema_version: int = 1) -> tuple[bytes, str]:
    blob = envelope.dumps_envelope(
        envelope.KIND_SCENE,
        build_deformable_scene_payload(deformable, collider,
                                       group_type=group_type,
                                       schema_version=schema_version),
        schema_version=schema_version)
    return blob, envelope.payload_sha256(blob)


def encode_multi_deformable_scene(deformables, collider, *,
                                  schema_version: int = 1) -> tuple[bytes, str]:
    blob = envelope.dumps_envelope(
        envelope.KIND_SCENE,
        build_multi_deformable_scene_payload(
            deformables, collider, schema_version=schema_version),
        schema_version=schema_version)
    return blob, envelope.payload_sha256(blob)


def encode_multi_deformable_scene_file(deformables, collider, path: Path, *,
                                       progress=None,
                                       schema_version: int = 1
                                       ) -> tuple[Path, str]:
    digest = envelope.dump_envelope_file(
        envelope.KIND_SCENE,
        build_multi_deformable_scene_payload(
            deformables, collider, schema_version=schema_version), path,
        progress=progress, schema_version=schema_version)
    return path, digest


def encode_scene(cloth: SceneObject, collider, *,
                 schema_version: int = 1) -> tuple[bytes, str]:
    blob = envelope.dumps_envelope(
        envelope.KIND_SCENE,
        build_scene_payload(cloth, collider, schema_version=schema_version),
        schema_version=schema_version)
    return blob, envelope.payload_sha256(blob)


def zero_area_triangles(vertices: Sequence[Sequence[float]],
                        triangles: Sequence[Sequence[int]],
                        *, epsilon: float = 1e-12) -> list[int]:
    """Indices of triangles with (near-)zero area, for scene validation."""
    bad: list[int] = []
    for index, (a, b, c) in enumerate(triangles):
        ax, ay, az = vertices[a]
        bx, by, bz = vertices[b]
        cx, cy, cz = vertices[c]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        if (nx * nx + ny * ny + nz * nz) <= epsilon * epsilon:
            bad.append(index)
    return bad
