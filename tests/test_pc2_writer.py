# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PC2 writer: exact header, size validation, atomic publication."""

from __future__ import annotations

import struct
import os

import numpy as np

import pytest

from cloth_next.bake import pc2


def _frames(frame_count=8, vertex_count=4):
    return [[(float(f), float(v), 0.5) for v in range(vertex_count)]
            for f in range(frame_count)]


def test_write_and_read_header(tmp_path):
    path = tmp_path / "cache.pc2"
    header = pc2.write_pc2(path, _frames())
    assert header == pc2.Pc2Header(4, 0.0, 1.0, 8)
    raw = path.read_bytes()
    assert raw[:12] == b"POINTCACHE2\0"
    version, verts = struct.unpack_from("<ii", raw, 12)
    start, sampling = struct.unpack_from("<ff", raw, 20)
    frames = struct.unpack_from("<i", raw, 28)[0]
    assert (version, verts, start, sampling, frames) == (1, 4, 0.0, 1.0, 8)
    assert len(raw) == 32 + 8 * 4 * 12
    # first float triple is frame 0 / vertex 0
    assert struct.unpack_from("<3f", raw, 32) == (0.0, 0.0, 0.5)
    assert pc2.read_header(path) == header
    assert not path.with_name(path.name + ".tmp").exists()  # atomic publish


def test_write_rejects_bad_frames(tmp_path):
    with pytest.raises(pc2.Pc2Error):
        pc2.write_pc2(tmp_path / "a.pc2", [])
    with pytest.raises(pc2.Pc2Error, match="constant topology"):
        pc2.write_pc2(tmp_path / "b.pc2",
                      [[(0, 0, 0)], [(0, 0, 0), (1, 1, 1)]])
    with pytest.raises(pc2.Pc2Error, match="non-finite"):
        pc2.write_pc2(tmp_path / "c.pc2", [[(float("nan"), 0, 0)]])


def test_read_header_rejects_corruption(tmp_path):
    path = tmp_path / "cache.pc2"
    pc2.write_pc2(path, _frames())
    with pytest.raises(pc2.Pc2Error, match="not a POINTCACHE2"):
        bad = tmp_path / "bad.pc2"
        bad.write_bytes(b"NOTACACHE" + b"\x00" * 40)
        pc2.read_header(bad)
    truncated = tmp_path / "trunc.pc2"
    truncated.write_bytes(path.read_bytes()[:-4])
    with pytest.raises(pc2.Pc2Error, match="size mismatch"):
        pc2.read_header(truncated)


def test_streaming_writer_frame_order_endianness_and_mapping(tmp_path):
    path = tmp_path / "stream.pc2"
    writer = pc2.StreamingPc2Writer(path, vertex_count=2, frame_count=2,
                                    start_frame=12.5, sample_rate=.5)
    writer.write_frame(np.array([[1, 2, 3], [4, 5, 6]], dtype=">f4"))
    writer.write_frame([[7, 8, 9], [10, 11, 12]])
    assert writer.finalize() == pc2.Pc2Header(2, 12.5, .5, 2)
    assert np.frombuffer(path.read_bytes()[32:], dtype="<f4").reshape(2, 2, 3).tolist() == [
        [[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_streaming_writer_rejects_nonfinite_and_preserves_old_cache(tmp_path, value):
    path = tmp_path / "cache.pc2"
    path.write_bytes(b"old-valid-cache")
    writer = pc2.StreamingPc2Writer(path, vertex_count=1, frame_count=1)
    with pytest.raises(pc2.Pc2Error, match="non-finite"):
        writer.write_frame([[value, 0, 0]])
    writer.abort()
    assert path.read_bytes() == b"old-valid-cache"
    assert not writer.temporary_path.exists()


def test_streaming_writer_lifecycle_errors_and_atomic_replace(tmp_path, monkeypatch):
    path = tmp_path / "cache.pc2"
    old = b"old-valid-cache"
    path.write_bytes(old)
    writer = pc2.StreamingPc2Writer(path, vertex_count=1, frame_count=2)
    writer.write_frame([[0, 0, 0]])
    with pytest.raises(pc2.Pc2Error, match="expected 2"):
        writer.finalize()
    assert path.read_bytes() == old
    assert not writer.temporary_path.exists()

    writer = pc2.StreamingPc2Writer(path, vertex_count=1, frame_count=1)
    with pytest.raises(pc2.Pc2Error, match="shape"):
        writer.write_frame([0, 0, 0])
    writer.abort()

    writer = pc2.StreamingPc2Writer(path, vertex_count=1, frame_count=1)
    writer.write_frame([[1, 2, 3]])
    calls = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda source, target: (calls.append((source, target)), real_replace(source, target))[1])
    writer.finalize()
    assert calls and calls[-1][1] == path
    with pytest.raises(pc2.Pc2Error):
        writer.finalize()
    with pytest.raises(pc2.Pc2Error):
        writer.write_frame([[0, 0, 0]])


def test_streaming_writer_too_many_frames_and_context_abort(tmp_path):
    path = tmp_path / "cache.pc2"
    writer = pc2.StreamingPc2Writer(path, vertex_count=1, frame_count=1)
    writer.write_frame([[0, 0, 0]])
    with pytest.raises(pc2.Pc2Error, match="more frames"):
        writer.write_frame([[0, 0, 0]])
    writer.abort()
    with pytest.raises(RuntimeError):
        with pc2.StreamingPc2Writer(path, vertex_count=1, frame_count=1) as active:
            temporary = active.temporary_path
            raise RuntimeError("write failed")
    assert not temporary.exists()


def test_streaming_output_is_byte_exact_with_legacy_reference(tmp_path):
    frames = _frames(frame_count=3, vertex_count=4)
    reference = tmp_path / "reference.pc2"
    header = struct.pack("<12siiffi", b"POINTCACHE2\0", 1, 4, 2.0, .25, 3)
    body = bytearray()
    for frame in frames:
        for position in frame:
            body += struct.pack("<3f", *position)
    reference.write_bytes(header + bytes(body))
    streamed = tmp_path / "streamed.pc2"
    writer = pc2.StreamingPc2Writer(streamed, vertex_count=4, frame_count=3,
                                    start_frame=2.0, sample_rate=.25)
    for frame in frames:
        writer.write_frame(frame)
    writer.finalize()
    assert streamed.read_bytes() == reference.read_bytes()


def test_writer_uses_bytes_view_and_reports_separate_finalize_timings(tmp_path):
    array = np.arange(12, dtype="<f4").reshape(4, 3)
    path = tmp_path / "view.pc2"
    writer = pc2.StreamingPc2Writer(path, vertex_count=4, frame_count=1)
    writer.write_frame(array)
    writer.finalize()
    assert path.read_bytes()[32:] == array.tobytes()
    for value in (writer.flush_seconds, writer.fstat_seconds,
                  writer.fsync_seconds, writer.close_seconds,
                  writer.replace_seconds, writer.validation_seconds):
        assert value >= 0
