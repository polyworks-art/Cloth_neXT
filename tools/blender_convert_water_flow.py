"""Read-only FLIP bake conversion and source-scene fixture in one .gaia file."""
import bpy
import io
import json
import numpy as np
from pathlib import Path
import sys
import time
import zipfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from cloth_next.blender.water_flow import FLIPWaterFlowProvider
from cloth_next.gaia.water_field import reconstruct, grid_dimensions, fingerprint
from cloth_next.gaia.container import write_water_container, GaiaContainer

output=Path(sys.argv[sys.argv.index('--')+1])
scene=bpy.context.scene
original=scene.frame_current
domain=scene.objects['FLIP Domain']
frames=range(1,251)
started=time.monotonic()
provider=FLIPWaterFlowProvider(bpy.context,domain,frames)
dimensions=grid_dimensions(provider.minimum,provider.maximum,24)
fps=scene.render.fps/scene.render.fps_base
metadata={'provider':provider.identity,'fps':fps,'frames':[1,250],
          'dimensions':dimensions,'algorithm':'trilinear-splat-support-v1',
          'field_format':'gaia-0.23','physical_velocity_units':'m/s'}
metadata['fingerprint']=fingerprint(metadata)
statistics=[]
def sequence():
    for frame in frames:
        begin=time.monotonic()
        p,v=provider.sample(frame)
        field=reconstruct(p,v,provider.minimum,provider.maximum,dimensions)
        statistics.append({'frame':frame,'samples':len(p),'seconds':time.monotonic()-begin,
                           'mean_velocity':v.mean(0).tolist(),'max_speed':float(np.linalg.norm(v,axis=1).max()),
                           'occupied_nodes':int(np.count_nonzero(field.influence))})
        if frame%25==0: print('WATER FRAME',frame,flush=True)
        yield frame,(frame-1)/fps,field
try:
    reuse=False
    if output.is_file():
        with GaiaContainer(output) as cached:
            reuse=cached.manifest['water']['fingerprint']==metadata['fingerprint']
    if not reuse: write_water_container(output,metadata,sequence())
finally:
    scene.frame_set(original)
with zipfile.ZipFile(output,'a',compression=zipfile.ZIP_DEFLATED) as archive:
    if not reuse:
        archive.writestr('conversion.json',json.dumps({'seconds':time.monotonic()-started,
                         'mode':provider.mode,'statistics':statistics}))
    # Snapshot for a real CUDA test, without saving or editing the user scene.
    for name in ('Plane','Cylinder'):
        obj=scene.objects.get(name)
        if obj is None or 'fixture/'+name+'.npz' in archive.namelist(): continue
        mesh=obj.data
        p=np.asarray([tuple(obj.matrix_world @ v.co) for v in mesh.vertices])
        mesh.calc_loop_triangles()
        tri=np.asarray([t.vertices[:] for t in mesh.loop_triangles])
        buffer=io.BytesIO()
        np.savez(buffer,positions=p,triangles=tri)
        archive.writestr('fixture/'+name+'.npz',buffer.getvalue())
print('WATER CONVERSION COMPLETE',output,flush=True)
