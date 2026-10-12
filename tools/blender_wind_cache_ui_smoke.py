"""UI fingerprints must not capture Wind meshes; validation must track edits."""
import sys,cProfile
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.blender import registration,solver_test,wind_obstacles,validation_state
registration.register()
try:
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add()
    cloth=bpy.context.object
    bpy.ops.clothnext.add_physics(); bpy.ops.clothnext.set_object_type(role='CLOTH')
    bpy.ops.mesh.primitive_cube_add(location=(2,0,0))
    obstacle=bpy.context.object
    bpy.ops.object.empty_add()
    force=bpy.context.object; force.cloth_next.enabled=True; force.cloth_next.role='FORCE'
    force.cloth_next.force.wind_local=True; force.cloth_next.force.wind_obstacles='SCENE'
    bpy.context.view_layer.update()
    profile=cProfile.Profile(); profile.enable()
    before=solver_test.cheap_settings_fingerprint(bpy.context)
    for _ in range(100):
        assert solver_test.cheap_settings_fingerprint(bpy.context)==before
    profile.disable()
    assert before is not None, 'No valid settings fingerprint was exercised'
    forbidden={'to_mesh','calc_loop_triangles','foreach_get'}
    assert not any(any(name in str(entry.code) for name in forbidden) for entry in profile.getstats()), 'UI path scanned Wind mesh data'
    snapshot=solver_test.validate_scene(bpy.context)
    assert validation_state.record_for(cloth).state is validation_state.ValidationState.VALID
    obstacle.data.vertices[0].co.z+=.2; obstacle.data.update(); bpy.context.view_layer.update()
    assert validation_state.record_for(cloth).state is validation_state.ValidationState.DIRTY, 'Scene-only obstacle edit did not dirty cache'
    assert solver_test.cheap_settings_fingerprint(bpy.context)==before, 'Geometry entered property-only fingerprint'
    changed=solver_test.validate_scene(bpy.context)
    assert changed.geometry_fingerprint!=snapshot.geometry_fingerprint, 'Validation missed obstacle mesh edit'
    assert changed.settings_fingerprint==snapshot.settings_fingerprint
    obstacle.location.x+=1; bpy.context.view_layer.update()
    assert solver_test.cheap_settings_fingerprint(bpy.context)!=before, 'Obstacle transform missed by settings fingerprint'
    bpy.ops.mesh.primitive_plane_add(location=(0,4,0))
    bpy.ops.clothnext.add_physics(); bpy.ops.clothnext.set_object_type(role='CLOTH')
    multi=solver_test.validate_scene(bpy.context)
    properties=solver_test.cheap_settings_fingerprint(bpy.context)
    obstacle.data.vertices[1].co.x+=.1; obstacle.data.update(); bpy.context.view_layer.update()
    assert solver_test.cheap_settings_fingerprint(bpy.context)==properties
    updated=solver_test.validate_scene(bpy.context)
    assert updated.geometry_fingerprint!=multi.geometry_fingerprint, 'Multi-target validation missed Wind obstacle edit'
    print('CLOTH_NEXT_WIND_CACHE_UI_SMOKE_PASS')
finally: registration.unregister()
