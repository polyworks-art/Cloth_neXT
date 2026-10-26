# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Imported liquid particles (Houdini, LiquiGen: Alembic) as a GAIA Water Flow source.

Any object whose evaluated geometry carries liquid points with a point
velocity attribute works: an Alembic import (Mesh Sequence Cache), a point
cloud, or Geometry Nodes. The optional surface object (the liquid mesh) is
written per frame as a FLIP-style BOBJ folder so Hydrodynamic Pressure reads
it exactly like FLIP. Same interface as ``FLIPWaterFlowProvider``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy
import numpy as np

from ..gaia import mantaflow  # BOBJ/bounds writers shared with Mantaflow

VELOCITY_ATTRIBUTES = ("velocity", "v", "vel", ".velocities")
SURFACE_RECIPE = "particles-evaluated-surface-v1"


def surface_folder(container) -> Path:
    path = Path(bpy.path.abspath(str(container)))
    return path.with_name(path.stem + "_particle_surface")


def _cache_files(obj):
    """Alembic/USD files behind an object's Mesh Sequence Cache modifiers."""
    files = []
    for modifier in getattr(obj, "modifiers", ()):
        cache = getattr(modifier, "cache_file", None) if modifier.type == "MESH_SEQUENCE_CACHE" else None
        if cache is not None:
            files.append(cache)
    return files


def velocity_per_second(obj, fps) -> float:
    """1, or fps when the Alembic stores velocity per frame."""
    for cache in _cache_files(obj):
        if getattr(cache, "velocity_unit", "SECOND") == "FRAME":
            return float(fps)
    return 1.0


def velocity_attribute(mesh):
    for name in VELOCITY_ATTRIBUTES:
        attribute = mesh.attributes.get(name)
        if attribute is not None and attribute.domain == "POINT" and attribute.data_type == "FLOAT_VECTOR":
            return attribute
    return None


class ParticleWaterFlowProvider:
    def __init__(self, context, particles, frames, *, container, surface=None, provider="Alembic",
                 hash_sources=True, need_surface=False):
        if particles is None:
            raise ValueError("Pick the imported liquid particles (with a velocity attribute)")
        if abs(context.scene.unit_settings.scale_length - 1.0) > 1e-9:
            raise ValueError("Water Flow needs Scene Unit Scale 1 (metres)")
        if need_surface and surface is None:
            raise ValueError("Hydrodynamic Pressure needs the liquid surface: pick the Surface mesh")
        self.context, self.particles, self.surface = context, particles, surface
        frames = tuple(frames)
        self.frame_count = len(frames)
        self.fps = context.scene.render.fps / context.scene.render.fps_base
        self.velocity_scale = velocity_per_second(particles, self.fps)
        self.mode = "FLUID_PARTICLES"
        self.source_paths = []
        for obj in (particles, surface):
            for cache in _cache_files(obj) if obj is not None else ():
                path = Path(bpy.path.abspath(cache.filepath))
                if not path.is_file():
                    raise ValueError(f"Alembic file is missing: {path}")
                if path not in self.source_paths:
                    self.source_paths.append(path)
        self.root = surface_folder(container)
        scene = context.scene
        original = scene.frame_current
        minimum, maximum = np.full(3, np.inf), np.full(3, -np.inf)
        try:
            for frame in frames:
                positions, _ = self._evaluated_points(frame)
                if len(positions):
                    minimum = np.minimum(minimum, positions.min(axis=0))
                    maximum = np.maximum(maximum, positions.max(axis=0))
                if surface is not None:
                    vertices, _faces = self._evaluated_surface(frame)
                    if len(vertices):
                        minimum = np.minimum(minimum, vertices.min(axis=0))
                        maximum = np.maximum(maximum, vertices.max(axis=0))
        finally:
            scene.frame_set(original)
        if not np.isfinite(minimum).all():
            raise ValueError("The liquid particles have no points in the requested frames")
        pad = 0.05 * float(max(maximum - minimum).max()) + 1e-3
        self.minimum, self.maximum = minimum - pad, maximum + pad
        self.cache_matrix = np.eye(4)
        self.cache_matrix[:3, 3] = self.minimum
        self.identity = {
            "mode": self.mode, "cache": str(self.root.resolve()), "provider": provider,
            "particles": particles.name, "surface": surface.name if surface else None,
            "sources": [], "coordinate_recipe": "evaluated-points-world-v1",
            "surface_recipe": SURFACE_RECIPE, "velocity_per_second": self.velocity_scale,
            "baked_fps": self.fps, "time_scale": 1.0, "baked_world_scale": 1.0,
            "sampling": "all-liquid-particles", "provider_version": bpy.app.version_string,
            "source_object_id": getattr(getattr(particles, "cloth_next", None), "uuid", "") or particles.name,
            "cache_matrix": self.cache_matrix.tolist(),
            "bounds": [self.minimum.tolist(), self.maximum.tolist()],
            "frames": [frames[0], frames[-1]] if frames else [],
        }
        if hash_sources:
            for path in self.source_paths:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                self.identity["sources"].append([path.name, digest.hexdigest()])
        if surface is not None:
            self.ensure_surfaces(frames)

    def _evaluated(self, obj, frame):
        self.context.scene.frame_set(frame)
        evaluated = obj.evaluated_get(self.context.evaluated_depsgraph_get())
        return evaluated, evaluated.to_mesh()

    def _evaluated_points(self, frame):
        evaluated, mesh = self._evaluated(self.particles, frame)
        try:
            count = len(mesh.vertices)
            positions = np.empty(count * 3, np.float64)
            mesh.vertices.foreach_get("co", positions)
            attribute = velocity_attribute(mesh) if count else None
            if count and attribute is None:
                raise ValueError(f"'{self.particles.name}' has no point velocity attribute "
                                 f"({', '.join(VELOCITY_ATTRIBUTES[:3])}); export velocity from "
                                 "Houdini/LiquiGen and read it in the Mesh Sequence Cache")
            velocities = np.empty(count * 3, np.float64)
            if count:
                attribute.data.foreach_get("vector", velocities)
        finally:
            evaluated.to_mesh_clear()
        matrix = np.asarray(self.particles.matrix_world, float)
        positions = positions.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        # Velocities are world vectors per second, like FLIP's export; an object
        # scale/rotation applies to them as to the positions.
        velocities = velocities.reshape(-1, 3) @ matrix[:3, :3].T * self.velocity_scale
        if not (np.isfinite(positions).all() and np.isfinite(velocities).all()):
            raise ValueError(f"Liquid particles are not finite at frame {frame}")
        return positions, velocities

    def _evaluated_surface(self, frame):
        evaluated, mesh = self._evaluated(self.surface, frame)
        try:
            mesh.calc_loop_triangles()
            vertices = np.empty(len(mesh.vertices) * 3, np.float64)
            mesh.vertices.foreach_get("co", vertices)
            faces = np.empty(len(mesh.loop_triangles) * 3, np.int64)
            mesh.loop_triangles.foreach_get("vertices", faces)
        finally:
            evaluated.to_mesh_clear()
        matrix = np.asarray(self.surface.matrix_world, float)
        return vertices.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3], faces.reshape(-1, 3)

    def ensure_surfaces(self, frames):
        manifest_path = self.root / "surfaces.json"
        stamps = [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in self.source_paths]
        wanted = {"recipe": SURFACE_RECIPE, "surface": self.surface.name, "sources": stamps,
                  "frames": [frames[0], frames[-1]] if frames else [],
                  "minimum": self.minimum.tolist(), "maximum": self.maximum.tolist()}
        try:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if current == wanted and all((self.root / f"{f:06d}.bobj").is_file() for f in frames):
            return
        scene = self.context.scene
        original = scene.frame_current
        dx = float(max(self.maximum - self.minimum)) / 256.0
        try:
            for frame in frames:
                vertices, faces = self._evaluated_surface(frame)
                if not len(faces):
                    raise ValueError(f"The liquid surface is empty at frame {frame}")
                mantaflow.write_bobj(self.root / f"{frame:06d}.bobj", vertices, faces)
                mantaflow.write_bounds(self.root / f"bounds{frame:06d}.bbox", self.minimum, self.maximum, dx)
        finally:
            scene.frame_set(original)
        mantaflow._atomic(manifest_path, json.dumps(wanted, sort_keys=True).encode())

    def sample(self, frame):
        positions, velocities = self._evaluated_points(frame)
        if not len(positions):
            raise ValueError(f"No liquid particles at frame {frame}")
        return positions, velocities
