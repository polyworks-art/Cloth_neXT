# SPDX-License-Identifier: GPL-3.0-or-later
"""GAIA Water Flow switches on only with GAIA access, then with pressure."""
import sys
from types import SimpleNamespace as NS


def _settings(enabled):
    return NS(water_flow_enabled=enabled, water_hydrodynamic_pressure=False)


def _run(blender_env, monkeypatch, *, access, pressure_available, enabled=True):
    props = blender_env.object_properties
    gaia_account = sys.modules['cloth_next.blender.gaia_account']
    monkeypatch.setattr(gaia_account, 'access_granted', lambda: access)
    monkeypatch.setattr(props, '_hydrodynamic_pressure_available', lambda: pressure_available)
    shared = []
    monkeypatch.setattr(props, '_on_shared_water_update', lambda s, _c: shared.append(s.water_flow_enabled))
    settings = _settings(enabled)
    props._on_water_flow_enabled_update(settings, NS())
    return settings, shared


def test_without_gaia_access_water_flow_switches_back_off(blender_env, monkeypatch):
    settings, shared = _run(blender_env, monkeypatch, access=False, pressure_available=True)
    assert not settings.water_flow_enabled and not settings.water_hydrodynamic_pressure
    assert shared == []


def test_with_access_water_flow_starts_with_pressure(blender_env, monkeypatch):
    settings, shared = _run(blender_env, monkeypatch, access=True, pressure_available=True)
    assert settings.water_flow_enabled and settings.water_hydrodynamic_pressure
    assert shared == [True]


def test_switching_off_never_needs_access(blender_env, monkeypatch):
    settings, shared = _run(blender_env, monkeypatch, access=False, pressure_available=True, enabled=False)
    assert not settings.water_flow_enabled and shared == [False]
