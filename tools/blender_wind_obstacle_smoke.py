# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender scene-geometry, animation and Wind-export regression."""
import json
import math
import sys
import time
import zlib
from pathlib import Path
from types import SimpleNamespace
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.blender import registration, wind_fields, wind_obstacles, solver_test
from cloth_next.gaia.wind_field import velocity
from cloth_next.ppf.schema import envelope

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
registration.register()
try:
    bpy.ops.object.empty_add()
    emitter = bpy.context.object; emitter.name = 'Wind'
    emitter.cloth_next.enabled = True; emitter.cloth_next.role = 'FORCE'
    force = emitter.cloth_next.force
    force.wind_local = True; force.wind_strength = 3; force.wind_radius = 3
    force.wind_resolution = 13; force.wind_show_vectors = True
    force.gravity_strength = 0; force.air_density = .1
    emitter.rotation_euler.y = math.pi/2
    bpy.ops.mesh.primitive_cube_add(location=(.5,0,0))
    obstacle = bpy.context.object; obstacle.name = 'Scene obstacle'
    obstacle.scale = (.25,.6,.6)
    points = np.array([[1.,0,0], [1.,1.3,0], [.1,.3,0], [.5,0,0]])
    scene = bpy.context.scene; scene.frame_set(1)
    force.wind_obstacles = 'NONE'; control_source = wind_fields.source_at(bpy.context, emitter)
    control = velocity(points,[control_source],1/24)
    force.wind_obstacles = 'COLLIDERS'; source = wind_fields.source_at(bpy.context, emitter)
    query = wind_obstacles.sampler(bpy.context,'COLLIDERS',(-3,-3,-3),(3,3,3))
    np.testing.assert_allclose(velocity(points,[source],1/24,query),control)
    force.wind_obstacles = 'SCENE'; source = wind_fields.source_at(bpy.context, emitter)
    query = wind_obstacles.sampler(bpy.context,'SCENE',(-3,-3,-3),(3,3,3),cached=True)
    modified = velocity(points,[source],1/24,query)
    assert modified[0,0] < control[0,0] * .9, 'No downstream Wind shadow'
    np.testing.assert_allclose(modified[1],control[1],atol=1e-9)
    assert abs(modified[2,1]) > 1e-3, 'No surface deflection'
    np.testing.assert_allclose(modified[3],[0,0,0],atol=1e-9)
    later = velocity(points,[source],.7,query)
    assert not np.allclose(modified[0,1:],later[0,1:]), 'Wake does not evolve with time'
    assert len(wind_obstacles._cache) == 1
    obstacle.scale.x = -.25; bpy.context.view_layer.update()
    mirrored = wind_obstacles.sampler(bpy.context,'SCENE',(-3,-3,-3),(3,3,3))
    np.testing.assert_allclose(velocity(points,[source],1/24,mirrored)[3],[0,0,0],atol=1e-9)
    obstacle.scale.x = .25; bpy.context.view_layer.update()
    # A non-Collider changes the field/cache identity only in Scene mode.
    identity = wind_obstacles.signature(bpy.context,{'SCENE'})
    obstacle.data.vertices[0].co.z += .1; obstacle.data.update(); bpy.context.view_layer.update()
    assert not wind_obstacles._cache, 'Geometry edit retained stale BVH'
    assert identity != wind_obstacles.signature(bpy.context,{'SCENE'})
    obstacle.data.vertices[0].co.z -= .1; obstacle.data.update()
    bpy.context.view_layer.objects.active = obstacle
    bpy.ops.clothnext.add_physics()
    bpy.ops.clothnext.set_object_type(role='COLLIDER')
    query = wind_obstacles.sampler(bpy.context,'COLLIDERS',(-3,-3,-3),(3,3,3))
    assert len(query.surfaces) == 1
    # Targets do not shield their own air field using a previous Bake cache.
    obstacle.cloth_next.role = 'CLOTH'
    assert not wind_obstacles.objects(bpy.context,'SCENE')
    # Scene-mode display can still sample air near a simulation target;
    # using its pose for display does not make it an air obstacle.
    assert wind_obstacles.objects(bpy.context,'SCENE',include_targets=True) == (obstacle,)
    obstacle.cloth_next.role = 'COLLIDER'
    bpy.ops.mesh.primitive_cube_add(location=(30,0,0))
    distant = bpy.context.object; distant.name = 'Distant scene object'
    nodes, lengths = wind_obstacles.preview_points(bpy.context,{'SCENE'},(-100,-100,-100),(100,100,100))
    assert len(nodes) <= 512 and len(lengths) == len(nodes)
    assert not ((nodes[:,0]>3)&(nodes[:,0]<27)).any(), 'Preview populated empty gaps between objects'
    collider_nodes,_ = wind_obstacles.preview_points(bpy.context,{'COLLIDERS'},(-100,-100,-100),(100,100,100))
    assert collider_nodes[:,0].max()<2, 'Collider-only display included an unconfigured scene object'
    bpy.data.objects.remove(distant,do_unlink=True)
    # Collection instances are also surfaces in Scene Geometry mode.
    collection = bpy.data.collections.new('Wind obstacle instance collection')
    template = bpy.data.objects.new('Instanced obstacle',obstacle.data.copy())
    template.location = (.5,0,0); template.scale = (.25,.6,.6)
    collection.objects.link(template)
    bpy.ops.object.empty_add()
    instance = bpy.context.object; instance.instance_type = 'COLLECTION'; instance.instance_collection = collection
    instance.location.x = 1
    obstacle.hide_viewport = True
    bpy.context.view_layer.update()
    instance_query = wind_obstacles.sampler(bpy.context,'SCENE',(-3,-3,-3),(3,3,3))
    assert any(abs(surface.center[0] - 1.5) < 1e-5 for surface in instance_query.surfaces), 'Instanced obstacle transform was not evaluated'
    bpy.data.objects.remove(instance,do_unlink=True)
    bpy.data.objects.remove(template,do_unlink=True)
    bpy.data.collections.remove(collection)
    obstacle.hide_viewport = False; bpy.context.view_layer.update()
    obstacle.location.y = 0; obstacle.keyframe_insert('location',frame=1)
    obstacle.location.y = 5; obstacle.keyframe_insert('location',frame=5)
    scene.frame_set(1); start = time.perf_counter(); a = wind_fields.preview(bpy.context)
    preview_seconds = time.perf_counter()-start
    scene.frame_set(5); b = wind_fields.preview(bpy.context)
    assert a.shape != b.shape or not np.allclose(a,b), 'Moving obstacle did not change timeline preview'
    source = wind_fields.source_at(bpy.context,emitter)
    query = wind_obstacles.sampler(bpy.context,'SCENE',(-3,-3,-3),(3,3,3))
    np.testing.assert_allclose(velocity(points,[source],5/24,query),control,atol=1e-9)
    scene.frame_set(3,subframe=.25)
    tree = {'scene':{'air-density':.1},'group':[[{},['Cloth'],['cloth']]]}
    payload = envelope.dumps_envelope(envelope.KIND_PARAM,tree,schema_version=2)
    encoded,_digest = wind_fields.apply_wind_payload(bpy.context,payload,SimpleNamespace(protocol_version='0.23'),1,5)
    assert scene.frame_current == 3 and scene.frame_subframe == .25
    result = envelope.loads_envelope(encoded,envelope.KIND_PARAM,schema_version=2)
    grid = result['force_field']['grids'][0]
    values = np.frombuffer(zlib.decompress(grid['data']),'<f4').reshape(grid['shape'])
    assert not np.allclose(values[0],values[-1]), 'Export froze animated obstacle geometry'
    out = ROOT/'.tmp_verify'; out.mkdir(exist_ok=True)
    (out/'wind-obstacle-smoke-param.cbor').write_bytes(encoded)
    # Static exports for the actual CUDA response comparison.
    obstacle.animation_data_clear(); obstacle.location.y = 0
    scene.frame_set(1); bpy.context.view_layer.update()
    obstacle.cloth_next.enabled = False
    for mode in ('NONE','COLLIDERS','SCENE'):
        force.wind_obstacles = mode
        data,_hash = wind_fields.apply_wind_payload(bpy.context,payload,SimpleNamespace(protocol_version='0.23'),1,9)
        (out/f'wind-obstacle-native-{mode.lower()}.cbor').write_bytes(data)
    scene.frame_set(3,subframe=.25)
    counts = (len(bpy.data.objects),len(bpy.data.meshes))
    check = solver_test._check_preparation_cancel; calls = [0]
    def cancel_during_geometry():
        calls[0] += 1
        if calls[0] == 16:
            raise solver_test.SessionCancelled()
    solver_test._check_preparation_cancel = cancel_during_geometry
    try:
        try:
            wind_fields.apply_wind_payload(bpy.context,payload,SimpleNamespace(protocol_version='0.23'),1,9)
        except solver_test.SessionCancelled:
            pass
        else:
            raise AssertionError('Obstacle sampling ignored Cancel')
    finally:
        solver_test._check_preparation_cancel = check
    assert scene.frame_current == 3 and scene.frame_subframe == .25
    assert counts == (len(bpy.data.objects),len(bpy.data.meshes)), 'Cancelled obstacle evaluation leaked Blender data'
    (out/'wind-obstacle-smoke.json').write_text(json.dumps({'result':'PASS',
        'control':control.tolist(),'modified':modified.tolist(),'preview_seconds':preview_seconds,
        'shape':grid['shape']},indent=2))
    print('CLOTH_NEXT_WIND_OBSTACLE_SMOKE_PASS',preview_seconds)
finally:
    registration.unregister()
    assert not wind_obstacles._cache
