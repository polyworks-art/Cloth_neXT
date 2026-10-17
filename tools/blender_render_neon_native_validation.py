"""Diagnostic false-colour native trajectory views; no source scene edits."""
import argparse,json,sys
from pathlib import Path
import bpy,numpy as np
from mathutils import Vector
p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--all-frames',action='store_true');p.add_argument('--views',nargs='+',choices=('front','side'),default=['front','side'])
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);sys.path.insert(0,str(a.repo))
from cloth_next.ppf.schema import envelope
verified=json.loads((a.run/'verification.json').read_text());assert verified['result']=='PASS';native=Path(verified['native_output']);mapping=envelope.loads_envelope((native.parent/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2);tri=np.fromfile(native.parent/'bin/tri.bin',dtype='<u8').reshape(-1,3)
scene_path=next((a.run/'cache/.cloth_next_export/scene').rglob('scene.cbor'));objects={o['name']:o for part in envelope.loads_envelope(scene_path.read_bytes(),envelope.KIND_SCENE,schema_version=2) for o in part['object']};report=json.loads((a.run/'report.json').read_text());first,last=report['range'];a.output.mkdir(parents=True,exist_ok=True)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene;scene.render.engine='BLENDER_WORKBENCH';scene.display.shading.light='STUDIO';scene.display.shading.color_type='OBJECT';scene.display.shading.show_shadows=True;scene.display.shading.show_cavity=True;scene.display.shading.cavity_type='BOTH';scene.display.shading.background_type='WORLD';scene.world.color=(.055,.065,.08);scene.render.resolution_x=720;scene.render.resolution_y=1000;scene.render.resolution_percentage=100;scene.render.image_settings.file_format='PNG';scene.render.film_transparent=False
camera_data=bpy.data.cameras.new('DiagnosticCamera');camera=bpy.data.objects.new('DiagnosticCamera',camera_data);scene.collection.objects.link(camera);scene.camera=camera;camera_data.type='ORTHO';camera_data.ortho_scale=1.65
base=np.fromfile(native/'vert_0.bin',dtype='<f4').reshape(-1,3);owner=np.full(len(base),-1);entries=list(objects.items());colours=[(.10,.40,.80,1),(.65,.24,.65,1),(.10,.65,.55,1)];garments=[];generated=[]
for i,(name,obj) in enumerate(entries):
 if obj['uuid'] in mapping:owner[mapping[obj['uuid']]]=i
for i,(name,obj) in enumerate(entries):
 if obj['uuid'] not in mapping:continue
 ids=np.array(mapping[obj['uuid']],int);faces=tri[(owner[tri]==i).all(1)]
 if not len(faces):continue
 lookup=np.full(len(base),-1);lookup[ids]=np.arange(len(ids));vertices=base[ids][:,[0,2,1]]*[1,-1,1]
 mesh=bpy.data.meshes.new(name);mesh.from_pydata(vertices.tolist(),[],lookup[faces].tolist());mesh.update();ob=bpy.data.objects.new(name,mesh);scene.collection.objects.link(ob)
 if name.startswith('S04_NEON_Garments'):ob.color=colours[min(len(garments),2)];garments.append(vertices)
 else:ob.color=(.42,.45,.49,1)
 generated.append((ob,ids))
cloth=np.concatenate(garments);center=Vector((cloth.min(0)+cloth.max(0))/2);views={'front':Vector((0,-4,.1)),'side':Vector((4,-.2,.1))};records=[]
for index in (range(last-first+1) if a.all_frames else sorted(set((0,(last-first)//2,last-first)))):
 positions=np.fromfile(native/f'vert_{index}.bin',dtype='<f4').reshape(-1,3)
 for ob,ids in generated:
  vertices=positions[ids][:,[0,2,1]]*[1,-1,1];ob.data.vertices.foreach_set('co',vertices.astype('f4').ravel());ob.data.update()
 for name,direction in views.items():
  if name not in a.views:continue
  camera.location=center+direction;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler();path=a.output/f'frame-{first+index}-{name}.png';scene.render.filepath=str(path);bpy.ops.render.render(write_still=True);records.append({'frame':first+index,'view':name,'path':str(path)})
(a.output/'views.json').write_text(json.dumps({'scope':'False-colour native geometry, identical camera/framing. Not a beauty render, material test, or continuous collision proof. Playback separately verified.','views':records},indent=2))
