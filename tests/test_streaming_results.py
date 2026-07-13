from __future__ import annotations

import struct

import numpy as np
import pytest

from cloth_next.ppf import coordinates, results


def test_numpy_decode_matches_python_and_preserves_order():
    blob = struct.pack("<9f", *range(9))
    array = results.decode_frame_payload_numpy(blob)
    assert array.dtype == np.dtype("<f4")
    assert array.tolist() == [list(v) for v in results.decode_frame_payload(blob)]
    indices = results.object_index_array((2, 0), total_vertices=3, uuid="cloth")
    extracted = results.extract_object_frame_numpy(
        array, indices, frame=1, uuid="cloth", expected_count=2)
    assert extracted.tolist() == [[6, 7, 8], [0, 1, 2]]


@pytest.mark.parametrize("blob", [b"", b"123", struct.pack("<3f", float("nan"), 0, 0),
                                  struct.pack("<3f", float("inf"), 0, 0)])
def test_numpy_decode_rejects_invalid_frames(blob):
    with pytest.raises(results.ResultValidationError):
        results.decode_frame_payload_numpy(blob)


def test_numpy_mapping_rejects_out_of_range_and_wrong_count():
    with pytest.raises(results.ResultValidationError):
        results.object_index_array((3,), total_vertices=3, uuid="cloth")
    frame = np.zeros((3, 3), dtype="<f4")
    with pytest.raises(results.ResultValidationError, match="expected 2"):
        results.extract_object_frame_numpy(frame, np.array([0]), frame=1,
                                           uuid="cloth", expected_count=2)


@pytest.mark.parametrize("matrix", [
    ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
    ((1, 0, 0, 4), (0, 1, 0, -2), (0, 0, 1, .5), (0, 0, 0, 1)),
    ((0, -1, 0, 0), (1, 0, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
    ((2, 0, 0, 0), (0, 3, 0, 0), (0, 0, .5, 0), (0, 0, 0, 1)),
    coordinates.ZUP_TO_YUP,
])
def test_vector_transform_matches_python(matrix):
    points = np.array([[1.25, -2.5, 3.75], [0, 1, -1]], dtype="<f4")
    expected = coordinates.transform_points(matrix, points)
    actual = coordinates.transform_points_numpy(matrix, points)
    assert np.allclose(actual, expected, rtol=0, atol=1e-6)
