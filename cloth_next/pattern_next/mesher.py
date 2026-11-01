# SPDX-License-Identifier: GPL-3.0-or-later
"""Constrained interior meshing for cloth patterns.

Why not just ``delaunay_2d_cdt``? Blender's constrained Delaunay is an
excellent, exact triangulator, but it only connects the points it is given:
a rectangle comes back as two triangles. A cloth panel needs interior
particles at roughly Particle Distance. This mesher therefore

1. resamples every boundary/hole/internal line at Particle Distance with a
   stable ``(segment uid, fraction)`` source per vertex (:mod:`.curves`);
2. validates the resampled rings (:mod:`.validation`);
3. seeds interior vertices on a hexagonal lattice of the same spacing,
   anchored at the pattern origin so edits do not shuffle the interior,
   keeping only points inside with at least ``CLEARANCE * spacing`` to any
   constraint;
4. triangulates with constraints (the injected CDT: Blender's in the add-on,
   a pure fallback in headless tests), keeps triangles whose centroid is
   inside (even-odd, so holes are respected), relaxes interior vertices
   with an inversion guard and triangulates once more.

The result is boundary-conforming, consistently counter-clockwise (normal
facing the front view after the canvas-to-XZ mapping) and checked for area
conservation; anything else raises :class:`PatternError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time

import numpy as np

from .curves import resample
from .shapes import PatternShape
from .validation import PatternError, point_in_rings, validate_shape

CLEARANCE = 0.6
SMOOTH_ITERATIONS = 3
MAX_VERTICES = 400_000


@dataclass
class BoundaryLoop:
    kind: str            # "OUTLINE", "HOLE" or "LINE"
    index: int           # hole/line number (0 for the outline)
    vertices: list       # mesh vertex indices in contour order
    sources: list        # BoundarySample per vertex


@dataclass
class MeshResult:
    vertices: np.ndarray
    triangles: np.ndarray
    loops: list
    stats: dict = field(default_factory=dict)

    @property
    def outline(self):
        return self.loops[0]


def _ring_area(points):
    p = np.asarray(points)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _segment_arrays(chains):
    """Constraint segments as (s, 2, 2) from rings (closed) and lines (open)."""
    pieces = []
    for points, closed in chains:
        p = np.asarray(points, dtype=np.float64)
        if len(p) < 2:
            continue
        q = np.roll(p, -1, axis=0) if closed else p[1:]
        pieces.append(np.stack((p if closed else p[:-1], q), axis=1))
    return np.concatenate(pieces) if pieces else np.zeros((0, 2, 2))


def _min_distance(points, segments):
    result = np.full(len(points), np.inf)
    if not len(points) or not len(segments):
        return result
    a, b = segments[:, 0], segments[:, 1]
    d = b - a
    dd = np.maximum((d * d).sum(axis=1), 1e-30)
    for start in range(0, len(points), 2048):
        p = points[start:start + 2048]
        rel = p[:, None, :] - a[None, :, :]
        t = np.clip((rel * d[None]).sum(axis=2) / dd[None], 0.0, 1.0)
        closest = rel - t[..., None] * d[None]
        result[start:start + 2048] = np.sqrt((closest * closest).sum(axis=2).min(axis=1))
    return result


def lattice(rings, chains, spacing, origin=(0.0, 0.0)):
    """Hexagonal interior seed points with boundary clearance."""
    outline = np.asarray(rings[0])
    lo, hi = outline.min(axis=0), outline.max(axis=0)
    row = spacing * math.sqrt(3.0) / 2.0
    j0 = math.floor((lo[1] - origin[1]) / row) - 1
    j1 = math.ceil((hi[1] - origin[1]) / row) + 1
    i0 = math.floor((lo[0] - origin[0]) / spacing) - 1
    i1 = math.ceil((hi[0] - origin[0]) / spacing) + 1
    estimate = (j1 - j0 + 1) * (i1 - i0 + 1)
    if estimate > MAX_VERTICES * 2:
        raise PatternError("TOO_DENSE", "Particle Distance is too small for this pattern "
                                        f"(about {estimate:,} particles)")
    jj, ii = np.meshgrid(np.arange(j0, j1 + 1), np.arange(i0, i1 + 1), indexing="ij")
    x = origin[0] + (ii + 0.5 * (jj % 2)) * spacing
    y = origin[1] + jj * row
    points = np.stack((x.ravel(), y.ravel()), axis=1)
    inside = point_in_rings(points[:, 0], points[:, 1], rings)
    points = points[inside]
    clearance = _min_distance(points, _segment_arrays(chains))
    return points[clearance >= CLEARANCE * spacing]


def _filter(points, triangles, rings):
    if not len(triangles):
        return triangles
    centres = points[triangles].mean(axis=1)
    keep = point_in_rings(centres[:, 0], centres[:, 1], rings)
    return triangles[keep]


def _orient(points, triangles):
    a, b, c = (points[triangles[:, k]] for k in range(3))
    area = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
                  - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    flip = area < 0
    triangles = triangles.copy()
    triangles[flip] = triangles[flip][:, [0, 2, 1]]
    return triangles, np.abs(area)


def _signed_areas(points, triangles):
    a, b, c = (points[triangles[:, k]] for k in range(3))
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
                  - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


def _smooth(points, triangles, movable, iterations=SMOOTH_ITERATIONS):
    """Laplacian relaxation of interior vertices; never inverts a triangle."""
    if not movable.any() or not len(triangles):
        return points
    n = len(points)
    edges = np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]))
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    i, j = edges[:, 0], edges[:, 1]
    degree = np.bincount(np.concatenate((i, j)), minlength=n).astype(np.float64)
    reference = np.sign(_signed_areas(points, triangles))
    for _ in range(iterations):
        total = np.zeros_like(points)
        np.add.at(total, i, points[j])
        np.add.at(total, j, points[i])
        mean = total / np.maximum(degree, 1)[:, None]
        candidate = points.copy()
        candidate[movable] = points[movable] * 0.4 + mean[movable] * 0.6
        for _guard in range(8):
            areas = _signed_areas(candidate, triangles)
            bad = np.sign(areas) != reference
            bad |= np.abs(areas) < 1e-14
            if not bad.any():
                break
            revert = np.unique(triangles[bad])
            candidate[revert] = points[revert]
        points = candidate
    return points


def remove_slivers(points, triangles, spacing):
    """Flip away zero-area triangles made of three almost collinear points.

    Boundary samples on a slanted straight edge are not exactly collinear in
    floating point, so an exact CDT may connect three consecutive samples
    into a sliver of ~1e-18 m². Flipping the sliver's longest edge with its
    neighbour removes it without moving any vertex or constraint.
    """
    threshold = 1e-9 * spacing * spacing
    triangles = [tuple(map(int, t)) for t in triangles]
    initial = int((np.abs(_signed_areas(points, np.asarray(triangles, dtype=np.int64)
                                        .reshape(-1, 3))) <= threshold).sum())
    for _round in range(4 * initial + 1):
        areas = _signed_areas(points, np.asarray(triangles, dtype=np.int64).reshape(-1, 3))
        degenerate = [i for i in np.nonzero(np.abs(areas) <= threshold)[0]]
        if not degenerate:
            break
        index = int(degenerate[0])
        a, b, c = triangles[index]
        edges = ((a, b, c), (b, c, a), (c, a, b))
        u, v, w = max(edges, key=lambda e: float(np.linalg.norm(points[e[0]] - points[e[1]])))
        partner = None
        for j, t in enumerate(triangles):
            if j != index and u in t and v in t:
                partner = j
                break
        if partner is None:
            del triangles[index]  # sliver on the open hull: nothing references its long edge
            continue
        x = next(k for k in triangles[partner] if k not in (u, v))
        first, second = sorted((index, partner), reverse=True)
        del triangles[first]
        del triangles[second]
        triangles += [(u, w, x), (w, v, x)]
    return np.asarray(triangles, dtype=np.int64).reshape(-1, 3)


def triangle_quality(points, triangles):
    """Minimum interior angle (degrees) per triangle."""
    a, b, c = (points[triangles[:, k]] for k in range(3))
    def angle(p, q, r):
        u, v = q - p, r - p
        cos = (u * v).sum(axis=1) / np.maximum(
            np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-30)
        return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    return np.minimum(np.minimum(angle(a, b, c), angle(b, c, a)), angle(c, a, b))


def mesh_shape(shape: PatternShape, cdt):
    """Mesh ``shape``.

    ``cdt(points (n,2), edges, outline_indices) -> (m,3) input indices`` must
    return a constrained triangulation covering (at least) the outline.
    """
    started = time.perf_counter()
    spacing, tolerance = float(shape.spacing), float(shape.tolerance)
    if not (spacing > 0 and math.isfinite(spacing)):
        raise PatternError("INVALID_SPACING", "Particle Distance must be positive")
    if not shape.outline.closed:
        raise PatternError("OPEN_CONTOUR", "The pattern outline is not closed")
    outline = resample(shape.outline, spacing, tolerance)
    holes = [resample(hole, spacing, tolerance) for hole in shape.holes]
    lines = [resample(line, spacing, tolerance) for line in shape.lines]
    rings = [[s.co for s in outline]] + [[s.co for s in hole] for hole in holes]
    line_points = [[s.co for s in line] for line in lines]
    validate_shape(rings[0], rings[1:], line_points)
    chains = [(ring, True) for ring in rings] + [(line, False) for line in line_points]
    resampled = time.perf_counter()

    loops, coords, edges = [], [], []
    for kind, samples_list, closed in (("OUTLINE", [outline], True),
                                        ("HOLE", holes, True),
                                        ("LINE", lines, False)):
        for number, samples in enumerate(samples_list):
            start = len(coords)
            indices = list(range(start, start + len(samples)))
            coords.extend(s.co for s in samples)
            pairs = list(zip(indices, indices[1:]))
            if closed:
                pairs.append((indices[-1], indices[0]))
            edges.extend(pairs)
            loops.append(BoundaryLoop(kind, number, indices, list(samples)))
    fixed = len(coords)
    interior = lattice(rings, chains, spacing)
    if fixed + len(interior) > MAX_VERTICES:
        raise PatternError("TOO_DENSE", "Particle Distance is too small for this pattern "
                                        f"({fixed + len(interior):,} particles)")
    points = np.concatenate((np.asarray(coords, dtype=np.float64), interior.reshape(-1, 2)))
    seeded = time.perf_counter()

    outline_face = loops[0].vertices
    triangles = _filter(points, np.asarray(cdt(points, edges, outline_face),
                                           dtype=np.int64).reshape(-1, 3), rings)
    movable = np.zeros(len(points), dtype=bool)
    movable[fixed:] = True
    first_pass = time.perf_counter()
    points = _smooth(points, triangles, movable)
    triangles = _filter(points, np.asarray(cdt(points, edges, outline_face),
                                           dtype=np.int64).reshape(-1, 3), rings)
    triangles = remove_slivers(points, triangles, spacing)
    triangles, areas = _orient(points, triangles)
    triangulated = time.perf_counter()

    if not len(triangles):
        raise PatternError("TRIANGULATION_FAILED", "The pattern produced no triangles")
    if (areas <= 1e-14).any():
        raise PatternError("TRIANGULATION_FAILED", "The triangulation contains a zero-area triangle")
    expected = abs(_ring_area(rings[0])) - sum(abs(_ring_area(r)) for r in rings[1:])
    covered = float(areas.sum())
    if abs(covered - expected) > max(1e-9, 1e-7 * expected):
        raise PatternError("TRIANGULATION_FAILED",
                           f"Triangulation covers {covered:.6f} m² of {expected:.6f} m²")
    used = np.zeros(len(points), dtype=bool)
    used[triangles.ravel()] = True
    if not used[:fixed].all():
        raise PatternError("TRIANGULATION_FAILED", "A boundary vertex is not part of the mesh")
    if not used.all():  # an interior seed the CDT did not connect: drop it
        remap = np.cumsum(used) - 1
        points, triangles = points[used], remap[triangles]
    quality = triangle_quality(points, triangles)
    finished = time.perf_counter()
    stats = {
        "vertices": int(len(points)), "triangles": int(len(triangles)),
        "boundary_vertices": int(len(outline)), "interior_vertices": int(len(points) - fixed),
        "area": covered, "expected_area": expected,
        "min_angle": float(quality.min()), "mean_min_angle": float(quality.mean()),
        "skinny_ratio": float((quality < 20.0).mean()),
        "time_resample": resampled - started, "time_seed": seeded - resampled,
        "time_triangulate": triangulated - seeded, "time_first_pass": first_pass - seeded,
        "time_total": finished - started,
    }
    return MeshResult(points, triangles, loops, stats)
