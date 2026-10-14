from copy import deepcopy
from cloth_next.gaia.native_fluid import merge_disjoint_fluid_targets

def target(group, **changes):
    return dict(path='field.gaia',fingerprint='source',arrays_digest='arrays',
                groups=[group],strength=.2,influence=1.,flow=1.,velocity_scale=1.,**changes)

def test_identical_volume_is_shared_only_across_disjoint_groups_without_mutating_inputs():
    original=[target(0),target(1)]
    snapshot=deepcopy(original)
    assert merge_disjoint_fluid_targets(original)==[dict(target(0),groups=[0,1])]
    assert original==snapshot

def test_different_coupling_or_source_remains_separate():
    for key,value in [('strength',.5),('flow',.5),('influence',.5),
                      ('path','other.gaia'),('arrays_digest','changed')]:
        second=target(1);second[key]=value
        assert len(merge_disjoint_fluid_targets([target(0),second]))==2

def test_overlapping_groups_keep_additive_fields_separate():
    original=[target(0),target(0),target(0)]
    assert merge_disjoint_fluid_targets(original)==original
