# SPDX-License-Identifier: GPL-3.0-or-later
"""Real RNA preset persistence and independent static Collider reuse."""
from pathlib import Path
from types import SimpleNamespace
import sys
import uuid
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
import cloth_next
from cloth_next.blender import solver_test as bake
from cloth_next.bake.frame_range import BakeFrameRange

cloth_next.register()
obj = bpy.context.object
obj.cloth_next.role = 'SOFT_BODY'
obj.cloth_next.enabled = True
settings = obj.cloth_next
settings.soft_body.stretch_resistance = 123
settings.soft_body.volume_scale = .8
settings.soft_body.stretch_plasticity_enabled = True
settings.damping.shape_damping = .012
settings.soft_body.appear_solid = True
assert settings.soft_body.stretch_resistance == 1e7
assert not settings.soft_body.stretch_plasticity_enabled
path = Path(__file__).resolve().parents[1] / '.tmp_verify/softbody-controls.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
obj = bpy.data.objects['Cube']
settings = obj.cloth_next
assert settings.soft_body.appear_solid
settings.soft_body.appear_solid = False
assert settings.soft_body.stretch_resistance == 123
assert abs(settings.soft_body.volume_scale - .8) < 1e-6
assert settings.soft_body.stretch_plasticity_enabled
assert abs(settings.damping.shape_damping - .012) < 1e-6
settings.cache_directory = str(path.parent / ('static-collider-cache-' + uuid.uuid4().hex))
bpy.ops.mesh.primitive_cube_add(location=(0, 0, -3))
collider = bpy.context.object
collider.cloth_next.role = 'COLLIDER'
collider.cloth_next.enabled = True
bake.export_identity.ensure_unique_persistent_ids((obj, collider))
bake.ensure_simulation_modifier(collider)
original_extract = bake._extract_boundary_mesh
calls = []
def extract(*args, **kwargs):
    calls.append(1)
    return original_extract(*args, **kwargs)
bake._extract_boundary_mesh = extract
snapshot = SimpleNamespace(cloth_obj=obj, collider_source_signatures={})
def geometry():
    snapshot.collider_source_signatures[collider.name] = bake.mesh_geometry_signature(collider.data)
    return bake._static_collider_geometry(bpy.context, collider, BakeFrameRange(1, 10), snapshot)
try:
    first = geometry()
    settings.soft_body.stretch_resistance = 2000
    second = geometry()
    assert len(calls) == 1, calls
    np.testing.assert_array_equal(first[0], second[0])
    collider.data.vertices[0].co.x += .25
    geometry()
    assert len(calls) == 2, calls
    collider.location.x += 1
    bpy.context.view_layer.update()
    geometry()
    assert len(calls) == 3, calls
    print('SOFTBODY_CONTROLS_SMOKE_OK: real checkbox callback, saved backup, exact restore, material-only collider reuse, mesh/transform invalidation')
finally:
    bake._extract_boundary_mesh = original_extract
    cloth_next.unregister()
