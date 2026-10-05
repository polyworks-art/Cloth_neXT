# SPDX-License-Identifier: GPL-3.0-or-later
import json
from types import SimpleNamespace


def test_appear_solid_is_reversible_and_preserves_mass_and_contact(blender_env):
    module = blender_env.object_properties
    soft = SimpleNamespace(stretch_resistance=123., poisson_ratio=.3,
        volume_scale=.8, stretch_plasticity_enabled=True,
        stretch_plasticity_rate=.7, stretch_plasticity_threshold_percent=9.,
        appear_solid_backup='', volume_density=321., tetrahedralizer='FTETWILD')
    settings = SimpleNamespace(soft_body=soft,
        damping=SimpleNamespace(shape_damping=.012),
        collision=SimpleNamespace(surface_grip=.6, collision_gap=.001, surface_offset=0.))
    previous = dict(vars(soft))
    module._apply_soft_body_appear_solid(settings, True)
    assert soft.stretch_resistance == 1e7 and soft.volume_scale == 1
    assert not soft.stretch_plasticity_enabled
    material = module.soft_body_settings_from(settings)
    assert material.stretch_resistance == 1e7 and material.stretch_plasticity_rate == 0
    assert material.volume_density == 321 and material.surface_grip == .6
    backup = json.loads(soft.appear_solid_backup)
    module._apply_soft_body_appear_solid(settings, True)
    assert json.loads(soft.appear_solid_backup) == backup
    module._apply_soft_body_appear_solid(settings, False)
    assert vars(soft) == previous
    assert settings.damping.shape_damping == .012
