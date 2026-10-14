# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace as NS
import sys
import pytest


def domain(module):
    p=NS(**{key:False if type(value) is bool else .5 if type(value) is float else 2
            for key,value in module.FLIP_EXPORT_SETTINGS.items()})
    return NS(flip_fluid=NS(object_type='TYPE_DOMAIN',domain=NS(particles=p,
        bake=NS(is_simulation_running=False))))


def test_button_enables_all_required_exports_and_is_idempotent(blender_env):
    m=sys.modules['cloth_next.blender.water_flow'];d=domain(m)
    assert m.configure_flip_exports(d)==7
    assert vars(d.flip_fluid.domain.particles)==m.FLIP_EXPORT_SETTINGS
    assert m.configure_flip_exports(d)==0


def test_missing_setting_refuses_before_any_mutation(blender_env):
    m=sys.modules['cloth_next.blender.water_flow'];d=domain(m)
    del d.flip_fluid.domain.particles.enable_fluid_particle_velocity_vector_attribute
    before=vars(d.flip_fluid.domain.particles).copy()
    with pytest.raises(ValueError,match='lacks required'):m.configure_flip_exports(d)
    assert vars(d.flip_fluid.domain.particles)==before


def test_busy_bake_and_wrong_domain_refused(blender_env):
    m=sys.modules['cloth_next.blender.water_flow'];d=domain(m)
    d.flip_fluid.domain.bake.is_simulation_running=True
    before=vars(d.flip_fluid.domain.particles).copy()
    with pytest.raises(ValueError,match='Finish or cancel'):m.configure_flip_exports(d)
    assert vars(d.flip_fluid.domain.particles)==before
    with pytest.raises(ValueError,match='Select a FLIP'):m.configure_flip_exports(None)


def test_partial_setter_failure_restores_previous_values(blender_env):
    m=sys.modules['cloth_next.blender.water_flow'];d=domain(m)
    class Refusing(NS):
        def __setattr__(self,key,value):
            if key=='enable_fluid_particle_interior_output' and value is True:
                raise RuntimeError('RNA is read only')
            super().__setattr__(key,value)
    d.flip_fluid.domain.particles=Refusing(**vars(d.flip_fluid.domain.particles))
    before=vars(d.flip_fluid.domain.particles).copy()
    with pytest.raises(ValueError,match='RNA is read only'):m.configure_flip_exports(d)
    assert vars(d.flip_fluid.domain.particles)==before


def test_operator_reports_rebake_and_supports_undo(blender_env):
    m=sys.modules['cloth_next.blender.water_flow'];d=domain(m)
    op=m.CLOTHNEXT_OT_enable_flip_velocity_export();reports=[]
    op.report=lambda level,message:reports.append(message)
    context=NS(object=NS(cloth_next=NS(water_flow_domain=d)))
    assert op.execute(context)=={'FINISHED'}
    assert 'Rebake FLIP' in reports[-1]
    assert 'UNDO' in op.bl_options
