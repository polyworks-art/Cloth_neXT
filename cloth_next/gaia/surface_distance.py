# SPDX-License-Identifier: GPL-3.0-or-later
"""Signed distance to a liquid surface mesh at the nodes of a regular grid, NumPy only.

Particle support (``influence``) is dilated by the trilinear splat: next to a
liquid surface it saturates up to about one grid spacing *outside* the
liquid, so anything that thresholds it sees the water line one cell too
high. The liquid surface mesh knows where the water really ends.

Each axis casts one ray per node line and records where it crosses the
surface. Inside/outside is the majority of the three parity votes (as in
``voxelize``), so a few open or non-manifold edges do not flip a node. The
distance combines the three axis distances to the nearest crossing as
``1/sqrt(sum 1/d_axis^2)``, which is exact for a locally flat surface.
"""
import numpy as np

# Node lines are offset by an irrational fraction of a cell so rays never pass
# exactly through shared triangle edges or vertices (no double counts).
_JITTER = (0.000137, 0.000291)
# Distances are only resolved this many cells from the surface; beyond that
# a node is plainly inside or outside.
BAND_CELLS = 2.0


def _axis_crossings(triangles, origin, spacing, shape, axis):
    """Parity (bool) and distance in cells to the nearest crossing (float) along ``axis``.

    ``shape`` is (nx, ny, nz); results are indexed (x, y, z).
    """
    b, c = [k for k in range(3) if k != axis]
    nb, nc, na = shape[b], shape[c], shape[axis]
    pb = (triangles[:, :, b] - origin[b]) / spacing[b] - _JITTER[0]
    pc = (triangles[:, :, c] - origin[c]) / spacing[c] - _JITTER[1]
    i0 = np.maximum(np.ceil(pb.min(1)), 0).astype(np.int64)
    i1 = np.minimum(np.floor(pb.max(1)), nb - 1).astype(np.int64)
    j0 = np.maximum(np.ceil(pc.min(1)), 0).astype(np.int64)
    j1 = np.minimum(np.floor(pc.max(1)), nc - 1).astype(np.int64)
    wi, wj = i1 - i0 + 1, j1 - j0 + 1
    count = np.where((wi > 0) & (wj > 0), wi * wj, 0)
    parity = np.zeros((nc * nb, na), bool)
    distance = np.full((nc * nb, na), np.inf)
    if count.any():
        tri = np.repeat(np.arange(len(triangles)), count)
        local = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
        ci = i0[tri] + local % wi[tri]
        cj = j0[tri] + local // wi[tri]
        x0, x1, x2 = pb[tri, 0], pb[tri, 1], pb[tri, 2]
        y0, y1, y2 = pc[tri, 0], pc[tri, 1], pc[tri, 2]
        det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        safe = np.where(det == 0, 1, det)
        u = ((x1 - ci) * (y2 - cj) - (x2 - ci) * (y1 - cj)) / safe
        v = ((x2 - ci) * (y0 - cj) - (x0 - ci) * (y2 - cj)) / safe
        w = 1 - u - v
        hit = (det != 0) & (u >= 0) & (v >= 0) & (w >= 0)
        za = (triangles[tri[hit], :, axis] - origin[axis]) / spacing[axis]
        t = u[hit] * za[:, 0] + v[hit] * za[:, 1] + w[hit] * za[:, 2]
        column = cj[hit] * nb + ci[hit]
        # Crossings far beyond the block only matter for parity; clipping
        # keeps their order relative to every node.
        pad = BAND_CELLS + 3
        t = np.clip(t, -pad, na - 1 + pad)
        width = na - 1 + 2 * pad + 1
        keys = np.sort(column * width + (t + pad))
        nodes = np.arange(na, dtype=float) + pad
        node_keys = (np.arange(nc * nb)[:, None] * width + nodes[None, :]).ravel()
        position = np.searchsorted(keys, node_keys)
        column_start = np.searchsorted(keys, np.arange(nc * nb) * width)
        parity = ((position.reshape(nc * nb, na) - column_start[:, None]) & 1).astype(bool)
        node_column = np.repeat(np.arange(nc * nb), na)
        best = np.full(node_keys.shape, np.inf)
        for neighbour in (position - 1, position):
            valid = (neighbour >= 0) & (neighbour < len(keys))
            index = np.clip(neighbour, 0, max(len(keys) - 1, 0))
            same = valid & (np.floor(keys[index] / width) == node_column)
            gap = np.abs(keys[index] - node_keys)
            best = np.where(same, np.minimum(best, gap), best)
        distance = best.reshape(nc * nb, na)
    order = {c: 0, b: 1, axis: 2}
    transpose = (order[0], order[1], order[2])
    return (parity.reshape(nc, nb, na).transpose(transpose),
            distance.reshape(nc, nb, na).transpose(transpose))


def signed_distance(vertices, faces, origin, spacing, shape):
    """Signed distance in metres at grid nodes, positive inside the liquid.

    ``origin`` is node (0, 0, 0), ``shape`` is (nz, ny, nx) like the GAIA
    arrays. Magnitudes are clamped to ``BAND_CELLS`` mean spacings.
    """
    vertices = np.asarray(vertices, np.float64)
    faces = np.asarray(faces, np.int64)
    origin = np.asarray(origin, np.float64)
    spacing = np.asarray(spacing, np.float64)
    nz, ny, nx = (int(n) for n in shape)
    cap = BAND_CELLS * float(spacing.mean())
    if len(faces) == 0:
        return np.full((nz, ny, nx), -cap, np.float32)
    triangles = vertices[faces]
    size = np.array([nx, ny, nz])
    votes = np.zeros((nx, ny, nz), np.uint8)
    inverse_square = np.zeros((nx, ny, nz))
    for axis in range(3):
        # Parity needs every crossing along the line, so only the two other
        # axes may cull triangles.
        others = [k for k in range(3) if k != axis]
        low = origin[others] - spacing[others]
        high = origin[others] + spacing[others] * size[others]
        span = triangles[:, :, others]
        keep = np.all((span.max(1) >= low) & (span.min(1) <= high), axis=1)
        parity, cells = _axis_crossings(triangles[keep], origin, spacing, (nx, ny, nz), axis)
        votes += parity
        metres = cells * spacing[axis]
        inverse_square += np.where(np.isfinite(metres), 1.0 / np.maximum(metres, 1e-12) ** 2, 0.0)
    distance = np.where(inverse_square > 0, 1.0 / np.sqrt(np.maximum(inverse_square, 1e-300)), cap)
    distance = np.minimum(distance, cap)
    signed = np.where(votes >= 2, distance, -distance)
    return signed.transpose(2, 1, 0).astype(np.float32)


def contact_support(signed, spacing):
    """Influence-like 0..1 field whose 0.5 level is the liquid surface.

    Linear in the signed distance across one spacing, so trilinear sampling
    puts the half-contact point on the surface itself.
    """
    h = float(np.asarray(spacing, float).mean())
    return np.clip(0.5 + np.asarray(signed, float) / (2.0 * h), 0.0, 1.0).astype(np.float32)
