import math
import numpy as np
import pytest
from cloth_next.gaia.flow_budget import (
    coupling_budget,fits_budget,packed_frame_bytes,native_encoding_bytes)
from cloth_next.gaia.water_field import grid_plan


@pytest.mark.parametrize('selection,custom',[('AUTO',100),('200',200),('CUSTOM',1000),('CUSTOM',4096)])
@pytest.mark.parametrize('frames,fields',[(1,1),(250,1),(250,3),(1000,2)])
def test_high_requests_fit_every_transport_budget_without_allocating(monkeypatch,selection,custom,frames,fields):
    monkeypatch.setattr(np,'zeros',lambda *args,**kwargs:pytest.fail('planner allocated a field'))
    budget=coupling_budget(frames=frames,fields=fields)
    info=grid_plan([0,0,0],[1,2,1],selection,custom,source_voxel_size=.008,budget=budget)
    dims=info['dimensions']
    assert fits_budget(dims,budget)
    assert native_encoding_bytes(dims)<=128*1024**2
    assert packed_frame_bytes(dims)<=32*1024**2
    assert (packed_frame_bytes(dims)+4096)*frames*fields+(64<<20)<=1<<30
    assert max(dims)<=info['requested_resolution']
    # No unnecessarily smaller grid: the next longest-axis value fails.
    from cloth_next.gaia.water_field import grid_dimensions
    if info['budget_adjusted']:
        assert not fits_budget(grid_dimensions([0,0,0],[1,2,1],max(dims)+1),budget)


def test_budget_inputs_do_not_invalidate_identical_physical_grid():
    a=grid_plan([0,0,0],[1,2,1],'CUSTOM',128,budget=coupling_budget(frames=3,fields=1))
    b=grid_plan([0,0,0],[1,2,1],'CUSTOM',128,budget=coupling_budget(frames=3,fields=2))
    assert a==b


def test_neon_auto_250_frames_cannot_repeat_reported_budget_failure():
    info=grid_plan([0,0,0],[1,2,1],source_voxel_size=.008,budget=coupling_budget(frames=250))
    assert info['requested_resolution']==250
    assert info['dimensions']==(48,96,48)
    assert info['budget_adjusted']


def test_impossible_sequence_fails_before_field_allocation(monkeypatch):
    monkeypatch.setattr(np,'zeros',lambda *args,**kwargs:pytest.fail('allocated field'))
    with pytest.raises(ValueError,match='Split the Bake frame range'):
        grid_plan([0,0,0],[1,1,1],'CUSTOM',4096,budget=coupling_budget(frames=10**9))


def test_legacy_velocity_window_stays_within_budget():
    budget=coupling_budget(native=False)
    info=grid_plan([0,0,0],[1,1,1],'CUSTOM',1000,budget=budget)
    assert math.prod(info['dimensions'])*24<=128*1024**2
