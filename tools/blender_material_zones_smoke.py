# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender face attributes, save/load, mapping and evaluated BVH occlusion.

blender --background --factory-startup --python tools/blender_material_zones_smoke.py
"""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
from mathutils import Vector, Matrix

import cloth_next
from cloth_next.blender import material_zones as data, material_zone_selector as selector
from cloth_next.blender.object_properties import shell_settings_from
from cloth_next.materials.zones import ZoneError, visible_center


cloth_next.register()
mesh = bpy.data.meshes.new('Zone Smoke')
# Adjacent quads share an edge. All children of the second quad must be 100.
mesh.from_pydata(((0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)), (), ((0, 1, 4, 3), (1, 2, 5, 4)))
obj = bpy.data.objects.new('Zone Smoke', mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
obj.cloth_next.enabled = True
obj.cloth_next.material.bend_resistance = 10
assert bpy.ops.clothnext.add_material_zone() == {'FINISHED'}
zone = obj.cloth_next.material_zones[0]
identity, token = zone.identity, zone.token
assert identity and token == 1
assert bpy.ops.clothnext.material_zone_property(identity=identity, key='bend') == {'FINISHED'}
zone.overrides[0].value = 100
data.write_owners(obj, (0, token))
mesh.calc_loop_triangles()
triangles = tuple(tuple(t.vertices) for t in mesh.loop_triangles)
assert data.export_tables(obj, triangles, shell_settings_from(obj.cloth_next))['bend'] == (10, 10, 100, 100)
assert zone.face_count == 1

ngon_mesh = bpy.data.meshes.new('Zone Ngon')
ngon_mesh.from_pydata(((4, 0, 0), (5, 0, 0), (5.5, .5, 0), (5, 1, 0), (4, 1, 0)), (), ((0, 1, 2, 3, 4),))
ngon_obj = bpy.data.objects.new('Zone Ngon', ngon_mesh)
bpy.context.collection.objects.link(ngon_obj)
bpy.context.view_layer.objects.active = ngon_obj
ngon_obj.select_set(True)
ngon_obj.cloth_next.enabled = True
bpy.ops.clothnext.add_material_zone()
ngon_zone = ngon_obj.cloth_next.material_zones[0]
bpy.ops.clothnext.material_zone_property(identity=ngon_zone.identity, key='bend')
ngon_zone.overrides[0].value = 100
data.write_owners(ngon_obj, (ngon_zone.token,))
ngon_mesh.calc_loop_triangles()
ngon_triangles = tuple(tuple(t.vertices) for t in ngon_mesh.loop_triangles)
assert len(ngon_triangles) == 3
assert data.export_tables(ngon_obj, ngon_triangles, shell_settings_from(ngon_obj.cloth_next))['bend'] == (100, 100, 100)
bpy.context.view_layer.objects.active = obj
ngon_obj.select_set(False)

shared = bpy.data.objects.new('Shared test', mesh)
bpy.context.collection.objects.link(shared)
for action in (lambda: data.initialize(obj), lambda: data.write_owners(obj, (0, token))):
    try:
        action()
    except ZoneError:
        pass
    else:
        raise AssertionError('Shared meshes must refuse ownership mutation')
bpy.data.objects.remove(shared, do_unlink=True)

# Ownership does not depend on list ordering, and transfer replaces ownership.
bpy.ops.clothnext.add_material_zone()
second = obj.cloth_next.material_zones[1]
second_token, second_identity = second.token, second.identity
data.write_owners(obj, (0, second_token))
obj.cloth_next.material_zones.move(1, 0)
assert data.validate_ownership(obj) == (0, second_token)
bpy.ops.clothnext.remove_material_zone(identity=second_identity)
assert data.read_owners(mesh) == (0, 0)
data.write_owners(obj, (0, token))
save_path = Path(__file__).resolve().parents[1] / '.tmp_verify' / 'material-zones-smoke.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(save_path))
bpy.ops.wm.open_mainfile(filepath=str(save_path))
obj = bpy.data.objects['Zone Smoke']
mesh = obj.data
bpy.context.view_layer.objects.active = obj
assert obj.cloth_next.material_zones[0].identity == identity
assert data.validate_ownership(obj) == (0, token)
assert data.export_tables(obj, triangles, shell_settings_from(obj.cloth_next))['bend'] == (10, 10, 100, 100)

# Same mesh, overlapping layers, both face centers exactly aligned on screen.
# Front winding is reversed: visibility must not reject back-facing normals.
bpy.ops.clothnext.clear_material_zone_selections()
mesh.clear_geometry()
mesh.from_pydata(((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0),
                  (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1)), (),
                 ((3, 2, 1, 0), (4, 5, 6, 7)))
try:
    data.validate_ownership(obj)
except ZoneError:
    pass
else:
    raise AssertionError('Topology changes must refuse old ownership')
bpy.ops.clothnext.clear_material_zone_selections()
assert data.read_owners(mesh) == (0, 0)
# Remove default cube to keep the test scene deterministic.
for other in list(bpy.context.scene.objects):
    if other != obj:
        bpy.data.objects.remove(other, do_unlink=True)
bpy.context.view_layer.update()
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
class SelectorHarness:
    _rebuild = selector.CLOTHNEXT_OT_edit_material_zone_selection._rebuild
    _raycast = selector.CLOTHNEXT_OT_edit_material_zone_selection._raycast
    _project = selector.CLOTHNEXT_OT_edit_material_zone_selection._project
    _visible_candidates = selector.CLOTHNEXT_OT_edit_material_zone_selection._visible_candidates


operator = SelectorHarness()
operator._obj, operator._space = obj, area.spaces.active
operator._topology = data.signature(mesh)
operator._rebuild(bpy.context)
for origin in (Vector((0, 0, 10)), Vector((0, 0, 100))):
    for index, expected in ((0, True), (1, False)):
        direction = (operator._centers[index] - origin).normalized()
        assert visible_center(operator._centers[index], origin, direction,
                              operator._raycast, (True, index), operator._epsilon) == expected
# Another visible object occludes both layers.
cover = bpy.data.meshes.new('Occluder')
cover.from_pydata(((-2, -2, 1), (2, -2, 1), (2, 2, 1), (-2, 2, 1)), (), ((0, 1, 2, 3),))
blocker = bpy.data.objects.new('Occluder', cover)
bpy.context.collection.objects.link(blocker)
bpy.context.view_layer.update()
operator._rebuild(bpy.context)
assert not visible_center(operator._centers[0], Vector((0, 0, 10)), Vector((0, 0, -1)), operator._raycast, (True, 0), operator._epsilon)
blocker.hide_set(True)
bpy.context.view_layer.update()
operator._rebuild(bpy.context)
assert visible_center(operator._centers[0], Vector((0, 0, 10)), Vector((0, 0, -1)), operator._raycast, (True, 0), operator._epsilon)

# Blender cannot update RegionView3D matrices without a live drawing window.
# Use exact matrices with Blender's real view3d_utils and evaluated BVH cache.
operator._region = SimpleNamespace(width=800, height=800)
operator._cursor, operator._radius = (400, 400), 40
view = Matrix.Translation((0, 0, -10))
ortho = Matrix.Diagonal((.5, .5, -.01, 1))
persp = Matrix(((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, -1.002, -.2002), (0, 0, -1, 0)))
for perspective, projection in ((False, ortho), (True, persp)):
    operator._rv3d = SimpleNamespace(view_matrix=view, perspective_matrix=projection @ view,
                                     is_perspective=perspective, view_perspective='PERSP' if perspective else 'ORTHO')
    operator._view_key = None
    assert operator._visible_candidates() == (0,), perspective
    assert operator._points[0] == operator._points[1]
    assert operator._visible_candidates() == (0,)  # cached visibility result
cloth_next.unregister()
assert not selector._sessions
print('MATERIAL_ZONES_SMOKE_OK: save/load, transfer, deletion, quad/ngon mapping, exact 10/100 boundary, topology/shared-mesh refusal, reversed-normal same-mesh layers, scene occluder, PERSP/ORTHO center projection and occlusion')
