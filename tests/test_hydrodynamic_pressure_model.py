import importlib
from types import SimpleNamespace

import pytest


def cloth(strength=1., tangential=0.):
    return SimpleNamespace(cloth_next=SimpleNamespace(
        water_pressure_strength=strength, water_pressure_tangential=tangential))


def test_defaults_keep_the_physical_normal_only_model(blender_env):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    model = water_flow.pressure_model([cloth(), cloth()])
    assert model['density_kg_m3'] == 1000. and model['normal_coefficient'] == 1.
    assert model['tangential_coefficient'] == 0. and model['volume_mode'] == 1


def test_strength_scales_density_and_tangential_drag_is_passed(blender_env):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    model = water_flow.pressure_model([cloth(5., .2)])
    assert model['density_kg_m3'] == 5000. and model['tangential_coefficient'] == .2


def test_mixed_settings_are_refused(blender_env):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    with pytest.raises(ValueError, match='same Pressure Strength'):
        water_flow.pressure_model([cloth(1.), cloth(2.)])
