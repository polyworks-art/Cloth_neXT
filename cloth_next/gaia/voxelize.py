# SPDX-License-Identifier: GPL-3.0-or-later
"""Inside/outside voxelisation of a closed FLIP surface, NumPy only.

Each axis casts one ray per voxel column and counts surface crossings below
every voxel centre (parity). A surface hole only corrupts the columns that pass
through it, so the three axis votes are combined by majority: a defect must hit
the same voxel from two directions to change it. FLIP surfaces with a few open
or non-manifold edges therefore voxelise like their watertight neighbours.
"""
import numpy as np

# Column centres are offset by an irrational fraction of a cell so rays never
# pass exactly through shared triangle edges or vertices (no double counts).
_JITTER = (0.000137, 0.000291)


def _axis_parity(triangles, lo, cell, dims, axis):
    """Return a bool grid (dims order x,y,z) of parity along ``axis``."""
    b, c = [k for k in range(3) if k != axis]
    nb, nc, na = dims[b], dims[c], dims[axis]
    pb = (triangles[:, :, b] - lo[b]) / cell[b] - .5 - _JITTER[0]
    pc = (triangles[:, :, c] - lo[c]) / cell[c] - .5 - _JITTER[1]
    i0 = np.maximum(np.ceil(pb.min(1)), 0).astype(np.int64)
    i1 = np.minimum(np.floor(pb.max(1)), nb - 1).astype(np.int64)
    j0 = np.maximum(np.ceil(pc.min(1)), 0).astype(np.int64)
    j1 = np.minimum(np.floor(pc.max(1)), nc - 1).astype(np.int64)
    wi, wj = i1 - i0 + 1, j1 - j0 + 1
    count = np.where((wi > 0) & (wj > 0), wi * wj, 0)
    counts = np.zeros((nb * nc, na + 1), np.uint8)
    if not count.any():
        return np.zeros(dims, bool)
    tri = np.repeat(np.arange(len(triangles)), count)
    local = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    ci = i0[tri] + local % wi[tri]
    cj = j0[tri] + local // wi[tri]
    # 2D barycentric coordinates of the column centre in the projected triangle.
    x0, x1, x2 = pb[tri, 0], pb[tri, 1], pb[tri, 2]
    y0, y1, y2 = pc[tri, 0], pc[tri, 1], pc[tri, 2]
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    u = ((x1 - ci) * (y2 - cj) - (x2 - ci) * (y1 - cj)) / np.where(det == 0, 1, det)
    v = ((x2 - ci) * (y0 - cj) - (x0 - ci) * (y2 - cj)) / np.where(det == 0, 1, det)
    w = 1 - u - v
    hit = (det != 0) & (u >= 0) & (v >= 0) & (w >= 0)
    za = triangles[tri, :, axis]
    t = u * za[:, 0] + v * za[:, 1] + w * za[:, 2]
    # First voxel whose centre lies beyond the crossing.
    k = np.clip(np.ceil((t - lo[axis]) / cell[axis] - .5), 0, na).astype(np.int64)
    np.add.at(counts, (cj[hit] * nb + ci[hit], k[hit]), 1)
    parity = (np.cumsum(counts, axis=1, dtype=np.uint8)[:, :na] & 1).astype(bool)
    grid = parity.reshape(nc, nb, na)  # index order (c, b, axis)
    order = {c: 0, b: 1, axis: 2}
    return grid.transpose(order[0], order[1], order[2])


def inside_voxels(vertices, faces, lo, cell, dims):
    """Voxel-centre occupancy as a bool array indexed ``[z, y, x]``."""
    vertices = np.asarray(vertices, np.float64)
    faces = np.asarray(faces, np.int64)
    lo = np.asarray(lo, np.float64)
    cell = np.asarray(cell, np.float64)
    dims = tuple(int(n) for n in dims)
    if len(faces) == 0:
        return np.zeros(dims[::-1], bool)
    triangles = vertices[faces]
    votes = sum(_axis_parity(triangles, lo, cell, dims, axis).astype(np.uint8)
                for axis in range(3))
    return (votes >= 2).transpose(2, 1, 0)


def surface_defects(faces):
    """Open (boundary) and non-manifold edge counts of a triangle surface."""
    faces = np.asarray(faces, np.int64)
    if len(faces) == 0:
        return 0, 0
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum()), int((counts > 2).sum())
