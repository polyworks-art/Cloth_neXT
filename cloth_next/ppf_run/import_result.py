# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Convert validated solver frames into the Blender playback cache (pure).

Solver positions arrive in PPF world space (Y-up). They are converted into
the cloth object's Blender-local space via ``(Z2Y @ matrix_world)^-1``
(the audited upstream import path) and written as one PC2 file:

- PC2 sample 0 = Blender frame 1 = the exported initial cloth state,
- PC2 sample N = Blender frame N+1 = solver frame ``vert_<N>.bin``.

The Mesh Cache modifier is configured with ``frame_start = 1`` so the
timeline maps 1:1 onto the samples.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..bake.pc2 import Pc2Header, write_pc2
from ..ppf.coordinates import solver_world_to_object_local, transform_points
from .session import SolverFrame

PC2_START_FRAME = 0.0
PC2_SAMPLE_RATE = 1.0
MODIFIER_NAME = "Cloth NeXt Test Cache"


class ImportValidationError(ValueError):
    pass


def build_playback_frames(
        initial_local: Sequence[Sequence[float]],
        solver_frames: Sequence[SolverFrame],
        blender_world_matrix: Sequence[Sequence[float]],
        *, expected_frame_count: int,
) -> list[list[tuple[float, float, float]]]:
    """Validated local-space playback frames, original vertex order."""
    vertex_count = len(initial_local)
    if vertex_count == 0:
        raise ImportValidationError("initial cloth state has no vertices")
    ordered = sorted(solver_frames, key=lambda item: item.solver_frame)
    expected_solver = list(range(1, expected_frame_count))
    got = [frame.solver_frame for frame in ordered]
    if got != expected_solver:
        raise ImportValidationError(
            f"incomplete or duplicate solver frames: expected "
            f"{expected_solver}, got {got}")
    to_local = solver_world_to_object_local(blender_world_matrix)
    playback = [[(float(p[0]), float(p[1]), float(p[2]))
                 for p in initial_local]]
    for frame in ordered:
        if len(frame.positions_solver_world) != vertex_count:
            raise ImportValidationError(
                f"solver frame {frame.solver_frame} has "
                f"{len(frame.positions_solver_world)} vertices, expected "
                f"{vertex_count} (constant topology)")
        playback.append(transform_points(to_local,
                                         frame.positions_solver_world))
    return playback


def write_playback_cache(path: Path,
                         frames: Sequence[Sequence[Sequence[float]]]
                         ) -> Pc2Header:
    return write_pc2(path, frames, start_frame=PC2_START_FRAME,
                     sample_rate=PC2_SAMPLE_RATE)
