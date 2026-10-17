import numpy as np
import pytest
from cloth_next.gaia.hydrodynamics import SheetDrag, triangle_load, nodal_masses

X = np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]])

def load(u=(0,0,2), wet=1, x=X, v=None, model=SheetDrag()):
    return triangle_load(x, np.zeros((3,3)) if v is None else v, u, wet, model)

@pytest.mark.parametrize('u,wet', [((0,0,2),0),((0,0,0),1),((2,0,0),1)])
def test_zero_limits(u, wet):
    f,h,_=load(u,wet)
    assert np.array_equal(f,np.zeros((3,3)))

def test_normal_pressure_and_reverse():
    f,_,d=load()
    np.testing.assert_allclose(f.sum(0),[0,0,1000])
    assert d['dynamic_pressure_pa']==2000
    np.testing.assert_allclose(load((0,0,-2))[0],-f)
    np.testing.assert_allclose(load(x=X[[0,2,1]])[0],f)

def test_velocity_squared_area_and_wet_area():
    f=load()[0]
    np.testing.assert_allclose(load((0,0,4))[0],4*f)
    np.testing.assert_allclose(load(x=X*np.sqrt(2))[0],2*f)
    np.testing.assert_allclose(load((0,0,20),.001)[0],.1*f)

def test_tangential_resistance_opposes_relative_motion():
    f,h,_=load((2,0,0),model=SheetDrag(tangential_coefficient=.02))
    np.testing.assert_allclose(f.sum(0),[20,0,0])
    assert np.linalg.eigvalsh(h).min()>-1e-10

def test_velocity_tangent_matches_finite_difference():
    v=np.arange(9).reshape(3,3)*.03
    model=SheetDrag(tangential_coefficient=.03)
    f,h,_=load((1,2,3),v=v,model=model)
    numerical=np.empty((9,9))
    for j in range(9):
        dv=np.zeros(9);dv[j]=1e-6
        numerical[:,j]=-(load((1,2,3),v=v+dv.reshape(3,3),model=model)[0]-
                          load((1,2,3),v=v-dv.reshape(3,3),model=model)[0]).ravel()/2e-6
    np.testing.assert_allclose(h,numerical,rtol=2e-7,atol=2e-7)

def test_rest_mass_and_acceleration():
    m=nodal_masses(X,[[0,1,2]],.2)
    np.testing.assert_allclose(m.sum(),.1)
    np.testing.assert_allclose(load()[0]/nodal_masses(X,[[0,1,2]],.4)[:,None],
                               .5*load()[0]/m[:,None])

def test_refinement_preserves_total_force():
    a,b,c=X;ab=(a+b)/2;bc=(b+c)/2;ca=(c+a)/2
    refined=sum((load(x=np.array(t))[0].sum(0) for t in
                 [(a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca)]))
    np.testing.assert_allclose(refined,load()[0].sum(0))

def test_deformed_geometry_is_used():
    x=X.copy();x[2]=[0,0,1]
    np.testing.assert_allclose(load(x=x)[0],0)

@pytest.mark.parametrize('wet',[-1,1.01,float('nan')])
def test_reject_nonphysical_wet_area(wet):
    with pytest.raises(ValueError):load(wet=wet)

def test_degenerate_triangle_has_no_force():
    f,h,_=load(x=np.zeros((3,3)))
    assert not f.any() and not h.any()
