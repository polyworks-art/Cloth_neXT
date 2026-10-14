"""Read-only FLIP neighborhood and particle-ID cross-check for measured vertices."""
import sys,json
from pathlib import Path
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.blender.water_flow import FLIPWaterFlowProvider
from cloth_next.gaia.container import GaiaContainer
from tools.audit_water_response import sample
OUT=ROOT/'dist/water-response'
data=np.load(OUT/'real-local-response.npz');selected=data['selected'];positions=data['positions']
provider=FLIPWaterFlowProvider(bpy.context,bpy.context.scene.objects['FLIP Domain'],range(150,156),hash_sources=False)
provider.cache.enable_id_attribute=True
rows=[];tracked=[];matrices=[]
with GaiaContainer(r'F:\16_Cloth_neXt\cache_AD\Plane_WaterFlow.gaia') as container:
    for frame in range(150,156):
        p,v=provider.sample(frame);obj=provider.cache.get_cache_object();attr=obj.data.attributes.get('flip_id')
        # Hidden cache objects can retain an unevaluated identity matrix in background
        # Blender. Resolve the authored parent chain, following FLIP's public transform.
        matrix=np.array(obj.parent.matrix_world@obj.matrix_parent_inverse@obj.matrix_basis if obj.parent else obj.matrix_basis)
        local=np.empty(len(p)*3,dtype=np.float32);obj.data.vertices.foreach_get('co',local)
        p=local.reshape(-1,3)@matrix[:3,:3].T+matrix[:3,3]
        matrices.append({'frame':frame,'object_matrix':[list(r) for r in obj.matrix_world],'resolved_matrix':matrix.tolist(),'provider_matrix':provider.cache_matrix.tolist(),'particle_min':p.min(axis=0).tolist(),'particle_max':p.max(axis=0).tolist()})
        ids=np.empty(len(p),dtype=np.int32)
        if attr:attr.data.foreach_get('value',ids)
        field=container.frame(frame-1);lo=np.array(field.minimum);hi=np.array(field.maximum)
        for index in selected:
            cp=positions[frame-1,index];distance=np.linalg.norm(p-cp,axis=1);k=int(np.argmin(distance))
            near=distance<.15
            rows.append({'frame':frame,'vertex':int(index),'cloth_position':cp.tolist(),'nearest_distance':float(distance[k]),'particle_id':int(ids[k]) if attr else None,'particle_position':p[k].tolist(),'particle_velocity':v[k].tolist(),'field_at_particle':sample(field.contribution,lo,hi,p[k:k+1])[0].tolist(),'field_at_cloth':sample(field.contribution,lo,hi,cp[None])[0].tolist(),'neighbors':int(near.sum()),'neighborhood_mean_velocity':v[near].mean(axis=0).tolist() if near.any() else None})
        if frame==150:
            track_ids=[r['particle_id'] for r in rows if r['frame']==150]
        if attr:
            for identity in track_ids:
                matches=np.flatnonzero(ids==identity)
                tracked.append({'frame':frame,'particle_id':identity,'matches':len(matches),'position':p[matches[0]].tolist() if len(matches)==1 else None,'velocity':v[matches[0]].tolist() if len(matches)==1 else None})
(OUT/'particle-diagnostics.json').write_text(json.dumps({'nearest':rows,'tracked':tracked,'matrices':matrices},indent=2))
print('WATER_PARTICLE_DIAGNOSTICS_COMPLETE',flush=True)
