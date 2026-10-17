"""Blender Workbench evidence from native world meshes; creates a new scene only."""
import argparse
import json
import sys
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector

p = argparse.ArgumentParser()
p.add_argument('--repo', type=Path, required=True)
p.add_argument('--analysis', type=Path, required=True)
p.add_argument('--run', action='append', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0, str(a.repo))
from cloth_next.core.filesystem_paths import io_path
from cloth_next.ppf.schema import envelope

analysis = json.loads(a.analysis.read_text()); a.output.mkdir(parents=True, exist_ok=True)
runs = {}
for run in a.run:
    r = json.loads((run/'report.json').read_text()); native = io_path(Path(r['native_output']), reserved_length=1000)
    parts = envelope.loads_envelope(next((run/'cache/.cloth_next_export/scene').rglob('scene.cbor')).read_bytes(), envelope.KIND_SCENE, schema_version=2)
    objects = {o['name']: o for part in parts for o in part['object']}
    mapping = envelope.loads_envelope((native.parent/'map.pickle').read_bytes(), envelope.KIND_VERTEX_MAP, schema_version=2)
    tri = np.fromfile(native.parent/'bin/tri.bin', dtype='<u8').reshape(-1, 3).astype(int)
    runs[run.name] = (native, objects, mapping, tri)

def world(x):
    return x[:, [0, 2, 1]] * [1, -1, 1]

records = []
for pair_number, pair in enumerate(analysis['pairs']):
    main = pair['objects'][0]
    if pair['left'] not in runs or pair['right'] not in runs:
        continue
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene; scene.render.engine = 'BLENDER_WORKBENCH'
    shading = scene.display.shading; shading.light = 'STUDIO'; shading.color_type = 'VERTEX'
    shading.show_shadows = True; shading.show_cavity = True; shading.cavity_type = 'BOTH'
    shading.background_type = 'WORLD'; scene.world.color = (.035, .045, .06)
    scene.render.resolution_x = 1100; scene.render.resolution_y = 1000; scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    k = main['worst_frame']-130; generated = []; normals = None; center = None; marker_positions = {}
    for side, label in enumerate((pair['left'], pair['right'])):
        native, objects, mapping, tri = runs[label]
        raw = np.fromfile(native/f'vert_{k}.bin', dtype='<f4').reshape(-1, 3)
        for name, ob in objects.items():
            if ob['uuid'] not in mapping:
                continue
            ids = np.asarray(mapping[ob['uuid']], int); lookup = np.full(len(raw), -1); lookup[ids] = np.arange(len(ids))
            f = lookup[tri]; f = f[(f >= 0).all(1)]; vertices = world(raw[ids])
            mesh = bpy.data.meshes.new(label+' '+name); mesh.from_pydata(vertices.tolist(), [], f.tolist()); mesh.update()
            obj = bpy.data.objects.new(label+' '+name, mesh); scene.collection.objects.link(obj); obj.hide_render = bool(side)
            attr = mesh.color_attributes.new(name='MaterialVertexDeviation', type='FLOAT_COLOR', domain='POINT')
            colour = np.tile([.32, .35, .4, 1.], (len(ids), 1))
            if name.startswith('S04_NEON_Garments'):
                other_native, other_objects, other_mapping, _ = runs[(pair['right'], pair['left'])[side]]
                other_ids = np.asarray(other_mapping[other_objects[name]['uuid']], int)
                other_raw = np.fromfile(other_native/f'vert_{k}.bin', dtype='<f4').reshape(-1, 3)[other_ids]
                distance = np.linalg.norm(raw[ids]-other_raw, axis=1); q = np.clip(distance/.008, 0, 1)
                colour = np.column_stack([q, .25+.4*(1-q), 1-q, np.ones(len(ids))]); colour[distance > .008] = [1., .03, .15, 1.]
                if name == main['object']:
                    marker_positions[side] = Vector(vertices[main['worst_vertex']])
                if name == main['object'] and not side:
                    center = Vector(vertices[main['worst_vertex']]); normals = Vector(world(np.array([mesh.vertices[main['worst_vertex']].normal]))[0])
            attr.data.foreach_set('color', colour.astype('f4').ravel()); generated.append((obj, side))
    native, objects, mapping, tri = runs[pair['left']]
    # Red sphere marks the exact worst material vertex, not a claimed surface error.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=.0015, location=center)
    marker = bpy.context.object; marker.name = 'Worst material vertex '+str(main['worst_vertex']); marker.color = (1., .05, .05, 1.)
    marker.data.color_attributes.new(name='Marker', type='FLOAT_COLOR', domain='POINT')
    for item in marker.data.color_attributes.active_color.data: item.color = (1., .05, .05, 1.)
    camera_data = bpy.data.cameras.new('EvidenceCamera'); camera = bpy.data.objects.new('EvidenceCamera', camera_data); scene.collection.objects.link(camera); scene.camera = camera; camera_data.type = 'ORTHO'
    for view, direction, scale, target in [('context-front', Vector((0, -3, .1)), 1.5, Vector((0, 0, .75))), ('detail-front', Vector((0, -3, .1)), .20, center), ('detail-side', Vector((-3, 0, .1)), .20, center)]:
        camera_data.ortho_scale = scale; camera.location = target+direction; camera.rotation_euler = (target-camera.location).to_track_quat('-Z', 'Y').to_euler()
        for side, label in enumerate((pair['left'], pair['right'])):
            for obj, owner_side in generated: obj.hide_render = owner_side != side
            marker.location = marker_positions[side]
            path = a.output/f'pair-{pair_number}-frame-{main["worst_frame"]}-{view}-{side}.png'; scene.render.filepath = str(path); bpy.ops.render.render(write_still=True)
            records.append(dict(pair=[pair['left'], pair['right']], frame=main['worst_frame'], vertex=main['worst_vertex'], view=view, geometry=label, image=str(path)))
    scene['evidence_notes'] = 'Native world geometry. Blue=0, red>=8mm material vertex deviation. Sampled surface distances are separate. Marker identifies worst vertex. No source scene or physics changes.'
    for obj, side in generated:
        obj.hide_set(bool(side))
        obj.hide_render = bool(side)
    marker.location = marker_positions[0]
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.shading.color_type = 'VERTEX'
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
    bpy.ops.wm.save_as_mainfile(filepath=str(a.output/f'pair-{pair_number}.blend'))
(a.output/'views.json').write_text(json.dumps(records, indent=2))
