# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine and Cloth NeXt Force objects: gravity and Wind run, the legacy Wind type is refused."""
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def backend(blender_env):
    return importlib.import_module("cloth_next.blender.aether_engine_backend")


def _unified(wind=0.0, air=0.0):
    return SimpleNamespace(force=SimpleNamespace(gravity_strength=9.81, wind_strength=wind, air_density=air,
                                                 air_friction=0.0, vertex_air_damp=0.0))


def test_gravity_only_force_runs(backend):
    assert backend._force_reason(SimpleNamespace(name="Empty"), _unified()) == ""
    legacy = SimpleNamespace(force=SimpleNamespace(force_type="GRAVITY"))
    assert backend._force_reason(SimpleNamespace(name="G"), legacy) == ""


def test_wind_runs_and_only_the_legacy_wind_type_is_refused(backend):
    assert backend._force_reason(SimpleNamespace(name="Empty"), _unified(wind=10.0)) == ""
    legacy = SimpleNamespace(force=SimpleNamespace(force_type="WIND"))
    assert "not supported" in backend._force_reason(SimpleNamespace(name="W"), legacy)


def test_unsimulated_air_values_are_reported(backend):
    f = _unified(air=0.01)
    assert not backend._force_notes(SimpleNamespace(name="Empty"), f)    # air density drives the Wind now
    f.force.vertex_air_damp = 0.5
    assert "Vertex Air Damping" in backend._force_notes(SimpleNamespace(name="Empty"), f)[0]


def test_water_flow_is_ignored_not_refused(backend):
    obj = SimpleNamespace(name="Shirt", type="MESH")
    settings = SimpleNamespace(role="CLOTH", friction_regions=(), water_flow_enabled=True, pinning_enabled=False,
                               tethys=SimpleNamespace(enabled=True))
    assert backend._unsupported_object(obj, settings) == ""   # GAIA Flow and TETHYS stay on, AETHER ignores them


def test_bake_finished_message_reports_pinched_frames(backend):
    import numpy as np
    from cloth_next.aether_engine import bake as abake
    job = backend.AetherBakeJob.__new__(backend.AetherBakeJob)
    a = np.array([[0, 0, 0], [0.01, 0, 0], [0, 0.01, 0], [0.2, 0, 0], [0.21, 0, 0], [0.2, 0.01, 0]], np.float32)
    tris = np.array([0, 1, 2, 3, 4, 5], np.int32)
    touch = a.copy()
    touch[3:] -= [0.2, 0.0, 0.0]                       # the second part lands on the first
    frames = np.stack([a, touch, touch]).astype(np.float32)
    job.request = SimpleNamespace(frame_start=1, colliders=(abake.ColliderInput("Body", tris, a, frames),))
    job._start_pinch_check()
    note = job._pinch_note()
    assert "Body touches itself in frames 3-4" in note and "not a simulation error" in note
