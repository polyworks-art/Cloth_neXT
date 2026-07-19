# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strict-subset CBOR codec: roundtrips, exact bytes, and hard rejections.

Golden fixtures under ``tests/fixtures/ppf_0_11`` were generated with the
``cbor2`` build shipped inside the locally installed official solver package
(see ``phase3a_goldens.json``); byte equality against them proves encode
compatibility with the exact library the PPF ecosystem uses.
"""

from __future__ import annotations

import math
import io
import struct
from pathlib import Path

import numpy as np
import pytest

from cloth_next.ppf.schema import cbor_codec
from cloth_next.ppf.schema.cbor_codec import CborError, dumps, loads


def test_numpy_arrays_encode_without_python_materialization():
    value = np.arange(12, dtype=np.float32).reshape(2, 2, 3)
    decoded = cbor_codec.loads(cbor_codec.dumps(value))
    assert decoded == value.tolist()


def test_streamed_vec3_frames_are_byte_identical_and_report_progress():
    value = np.arange(60, dtype=np.float32).reshape(4, 5, 3) / 7.0
    stream = io.BytesIO()
    progress = []
    cbor_codec.dump({"frames": value}, stream,
                    progress=lambda current, total:
                    progress.append((current, total)))
    assert stream.getvalue() == cbor_codec.dumps({"frames": value})
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]


FIXTURES = Path(__file__).parent / "fixtures" / "ppf_0_11"


@pytest.mark.parametrize("value", [
    0, 1, 23, 24, 255, 256, 65535, 65536, 2**32 - 1, 2**32, 2**64 - 1,
    -1, -24, -25, -256, -257, -2**32, -2**64,
    0.0, 1.5, -9.81, 1e-3, 6.5e11,
    True, False, None,
    "", "hello", "ünïcode ✓",
    b"", b"\x00\x01\xff",
    [], [1, [2, [3.5, "x"]]],
    {}, {"a": 1, "b": [True, None]},
    {42: "int-key", -7: [1.0]},
])
def test_roundtrip(value):
    assert loads(dumps(value)) == value


def test_exact_encodings():
    assert dumps(0) == b"\x00"
    assert dumps(23) == b"\x17"
    assert dumps(24) == b"\x18\x18"
    assert dumps(-1) == b"\x20"
    assert dumps(True) == b"\xf5"
    assert dumps(None) == b"\xf6"
    assert dumps("a") == b"\x61a"
    assert dumps([1]) == b"\x81\x01"
    assert dumps({"a": 1}) == b"\xa1\x61a\x01"
    # floats are ALWAYS float64, like cbor2 for Python floats
    assert dumps(1.5) == b"\xfb" + struct.pack(">d", 1.5)
    assert dumps(0.0) == b"\xfb" + struct.pack(">d", 0.0)


def test_map_insertion_order_is_preserved():
    a = dumps({"x": 1, "y": 2})
    b = dumps({"y": 2, "x": 1})
    assert a != b
    assert loads(a) == loads(b)


def test_nan_and_infinity_roundtrip():
    encoded = dumps([float("inf"), float("-inf")])
    assert loads(encoded) == [float("inf"), float("-inf")]
    assert math.isnan(loads(dumps(float("nan"))))


def test_decode_accepts_short_floats():
    assert loads(b"\xf9\x3c\x00") == 1.0          # float16
    assert loads(b"\xfa" + struct.pack(">f", 2.5)) == 2.5  # float32


@pytest.mark.parametrize("bad", [
    b"",                       # empty input
    b"\x18",                   # truncated argument
    b"\x81",                   # truncated array
    b"\x62a",                  # truncated text
    b"\xfb\x00\x00",           # truncated float64
    b"\x5f\xff",               # indefinite byte string
    b"\x9f\xff",               # indefinite array
    b"\xbf\xff",               # indefinite map
    b"\xc0\x00",               # tag
    b"\x1c",                   # reserved additional info
    b"\x00\x00",               # trailing bytes
    b"\xa1\x81\x00\x00",       # array as map key
    b"\x63\xff\xff\xff",       # invalid UTF-8
])
def test_malformed_inputs_rejected(bad):
    with pytest.raises(CborError):
        loads(bad)


@pytest.mark.parametrize("value", [
    {1.5: "float key"}, {(1, 2): "tuple key"}, object(), {"x": object()},
    2**64, -2**64 - 1,
])
def test_unsupported_values_rejected(value):
    with pytest.raises(CborError):
        dumps(value)


def test_nesting_bound_enforced():
    deep: object = 1
    for _ in range(cbor_codec.MAX_NESTING + 2):
        deep = [deep]
    with pytest.raises(CborError):
        dumps(deep)


def test_golden_fixtures_decode_and_reencode_byte_identically():
    for name in ("scene_micro.cbor", "param_micro.cbor",
                 "vertex_map_micro.cbor"):
        blob = (FIXTURES / name).read_bytes()
        decoded = loads(blob)
        assert dumps(decoded) == blob, f"{name} re-encode is not byte-identical"


_SHIPPED_SITE_PACKAGES = (Path(__file__).parents[1] / "solver" /
                          "windows-x86_64" / "python" / "Lib" / "site-packages")


@pytest.mark.skipif(not _SHIPPED_SITE_PACKAGES.is_dir(),
                    reason="local official solver package not present")
def test_encodings_match_shipped_cbor2():
    import sys
    sys.path.insert(0, str(_SHIPPED_SITE_PACKAGES))
    try:
        import cbor2
    except ImportError:
        pytest.skip("shipped cbor2 not importable")
    finally:
        sys.path.remove(str(_SHIPPED_SITE_PACKAGES))
    samples = [0, 24, -257, 1.5, -0.0, True, None, "text",
               {"version": 1, "kind": "Scene",
                "payload": [{"type": "SHELL", "object": []}]},
               {7: [1.0, -2.0]}, [b"\x00", ["nested", {"k": False}]]]
    for sample in samples:
        assert dumps(sample) == cbor2.dumps(sample)
        assert cbor2.loads(dumps(sample)) == sample
