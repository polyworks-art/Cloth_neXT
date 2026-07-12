# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Solver output decoding: vertex map, frame payloads, range validation."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from cloth_next.ppf import results
from cloth_next.ppf.schema import cbor_codec

FIXTURES = Path(__file__).parent / "fixtures" / "ppf_0_11"


def test_parse_output_map_golden():
    output_map = results.parse_output_map(
        (FIXTURES / "vertex_map_micro.cbor").read_bytes())
    assert output_map.indices_for("cn-cloth-0001", 4) == (0, 1, 2, 3)
    assert output_map.indices_for("cn-collider-0001", 4) == (4, 5, 6, 7)


def test_output_map_rejections():
    with pytest.raises(results.ResultValidationError):
        results.parse_output_map(cbor_codec.dumps(
            {"version": 1, "kind": "VertexMap", "payload": {}}))
    with pytest.raises(results.ResultValidationError):
        results.parse_output_map(cbor_codec.dumps(
            {"version": 1, "kind": "VertexMap", "payload": {"u": [-1]}}))
    with pytest.raises(results.ResultValidationError):
        results.parse_output_map(cbor_codec.dumps(
            {"version": 1, "kind": "VertexMap", "payload": {"u": "wrong"}}))
    with pytest.raises(Exception, match="kind"):
        results.parse_output_map(cbor_codec.dumps(
            {"version": 1, "kind": "Scene", "payload": {"u": [0]}}))
    output_map = results.parse_output_map(
        (FIXTURES / "vertex_map_micro.cbor").read_bytes())
    with pytest.raises(results.ResultValidationError, match="no entry"):
        output_map.indices_for("missing-uuid", 4)
    with pytest.raises(results.ResultValidationError, match="expected 5"):
        output_map.indices_for("cn-cloth-0001", 5)


def test_decode_frame_payload():
    values = [1.0, 2.0, 3.0, -4.5, 5.25, -6.125]
    blob = struct.pack(f"<{len(values)}f", *values)
    positions = results.decode_frame_payload(blob)
    assert positions == ((1.0, 2.0, 3.0), (-4.5, 5.25, -6.125))
    with pytest.raises(results.ResultValidationError):
        results.decode_frame_payload(b"")
    with pytest.raises(results.ResultValidationError, match="multiple of 12"):
        results.decode_frame_payload(b"\x00" * 10)


def test_extract_object_frame_validation():
    frame = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (2.0, 2.0, 2.0))
    assert results.extract_object_frame(frame, (2, 0), frame=1, uuid="u") == \
        ((2.0, 2.0, 2.0), (0.0, 0.0, 0.0))
    with pytest.raises(results.ResultValidationError, match="exceeds"):
        results.extract_object_frame(frame, (3,), frame=1, uuid="u")
    nan_frame = ((float("nan"), 0.0, 0.0),)
    with pytest.raises(results.ResultValidationError, match="non-finite"):
        results.extract_object_frame(nan_frame, (0,), frame=2, uuid="u")


def test_frame_file_paths():
    assert results.frame_file_path(1) == "session/output/vert_1.bin"
    assert results.frame_file_path(7) == "session/output/vert_7.bin"
    assert results.MAP_PATH == "session/map.pickle"
    with pytest.raises(ValueError):
        results.frame_file_path(-1)
