# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender Mantaflow liquid as a GAIA Water Flow source.

Same interface as ``FLIPWaterFlowProvider``: identity, source_paths, bounds,
``sample(frame) -> (world positions, world m/s velocities)``. The Mantaflow
cache is only read. Its liquid surface is written once per frame, in world
metres, into an owned FLIP-style folder next to the .gaia file, so GAIA's
Hydrodynamic Pressure preparation reads Mantaflow exactly like FLIP.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy
import numpy as np

from ..gaia import mantaflow

SURFACE_RECIPE = "mantaflow-evaluated-surface-v1"


def fluid_domain(obj):
    """The Mantaflow domain settings of ``obj``, or None."""
    for modifier in getattr(obj, "modifiers", ()):
        if modifier.type == "FLUID" and modifier.fluid_type == "DOMAIN":
            return modifier.domain_settings
    return None


def is_liquid_domain(obj) -> bool:
    settings = fluid_domain(obj)
    return settings is not None and settings.domain_type == "LIQUID"


def domain_box(domain):
    """World (minimum, maximum) of the domain box from its original mesh.

    ``bound_box``/``dimensions`` describe the evaluated object, which for a
    baked liquid domain is the liquid surface, not the simulation grid.
    """
    count = len(domain.data.vertices)
    if count == 0:
        raise ValueError("The Mantaflow Domain mesh is empty")
    local = np.empty(count * 3, np.float64)
    domain.data.vertices.foreach_get("co", local)
    local = local.reshape(-1, 3)
    matrix = np.asarray(domain.matrix_world, float)
    world = local @ matrix[:3, :3].T + matrix[:3, 3]
    return world.min(axis=0), world.max(axis=0)


def surface_folder(container) -> Path:
    """Owned folder of world-space surfaces beside the .gaia container."""
    path = Path(bpy.path.abspath(str(container)))
    return path.with_name(path.stem + "_mantaflow_surface")


def cache_folder(settings) -> Path:
    return Path(bpy.path.abspath(settings.cache_directory))


def surface_frame_path(root: Path, frame: int) -> Path:
    return root / f"{frame:06d}.bobj"


class MantaflowWaterFlowProvider:
    def __init__(self, context, domain, frames, *, container, hash_sources=True):
        settings = fluid_domain(domain)
        if settings is None or settings.domain_type != "LIQUID":
            raise ValueError("Select a baked Mantaflow Liquid Domain")
        self.context, self.domain, self.settings = context, domain, settings
        frames = tuple(frames)
        self.frame_count = len(frames)
        if getattr(settings, "is_cache_baking_any", False):
            raise ValueError("Mantaflow is still baking; finish the bake before Water Flow")
        if not settings.has_cache_baked_data:
            raise ValueError("The Mantaflow liquid is not baked; bake its Data first")
        if settings.cache_data_format != "OPENVDB":
            raise ValueError("Set the Mantaflow Cache Data format to OpenVDB and bake again")
        if not settings.use_mesh or not settings.has_cache_baked_mesh:
            raise ValueError("Enable Mesh on the Mantaflow Liquid Domain and bake the Mesh too; "
                             "GAIA needs the liquid surface")
        if abs(settings.time_scale - 1.0) > 1e-9:
            raise ValueError("Mantaflow Time Scale must be 1 for Water Flow; retiming is not supported yet")
        if getattr(settings, "use_adaptive_domain", False):
            raise ValueError("Switch off Adaptive Domain on the Mantaflow Domain and bake again")
        if abs(context.scene.unit_settings.scale_length - 1.0) > 1e-9:
            raise ValueError("Water Flow needs Scene Unit Scale 1 (metres)")
        matrix = np.asarray(domain.matrix_world, float)
        if not np.isfinite(matrix).all() or not np.allclose(matrix[:3, :3], np.eye(3), atol=1e-6):
            raise ValueError("Apply Rotation and Scale on the Mantaflow Domain (Ctrl+A) and bake again")
        start, end = int(settings.cache_frame_start), int(settings.cache_frame_end)
        if frames and (frames[0] < start or frames[-1] > end):
            raise ValueError(f"The Mantaflow cache covers frames {start}-{end}; "
                             f"Water Flow needs {frames[0]}-{frames[-1]}")
        self.cache = cache_folder(settings)
        # The evaluated domain is the liquid mesh; the grid is the original box.
        self.minimum, self.maximum = domain_box(domain)
        self.dx = float(max(self.maximum - self.minimum)) / float(settings.resolution_max)
        self.grid_minimum = self.minimum.copy()
        self.cache_matrix = np.eye(4)
        self.cache_matrix[:3, 3] = self.minimum
        self.stream_bounds = {"dx": self.dx}
        self.root = surface_folder(container)
        self.mode = "FLUID_PARTICLES"
        self.source_paths = []
        for frame in frames:
            data = mantaflow.data_file(self.cache, frame)
            mesh = self.cache / "mesh" / f"fluid_mesh_{frame:04d}.bobj.gz"
            for path in (data, mesh):
                if not path.is_file():
                    raise ValueError(f"Mantaflow frame {frame} is not baked: {path.name} is missing")
                self.source_paths.append(path)
        self.identity = {
            "mode": self.mode, "cache": str(self.root.resolve()), "provider": "Mantaflow",
            "mantaflow_cache": str(self.cache.resolve()), "domain": domain.name,
            "matrix": matrix.tolist(), "sources": [],
            "coordinate_recipe": "mantaflow-vdb-world-v1", "surface_recipe": SURFACE_RECIPE,
            "baked_fps": context.scene.render.fps / context.scene.render.fps_base,
            "time_scale": 1.0, "baked_world_scale": 1.0, "dx": self.dx,
            "resolution_max": int(settings.resolution_max),
            "sampling": f"liquid-cells-{mantaflow.SUBSAMPLES}x{mantaflow.SUBSAMPLES}x{mantaflow.SUBSAMPLES}",
            "provider_version": bpy.app.version_string,
            "source_object_id": getattr(getattr(domain, "cloth_next", None), "uuid", "") or domain.name,
            "cache_matrix": self.cache_matrix.tolist(),
            "bounds": [self.minimum.tolist(), self.maximum.tolist()],
        }
        if hash_sources:
            for path in self.source_paths:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                self.identity["sources"].append([path.name, digest.hexdigest()])
        self._surfaces: dict[int, tuple] = {}
        self.ensure_surfaces(frames)

    # The liquid surface -------------------------------------------------------------------
    def _surface_manifest(self, frames) -> dict:
        stamps = [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in self.source_paths]
        return {"recipe": SURFACE_RECIPE, "frames": [frames[0], frames[-1]] if frames else [],
                "minimum": self.minimum.tolist(), "maximum": self.maximum.tolist(), "dx": self.dx,
                "sources": stamps}

    def ensure_surfaces(self, frames) -> None:
        """Write the world-space surface and bounds of every frame (once per bake)."""
        manifest_path = self.root / "surfaces.json"
        wanted = self._surface_manifest(frames)
        try:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if current == wanted and all(surface_frame_path(self.root, f).is_file() for f in frames):
            return
        scene = self.context.scene
        original = scene.frame_current
        try:
            for frame in frames:
                vertices, faces = self._evaluated_surface(frame)
                mantaflow.write_bobj(surface_frame_path(self.root, frame), vertices, faces)
                mantaflow.write_bounds(self.root / f"bounds{frame:06d}.bbox",
                                       self.minimum, self.maximum, self.dx)
        finally:
            scene.frame_set(original)
        mantaflow._atomic(manifest_path, json.dumps(wanted, sort_keys=True).encode())

    def _evaluated_surface(self, frame):
        self.context.scene.frame_set(frame)
        evaluated = self.domain.evaluated_get(self.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            count = len(mesh.vertices)
            vertices = np.empty(count * 3, np.float64)
            mesh.vertices.foreach_get("co", vertices)
            faces = np.empty(len(mesh.loop_triangles) * 3, np.int64)
            mesh.loop_triangles.foreach_get("vertices", faces)
        finally:
            evaluated.to_mesh_clear()
        matrix = np.asarray(self.domain.matrix_world, float)
        vertices = vertices.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        if not np.isfinite(vertices).all():
            raise ValueError(f"Mantaflow liquid surface is not finite at frame {frame}")
        return vertices, faces.reshape(-1, 3)

    # GAIA samples ---------------------------------------------------------------------------
    def sample(self, frame):
        from ..gaia.surface import read_surface
        vertices, faces, _digest = read_surface(surface_frame_path(self.root, frame))
        velocity, origin = mantaflow.read_velocity(mantaflow.data_file(self.cache, frame))
        positions, velocities = mantaflow.liquid_samples(
            velocity, origin, grid_minimum=self.grid_minimum, dx=self.dx,
            vertices=vertices, faces=faces)
        if not len(positions):
            raise ValueError(f"No Mantaflow liquid at frame {frame}")
        return positions, velocities
