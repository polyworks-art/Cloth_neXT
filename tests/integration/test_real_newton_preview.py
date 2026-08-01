"""Real Newton 1.4.0 / Warp 1.15.0 CUDA integration evidence."""

from __future__ import annotations

import os
from pathlib import Path
import time
import uuid

import numpy as np
import pytest

from cloth_next.newton_preview.client import NewtonWorkerClient
from cloth_next.newton_preview.contracts import (
    PreviewCreateRequest, PreviewMaterial, PreviewMesh, PreviewQuality)

pytestmark = pytest.mark.integration


def _python():
    value = os.environ.get("CLOTHNEXT_NEWTON_PYTHON", "").strip()
    if not value:
        pytest.skip("Newton unverified")
    path = Path(value)
    if not path.is_file():
        pytest.skip("Newton unverified")
    return path


def _grid(nx=6, ny=6, *, size=1.0, z=1.0):
    vertices = tuple((size * x / (nx - 1), size * y / (ny - 1), z)
                     for y in range(ny) for x in range(nx))
    triangles = []
    for y in range(ny - 1):
        for x in range(nx - 1):
            a = y * nx + x; b = a + 1; c = a + nx; d = c + 1
            triangles.extend(((a, c, b), (b, c, d)))
    return PreviewMesh(vertices, tuple(triangles))


def _material(margin=0.005):
    return PreviewMaterial(0.2, 1000.0, 700.0, 5.0,
                           1.0, 0.1, 0.3, margin, margin * 0.5)


def _run(tmp_path, cloth, *, colliders=(), pins=(), self_collision=False,
         target=12, fps=30.0):
    root = Path(__file__).resolve().parents[2]
    client = NewtonWorkerClient(_python(), package_root=root, startup_timeout=60)
    session = uuid.uuid4().hex
    request = PreviewCreateRequest(
        session, session, cloth, tuple(colliders), tuple(pins), _material(),
        PreviewQuality("TEST", 4, 8, 5, 8, self_collision),
        1, target, fps, 1.0, (0.0, 0.0, -9.81),
        str(tmp_path / session))
    try:
        health = client.start()
        assert health["newton_version"] == "1.4.0"
        assert health["warp_version"] == "1.15.0"
        assert health["cuda_device"]
        client.send("create_preview", request=request.to_wire())
        deadline = time.monotonic() + 180.0
        created = False
        initial = result = None
        while time.monotonic() < deadline and (not created or initial is None):
            message = client.poll(0.1)
            if message is None: continue
            assert message.get("event") != "error", message
            created |= message.get("event") == "created"
            if message.get("event") == "result" and message.get("frame") == 1:
                initial = np.load(message["artifact"], allow_pickle=False)
        assert created and initial is not None
        client.send("update_target_frame", frame=target)
        while time.monotonic() < deadline:
            message = client.poll(0.1)
            if message is None: continue
            assert message.get("event") != "error", message
            if message.get("event") == "result" and message.get("frame") == target:
                result = np.load(message["artifact"], allow_pickle=False)
                break
        assert result is not None
        assert result.shape == initial.shape == (len(cloth.vertices), 3)
        assert np.isfinite(result).all()
        return initial, result
    finally:
        client.shutdown()


def test_real_newton_hanging_cloth_pins_and_sags(tmp_path):
    cloth = _grid(z=1.0)
    pins = (30, 35)
    initial, result = _run(tmp_path, cloth, pins=pins, target=12)
    assert np.allclose(result[list(pins)], initial[list(pins)], atol=1.0e-5)
    free = [index for index in range(len(cloth.vertices)) if index not in pins]
    assert float(np.mean(result[free, 2])) < float(np.mean(initial[free, 2])) - 0.05


def test_real_newton_cloth_contacts_static_triangle_collider(tmp_path):
    cloth = _grid(z=0.4)
    collider = PreviewMesh(
        ((-2.0, -2.0, 0.0), (3.0, -2.0, 0.0),
         (-2.0, 3.0, 0.0), (3.0, 3.0, 0.0)),
        ((0, 1, 2), (1, 3, 2)))
    _initial, result = _run(tmp_path, cloth, colliders=(collider,), target=30)
    assert float(np.min(result[:, 2])) > -0.03
    assert float(np.min(result[:, 2])) < 0.08


def test_real_newton_self_collision_has_concrete_separation_effect(tmp_path):
    first = _grid(4, 4, z=0.8)
    offset = len(first.vertices)
    second_vertices = tuple((x, y, z + 0.001) for x, y, z in first.vertices)
    triangles = first.triangles + tuple(tuple(index + offset for index in tri)
                                       for tri in first.triangles)
    cloth = PreviewMesh(first.vertices + second_vertices, triangles)
    _initial, without = _run(tmp_path / "without", cloth,
                             self_collision=False, target=5)
    _initial, with_contact = _run(tmp_path / "with", cloth,
                                  self_collision=True, target=5)
    without_distance = float(np.mean(np.linalg.norm(
        without[:offset] - without[offset:], axis=1)))
    with_distance = float(np.mean(np.linalg.norm(
        with_contact[:offset] - with_contact[offset:], axis=1)))
    assert with_distance > without_distance + 0.001
