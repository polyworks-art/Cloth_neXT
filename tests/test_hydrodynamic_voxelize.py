import numpy as np

from cloth_next.gaia.voxelize import inside_voxels, surface_defects


def uv_sphere(radius=.3, centre=(.1, -.05, .2), rings=48, segments=96):
    vertices = [(0., 0., radius)]
    for i in range(1, rings):
        theta = np.pi * i / rings
        for j in range(segments):
            phi = 2 * np.pi * j / segments
            vertices.append((radius * np.sin(theta) * np.cos(phi),
                             radius * np.sin(theta) * np.sin(phi), radius * np.cos(theta)))
    vertices.append((0., 0., -radius))
    faces = []
    for j in range(segments):
        faces.append((0, 1 + j, 1 + (j + 1) % segments))
    for i in range(rings - 2):
        a, b = 1 + i * segments, 1 + (i + 1) * segments
        for j in range(segments):
            k = (j + 1) % segments
            faces += [(a + j, b + j, b + k), (a + j, b + k, a + k)]
    last, base = len(vertices) - 1, 1 + (rings - 2) * segments
    for j in range(segments):
        faces.append((base + j, last, base + (j + 1) % segments))
    return np.asarray(vertices) + centre, np.asarray(faces)


def grid():
    lo = np.array([-.4, -.5, -.3]); cell = np.array([.01, .012, .009]); dims = (80, 90, 100)
    axes = [lo[j] + (np.arange(n) + .5) * cell[j] for j, n in enumerate(dims)]
    z, y, x = np.meshgrid(axes[2], axes[1], axes[0], indexing='ij')
    return lo, cell, dims, (x, y, z)


def test_sphere_occupancy_matches_analytic_volume():
    vertices, faces = uv_sphere()
    lo, cell, dims, (x, y, z) = grid()
    inside = inside_voxels(vertices, faces, lo, cell, dims)
    reference = np.sqrt((x - .1) ** 2 + (y + .05) ** 2 + (z - .2) ** 2) < .3
    assert inside.shape == (100, 90, 80)
    # Only faceting along the surface may differ (well under 1 %).
    assert (inside != reference).sum() < .01 * reference.sum()


def test_small_hole_does_not_leak_through_columns():
    vertices, faces = uv_sphere()
    lo, cell, dims, _ = grid()
    closed = inside_voxels(vertices, faces, lo, cell, dims)
    holed = faces[np.r_[0:200, 230:len(faces)]]
    assert surface_defects(holed)[0] > 0
    assert (inside_voxels(vertices, holed, lo, cell, dims) != closed).sum() < 20


def test_disconnected_parts_and_empty_surface():
    a, fa = uv_sphere(.1, (-.2, -.2, 0.))
    b, fb = uv_sphere(.1, (.2, .2, .2))
    vertices = np.r_[a, b]; faces = np.r_[fa, fb + len(a)]
    lo, cell, dims, (x, y, z) = grid()
    inside = inside_voxels(vertices, faces, lo, cell, dims)
    first = np.sqrt((x + .2) ** 2 + (y + .2) ** 2 + z ** 2) < .1
    second = np.sqrt((x - .2) ** 2 + (y - .2) ** 2 + (z - .2) ** 2) < .1
    assert (inside != (first | second)).sum() < .03 * (first | second).sum()
    assert not inside_voxels(vertices, faces[:0], lo, cell, dims).any()


def test_surface_defect_counts():
    _vertices, faces = uv_sphere()
    assert surface_defects(faces) == (0, 0)
    assert surface_defects(faces[1:]) == (3, 0)
