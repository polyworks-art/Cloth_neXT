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


def wet_cloth(enabled=True, fiber=1380., absorption=.4, wet=.5, dry=60., cling=.3):
    return SimpleNamespace(cloth_next=SimpleNamespace(
        water_wet_fabric=enabled, water_fiber_density=fiber, water_absorption=absorption,
        water_wet_time=wet, water_dry_time=dry, water_wet_cling=cling))


def test_wet_fabric_is_absent_unless_enabled(blender_env):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    assert water_flow.wet_fabric_model([wet_cloth(False)], (0., 0., -9.81)) == {}


def test_wet_fabric_passes_buoyancy_soaking_and_solver_gravity(blender_env):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    model = water_flow.wet_fabric_model([wet_cloth(fiber=1250.)], (0., 0., -9.81))
    assert model['buoyancy_ratio'] == pytest.approx(0.8)
    assert model['absorption'] == .4 and model['wet_time_s'] == .5 and model['dry_time_s'] == 60.
    assert model['wet_cling'] == .3
    # Blender Z-up gravity becomes the solver's Y-up axis.
    assert model['gravity_m_s2'] == pytest.approx([0., -9.81, 0.])


def test_mixed_wet_fabric_settings_are_refused(blender_env):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    with pytest.raises(ValueError, match='Wet Fabric settings'):
        water_flow.wet_fabric_model([wet_cloth(), wet_cloth(absorption=2.)], (0., 0., -9.81))
