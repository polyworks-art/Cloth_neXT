import numpy as np
from tools.gaia026_geometry_checks import triangles_pierce,acceleration_max

def test_detects_strict_triangle_crossing_but_not_separation_or_boundary_contact():
    flat=np.array([[-1,-1,0],[1,-1,0],[0,1,0]],dtype=float)
    crossing=np.array([[0,0,-1],[0,0,1],[.2,.1,1]],dtype=float)
    assert triangles_pierce(flat,crossing)
    assert not triangles_pierce(flat,crossing+np.array([5,0,0]))
    assert not triangles_pierce(flat,flat+np.array([0,0,1]))
    assert not triangles_pierce(flat,np.array([[0,0,0],[0,0,1],[.2,.1,1]]))

def test_acceleration_uses_frame_rate_and_preserves_constant_velocity():
    samples=np.array([[[0.,0,0]],[[1.,0,0]],[[2.,0,0]]])
    assert acceleration_max(samples,24)==0
    samples[-1,0,0]=3
    assert acceleration_max(samples,24)==576
