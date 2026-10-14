"""Compare bounded direct-cache sampling against the existing real FLIP loader."""
import argparse,json,sys,time
from pathlib import Path
import bpy,addon_utils,numpy as np
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--repo',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--transform',choices=['original','rotated','scaled'],default='original')
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);sys.path.insert(0,str(a.repo))
addon_utils.enable('cloth_next',default_set=True)
repos=bpy.context.preferences.extensions.repos
if not any(r.module=='superhivemarket_com' for r in repos):
    repos.new(name='FLIP stream validation',module='superhivemarket_com')
addon_utils.enable('bl_ext.superhivemarket_com.flip_fluids_addon',default_set=True)
bpy.ops.wm.open_mainfile(filepath=str(a.source))
from cloth_next.blender.water_flow import FLIPWaterFlowProvider
from cloth_next.gaia.water_field import reconstruct,reconstruct_chunks
domain=next(o for o in bpy.context.scene.objects if getattr(getattr(o,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN')
if a.transform!='original':
    from mathutils import Matrix
    change=Matrix.Rotation(.4,4,'Z') if a.transform=='rotated' else Matrix.Diagonal((1.2,.8,1.1,1.))
    domain.matrix_world=Matrix.Translation((1.,-2.,.5))@change@domain.matrix_world
    bpy.context.view_layer.update()
provider=FLIPWaterFlowProvider(bpy.context,domain,range(1,2),hash_sources=False)
positions,velocities=provider.sample(1)
sample=provider.sample_stream(1)
chunks=list(sample.chunks())
direct_p=np.concatenate([p for p,v in chunks]);direct_v=np.concatenate([v for p,v in chunks])
assert positions.shape==direct_p.shape
position_error=float(np.abs(positions-direct_p).max())
velocity_error=float(np.abs(velocities-direct_v).max())
assert position_error<1e-6 and velocity_error==0
dimensions=(100,100,51)
full=reconstruct(positions,velocities,provider.minimum,provider.maximum,dimensions,max_speed=None)
streamed=reconstruct_chunks(sample.chunks(),provider.minimum,provider.maximum,dimensions,max_speed=None)
np.testing.assert_allclose(full.velocity,streamed.velocity,rtol=1e-6,atol=1e-6)
np.testing.assert_allclose(full.influence,streamed.influence,rtol=1e-6,atol=1e-6)
a.output.write_text(json.dumps(dict(result='PASS',particles=len(positions),position_max_error=position_error,
    velocity_max_error=velocity_error,field_velocity_max_error=float(np.abs(full.velocity-streamed.velocity).max()),
    field_influence_max_error=float(np.abs(full.influence-streamed.influence).max())),indent=2))
