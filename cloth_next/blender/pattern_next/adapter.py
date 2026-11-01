# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only hand-over of PATTERN NeXt garments to the simulation side.

The simulation path itself is the existing Cloth NeXt pipeline: every panel
is a Cloth NeXt Cloth object and every seam a Cloth NeXt sewing definition,
so Bake / Live Simulation / AETHER read them like any other cloth. This
module is the explicit boundary for the coming AETHER fitting milestone: one
immutable snapshot with the flat rest meshes, UVs, arrangement transforms,
material references and seam vertex correspondence, derived from the
parametric patterns (never from stale mesh indices).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...pattern_next.seams import SeamSide, side_vertices
from . import model, sync


@dataclass(frozen=True)
class PanelSnapshot:
    pattern_id: str
    name: str
    object_name: str
    persistent_id: str
    rest_vertices: np.ndarray     # (n, 3) object-local flat rest shape (metres)
    triangles: np.ndarray         # (m, 3) counter-clockwise
    uv: np.ndarray                # (n, 2) canvas metres
    matrix_world: tuple           # 4x4 arrangement transform (row-major)
    particle_distance: float
    material: str


@dataclass(frozen=True)
class SeamSnapshot:
    seam_id: str
    source_pattern: str
    target_pattern: str
    source_vertices: tuple        # mesh vertex indices along the source side
    target_vertices: tuple
    crossed: bool
    sewing_definition: str        # identifier of the Cloth NeXt sewing row


@dataclass(frozen=True)
class GarmentSnapshot:
    panels: tuple
    seams: tuple


def snapshot(scene):
    panels, results = [], {}
    for pattern in scene.cloth_next_patterns:
        if not sync.object_alive(scene, pattern):
            continue
        result = sync.result_for(scene, pattern)
        results[pattern.identifier] = result
        obj = pattern.mesh_object
        (lx, ly), angle = model.placement(pattern)
        c, s = np.cos(angle), np.sin(angle)
        v = result.vertices
        uv = np.stack((v[:, 0] * c - v[:, 1] * s + lx, v[:, 0] * s + v[:, 1] * c + ly), axis=1)
        rest = np.zeros((len(v), 3))
        rest[:, 0], rest[:, 2] = v[:, 0], v[:, 1]
        panels.append(PanelSnapshot(
            pattern.identifier, pattern.name, obj.name,
            str(obj.cloth_next.persistent_export_id), rest, result.triangles.copy(), uv,
            tuple(tuple(row) for row in obj.matrix_world), float(pattern.particle_distance),
            pattern.material.name if pattern.material else ""))
    seams = []
    for seam in scene.cloth_next_pattern_seams:
        if seam.status or seam.a_pattern not in results or seam.b_pattern not in results:
            continue
        seams.append(SeamSnapshot(
            seam.identifier, seam.a_pattern, seam.b_pattern,
            tuple(side_vertices(results[seam.a_pattern].outline,
                                SeamSide(seam.a_pattern, seam.a_start, seam.a_end))),
            tuple(side_vertices(results[seam.b_pattern].outline,
                                SeamSide(seam.b_pattern, seam.b_start, seam.b_end))),
            bool(seam.flipped), seam.sewing_identifier))
    return GarmentSnapshot(tuple(panels), tuple(seams))
