"""Reduced analytical references, not claims about native shell/collider tests."""
import numpy as np
from cloth_next.gaia.hydrodynamics import implicit_normal_step


def simulate(dt, **kw):
    x=v=0.
    iterations=0
    for i in range(round(.1/dt)):
        s=implicit_normal_step(x,v,dt,**kw)
        assert abs(s['residual_n'])<1e-8
        x,v=s['position_m'],s['velocity_m_s']
        iterations=max(iterations,s['newton_iterations'])
    return x,v,iterations


def test_timestep_convergence_to_analytic_free_patch_velocity():
    # m dv/dt = k(U-v)^2; v=U-U/(1+k U t/m).
    args=dict(mass_kg=.1,area_m2=.001,fluid_m_s=2.)
    exact=2-2/(1+.5*2*.1/.1)
    errors=[abs(simulate(dt,**args)[1]-exact) for dt in (.01,.005,.0025)]
    assert errors[2]<errors[1]<errors[0]
    # Backward Euler is first order: halving dt approaches half the error.
    assert 1.8<errors[1]/errors[2]<2.2


def test_cantilever_bending_stiffness_changes_deformation():
    # A reduced bending benchmark: tip stiffness 3 EI/L^3, L=1 m.
    args=dict(mass_kg=.1,area_m2=.001,fluid_m_s=2.)
    flexible=simulate(.001,stiffness_n_m=3*.1,**args)
    stiff=simulate(.001,stiffness_n_m=3*100,**args)
    assert 0<stiff[0]<flexible[0]


def test_water_pressed_against_wall_has_balanced_reaction():
    s=implicit_normal_step(.01,0,.01,mass_kg=.1,area_m2=.5,
                           fluid_m_s=2,wall_max_m=.01)
    assert s['position_m']==.01 and s['velocity_m_s']==0
    assert s['reaction_n']==1000 and s['residual_n']==0


def test_animated_wall_and_high_speed_remain_finite():
    x=v=0.
    for i in range(100):
        wall=.002*np.sin(i*.1)
        s=implicit_normal_step(x,v,.001,mass_kg=.1,area_m2=.5,
            fluid_m_s=50,wet_fraction=.001,wall_max_m=wall)
        x,v=s['position_m'],s['velocity_m_s']
        assert np.isfinite([x,v,s['reaction_n']]).all()
        assert x<=wall+1e-12 and abs(s['residual_n'])<1e-7
