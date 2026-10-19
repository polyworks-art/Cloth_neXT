import math
import pytest
from cloth_next.sewing_visual import sample, threads, segment, thread_count, validate_paths, seam_color, length


def test_threads_are_independent_of_mesh_resolution_and_reverse_immediately():
    a=((0,0,0),(1,0,0))
    b=tuple((i/19,1,0) for i in range(20))
    result=threads(a,b,101)
    assert len(result)==202
    assert [p[0] for p in result[::2]] == pytest.approx([p[0] for p in result[1::2]])
    reverse=threads(a,b,101,True)
    assert reverse[1]==(1,1,0)
    assert reverse[-1]==(0,1,0)


def test_curved_sampling_is_arc_length_based():
    points=((0,0,0),(1,0,0),(1,3,0))
    assert sample(points,5)==((0,0,0),(1,0,0),(1,1,0),(1,2,0),(1,3,0))
    assert length(points)==4


def test_segment_stops_at_corners_and_branches():
    vertices=((0,0,0),(1,0,0),(2,0,0),(2,1,0),(1,1,0))
    edges=((0,1),(1,2),(2,3))
    assert segment(vertices,edges,(0,1))==(0,1,2)
    assert segment(vertices,edges+((1,4),),(0,1))==(0,1)


def test_invalid_same_object_and_disconnected_paths():
    vertices=((0,0,0),(1,0,0),(2,0,0),(3,0,0))
    edges=((0,1),(2,3))
    validate_paths(vertices,edges,(0,1),vertices,edges,(2,3),True)
    with pytest.raises(ValueError,match='share vertices'):
        validate_paths(vertices,edges,(0,1),vertices,edges,(0,1),True)
    with pytest.raises(ValueError,match='disconnected'):
        validate_paths(vertices,edges,(0,2),vertices,edges,(2,3))


def test_screen_density_bounded_and_color_stable():
    assert thread_count(900)==101
    assert thread_count(900,2)==201
    assert thread_count(100000)==1024
    assert seam_color('seam')==seam_color('seam')
    assert seam_color('seam')!=seam_color('other')
