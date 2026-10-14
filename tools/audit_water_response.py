"""Measure local prepared water at current baked cloth positions, read-only."""
import sys,json,csv
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import GaiaContainer
OUT=ROOT/'dist/water-response'
def sample(array,lo,hi,points):
    dims=np.array(array.shape[:3][::-1])
    q=(points-lo)/(hi-lo)*(dims-1)
    inside=np.all((q>=0)&(q<=dims-1),axis=-1)
    q=np.clip(q,0,dims-1);i=np.minimum(q.astype(int),dims-2);f=q-i
    result=np.zeros((len(points),)+array.shape[3:])
    for x in range(2):
        for y in range(2):
            for z in range(2):
                w=np.prod(np.where([x,y,z],f,1-f),axis=1)
                val=array[i[:,2]+z,i[:,1]+y,i[:,0]+x]
                result+=val*w.reshape((len(w),)+(1,)*(result.ndim-1))
    result[~inside]=0
    return result
def alignment(a,b):
    norm=np.linalg.norm(a,axis=-1)*np.linalg.norm(b,axis=-1)
    return np.divide(np.sum(a*b,axis=-1),norm,out=np.full(norm.shape,np.nan),where=norm>1e-9)
def main():
    data=np.load(OUT/'real-cloth.npz');p=data['positions'];n=data['normals'];pins=data['pins']
    v=np.gradient(p,1/24,axis=0);a=np.gradient(v,1/24,axis=0)
    water=np.zeros_like(p);occupancy=np.zeros(p.shape[:2])
    with GaiaContainer(r'F:\16_Cloth_neXt\cache_AD\Plane_WaterFlow.gaia') as cache:
        (OUT/'water-manifest.json').write_text(json.dumps(cache.manifest,indent=2))
        for j,record in enumerate(cache.manifest['frames']):
            frame=record['frame']
            if not 1<=frame<=len(p):continue
            field=cache.frame(j);lo=np.array(field.minimum);hi=np.array(field.maximum)
            water[frame-1]=sample(field.contribution,lo,hi,p[frame-1])
            occupancy[frame-1]=sample(field.influence,lo,hi,p[frame-1])
    speed=np.linalg.norm(water,axis=-1)
    score=np.mean(speed*(occupancy>.01),axis=0);score[pins]=-1
    # Initial regular patch extremes identify an edge independently of response.
    initial=p[0];edge=np.any(np.isclose(initial,initial.min(axis=0),atol=1e-5)|np.isclose(initial,initial.max(axis=0),atol=1e-5),axis=1)
    # Constant X plane does not make every vertex an edge.
    axes=np.ptp(initial,axis=0)>1e-4
    margin=np.minimum(initial-initial.min(axis=0),initial.max(axis=0)-initial)
    interior_mask=np.all(margin[:,axes]>=.2,axis=1)&~pins
    interior=int(np.argmax(np.where(interior_mask,score,-1)))
    edge=np.any((np.isclose(initial,initial.min(axis=0),atol=1e-5)|np.isclose(initial,initial.max(axis=0),atol=1e-5))[:,axes],axis=1)
    edge_score=np.where(edge&~pins,score,-1);edge_id=int(np.argmax(edge_score))
    selected={'wet_free':interior,'wet_edge':edge_id,'pinned':int(np.flatnonzero(pins)[0])}
    reports={}
    for label,k in selected.items():
        w=water[:,k];c=v[:,k];r=w-c;ws=np.linalg.norm(w,axis=1);cs=np.linalg.norm(c,axis=1);rs=np.linalg.norm(r,axis=1)
        align=alignment(c,w);accalign=alignment(a[:,k],r);ratio=np.divide(cs,ws,out=np.zeros_like(cs),where=ws>1e-6)
        rows=[]
        for j in range(len(p)):
            rows.append([j+1,j/24,*p[j,k],*w[j],*c[j],*r[j],ws[j],cs[j],rs[j],*a[j,k],align[j],accalign[j],ratio[j],occupancy[j,k],*n[j,k],alignment(n[j,k],w[j])])
        header=['frame','time_s']+[f'{prefix}_{axis}' for prefix in ['position','water','cloth','relative'] for axis in 'xyz']+['water_speed','cloth_speed','relative_speed']+[f'acceleration_{axis}' for axis in 'xyz']+['flow_alignment','acceleration_alignment','velocity_ratio','occupancy']+[f'normal_{axis}' for axis in 'xyz']+['normal_flow_cosine']
        with (OUT/f'real-{label}.csv').open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(header);writer.writerows(rows)
        valid=(occupancy[:,k]>.01)&(ws>.05)
        reports[label]={'vertex':k,'pin':bool(pins[k]),'wet_frames':int(valid.sum()),'mean_water_speed_wet':float(ws[valid].mean()) if valid.any() else None,'mean_cloth_speed_wet':float(cs[valid].mean()) if valid.any() else None,'mean_flow_alignment_wet':float(np.nanmean(align[valid])) if valid.any() else None,'mean_velocity_ratio_wet':float(ratio[valid].mean()) if valid.any() else None,'minimum_relative_speed_wet':float(rs[valid].min()) if valid.any() else None,'peak_acceleration':float(np.linalg.norm(a[:,k],axis=1).max()),'frames':{str(j+1):dict(zip(header,rows[j])) for j in [49,99,149,199,249]}}
    np.savez_compressed(OUT/'real-local-response.npz',positions=p,velocity=v,acceleration=a,water=water,occupancy=occupancy,selected=list(selected.values()))
    (OUT/'real-summary.json').write_text(json.dumps(reports,indent=2,allow_nan=True,default=lambda x:x.item()))
    print(json.dumps({k:{a:b for a,b in val.items() if a!='frames'} for k,val in reports.items()},indent=2),flush=True)
if __name__=='__main__':main()
