# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine and Cloth NeXt Force objects: gravity runs, wind is refused with a clear reason."""
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


def test_wind_is_refused_with_a_reason(backend):
    reason = backend._force_reason(SimpleNamespace(name="Empty"), _unified(wind=10.0))
    assert "Wind is not supported" in reason and "Empty" in reason
    legacy = SimpleNamespace(force=SimpleNamespace(force_type="WIND"))
    assert "not supported" in backend._force_reason(SimpleNamespace(name="W"), legacy)


def test_air_values_are_reported_not_enforced(backend):
    assert backend._force_notes(SimpleNamespace(name="Empty"), _unified(air=0.01))
    assert not backend._force_notes(SimpleNamespace(name="Empty"), _unified())
