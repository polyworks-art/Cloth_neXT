import numpy as np

from cloth_next.gaia.hydrodynamic_transport import compact_bricks, dense_bricks


def test_identical_bricks_share_storage_without_changing_lookups():
    values = np.zeros((16, 16, 24, 4), dtype='<f4')
    values[..., 0] = 0.02
    values[..., 3] = 1
    values[3:5, 4:12, 6:9, 0] = np.linspace(-.01, .01, 2 * 8 * 3).reshape(2, 8, 3)
    table, bricks, resolution = dense_bricks(values)
    compact, stored = compact_bricks(table, bricks)
    assert resolution == [24, 16, 16]
    assert len(compact) == len(table) and len(stored) < len(bricks)
    np.testing.assert_array_equal(stored[compact], bricks[table])


def test_empty_entries_stay_empty():
    bricks = np.zeros((3, 512, 4), dtype='<f4')
    bricks[2, :, 0] = 1
    table = np.array([0, 0xFFFFFFFF, 1, 2], dtype='<u4')
    compact, stored = compact_bricks(table, bricks)
    assert compact[1] == 0xFFFFFFFF and len(stored) == 2
    used = compact != 0xFFFFFFFF
    np.testing.assert_array_equal(stored[compact[used]], bricks[table[used]])
