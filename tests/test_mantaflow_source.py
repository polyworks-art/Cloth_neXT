import json

import numpy as np
import pytest

from cloth_next.gaia import mantaflow
from cloth_next.gaia.surface import read_surface


def cube(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    faces = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                      [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return corners, faces


def test_liquid_samples_fill_the_surface_and_convert_to_m_s():
    dx = 0.1
    velocity = np.zeros((10, 10, 10, 3), np.float32)
    velocity[..., 2] = -4.0  # cells per Mantaflow time unit
    vertices, faces = cube((0.2, 0.2, 0.2), (0.6, 0.6, 0.6))  # 4x4x4 cells
    positions, v = mantaflow.liquid_samples(velocity, (0, 0, 0), grid_minimum=(0, 0, 0), dx=dx,
                                            vertices=vertices, faces=faces)
    assert len(positions) == 4 ** 3 * mantaflow.SUBSAMPLES ** 3
    assert positions.min() > 0.2 and positions.max() < 0.6
    # 4 cells/unit * 0.1 m * 2.5 units/s
    assert np.allclose(v, [0.0, 0.0, -4.0 * dx * 2.5])


def test_liquid_samples_respect_the_grid_offset():
    velocity = np.ones((4, 4, 4, 3), np.float32)
    vertices, faces = cube((1.0, 1.0, 1.0), (1.4, 1.4, 1.4))
    positions, _ = mantaflow.liquid_samples(velocity, (5, 5, 5), grid_minimum=(0.5, 0.5, 0.5), dx=0.1,
                                            vertices=vertices, faces=faces)
    assert len(positions) and positions.min() > 1.0 and positions.max() < 1.4


def test_empty_frames_have_no_samples():
    vertices, faces = cube((0, 0, 0), (1, 1, 1))
    p, v = mantaflow.liquid_samples(np.zeros((0, 0, 0, 3)), (0, 0, 0), grid_minimum=(0, 0, 0), dx=0.1,
                                    vertices=vertices, faces=faces)
    assert p.shape == (0, 3) and v.shape == (0, 3)


def test_surface_and_bounds_match_the_flip_format(tmp_path):
    vertices, faces = cube((-0.5, 0, 1), (0.5, 1, 2))
    mantaflow.write_bobj(tmp_path / "000007.bobj", vertices, faces)
    x, f, _digest = read_surface(tmp_path / "000007.bobj")
    assert np.allclose(x, vertices) and (f == faces).all()
    mantaflow.write_bounds(tmp_path / "bounds000007.bbox", (-1, -1, 0), (1, 1, 2), 0.05)
    bounds = json.loads((tmp_path / "bounds000007.bbox").read_text())
    assert (bounds["x"], bounds["y"], bounds["z"]) == (-1, -1, 0)
    assert bounds["width"] == 2 and bounds["isize"] == 40 and bounds["dx"] == 0.05


class FakeGrid:
    def __init__(self, faces, lo):
        self.faces, self.lo = faces, lo

    def activeVoxelCount(self):
        return int(self.faces.size)

    def evalActiveVoxelBoundingBox(self):
        hi = tuple(l + n - 2 for l, n in zip(self.lo, self.faces.shape[:3]))
        return self.lo, hi

    def copyToArray(self, array, ijk):
        assert tuple(ijk) == self.lo
        array[...] = self.faces[:array.shape[0], :array.shape[1], :array.shape[2]]


class FakeOpenVDB:
    def __init__(self, grid):
        self.grid = grid

    def read(self, _path, name):
        if name != "velocity":
            raise KeyError(name)
        return self.grid


def test_staggered_faces_average_to_cell_centres():
    faces = np.zeros((4, 4, 4, 3), np.float32)
    faces[..., 0] = np.arange(4)[:, None, None]  # x component grows along x faces
    centre, origin = mantaflow.read_velocity("frame.vdb", openvdb=FakeOpenVDB(FakeGrid(faces, (2, 3, 4))))
    assert origin == (2, 3, 4) and centre.shape == (3, 3, 3, 3)
    assert np.allclose(centre[:, 0, 0, 0], [0.5, 1.5, 2.5])


def test_missing_velocity_grid_is_an_artist_error():
    class Empty:
        def read(self, _path, _name):
            raise KeyError("velocity")
    with pytest.raises(ValueError, match="velocity grid is missing"):
        mantaflow.read_velocity("frame.vdb", openvdb=Empty())
