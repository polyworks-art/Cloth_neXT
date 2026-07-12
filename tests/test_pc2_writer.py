# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PC2 writer: exact header, size validation, atomic publication."""

from __future__ import annotations

import struct

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
