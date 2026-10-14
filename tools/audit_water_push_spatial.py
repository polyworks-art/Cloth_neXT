"""Host-only spatial/representability audit. Frozen 48 Hz / tau .10 / BDF2."""
import sys,json,time
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from scipy.ndimage import distance_transform_edt
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.gaia.water_push_prototype import quadratic_velocity,discrete_push
from tools.audit_water_response import sample
OUT=ROOT/'dist/water-push-spatial';OUT.mkdir(exist_ok=True)
BUDGET={'relative_mean':.05,'relative_p95':.10,'relative_max':.20,'direction_max_degrees':5.,'meaningful_acceleration_m_s2':.1,'dry_vertex_max_m_s2':1e-6}

def bounds(p,v,w,a,gravity=False):
    window=1/48+.01
    pad=.001+(np.abs(v).max(axis=0)+np.abs(w).max(axis=0))*window+.5*(np.abs(a).max(axis=0)+[0,0,9.81 if gravity else 0])*window**2
    return p.min(axis=0)-pad,p.max(axis=0)+pad,pad

def layout(lo,hi,res):
    shape=np.maximum(2,np.ceil((hi-lo)/(hi-lo).max()*(res-1)).astype(int)+1)
    return tuple(int(x) for x in shape[::-1])

def idw(p,a,lo,hi,shape):
    d,h,w=shape
    z,y,x=np.meshgrid(np.linspace(lo[2],hi[2],d),np.linspace(lo[1],hi[1],h),np.linspace(lo[0],hi[0],w),indexing='ij')
    nodes=np.stack([x,y,z],axis=-1).reshape(-1,3)
    distance,index=cKDTree(p).query(nodes,k=4)
    weights=1/np.maximum(distance**2,1e-10);weights/=weights.sum(axis=1,keepdims=True)
    return np.sum(a[index]*weights[:,:,None],axis=1).reshape((*shape,3)).astype('f4')

def splat(p,a,lo,hi,shape):
    dims=np.array(shape[::-1]);q=(p-lo)/(hi-lo)*(dims-1);i=np.minimum(q.astype(int),dims-2);f=q-i
    values=np.zeros((*shape,3));mass=np.zeros(shape)
    for x in range(2):
        for y in range(2):
            for z in range(2):
                weight=np.prod(np.where([x,y,z],f,1-f),axis=1);idx=(i[:,2]+z,i[:,1]+y,i[:,0]+x)
                np.add.at(mass,idx,weight)
                for c in range(3):np.add.at(values[...,c],idx,weight*a[:,c])
    occupied=mass>1e-12;values[occupied]/=mass[occupied,None]
    # Explicit nearest occupied-node extension, no hidden Gaussian blur.
    if not occupied.all():
        nearest=distance_transform_edt(~occupied,return_distances=False,return_indices=True)
        values[~occupied]=values[tuple(nearest[:,~occupied])]
    return values.astype('f4')

def metrics(a,delivered,w,occupancy):
    absolute=np.linalg.norm(delivered-a,axis=1);norm=np.linalg.norm(a,axis=1);valid=norm>=BUDGET['meaningful_acceleration_m_s2']
    relative=absolute[valid]/norm[valid]
    den=np.linalg.norm(delivered[valid],axis=1)*norm[valid]
    cosine=np.divide(np.sum(delivered[valid]*a[valid],axis=1),den,out=np.zeros_like(den),where=den>1e-12)
    direction=np.degrees(np.arccos(np.clip(cosine,-1,1)))
    water_norm=np.linalg.norm(w,axis=1);unit=np.divide(w,water_norm[:,None],out=np.zeros_like(w),where=water_norm[:,None]>1e-12)
    projected=np.sum((delivered-a)*unit,axis=1);dry=occupancy==0
    result={'absolute_mean_m_s2':float(absolute.mean()),'absolute_p95_m_s2':float(np.percentile(absolute,95)),'absolute_max_m_s2':float(absolute.max()),'relative_mean':float(relative.mean()) if len(relative) else 0.,'relative_p95':float(np.percentile(relative,95)) if len(relative) else 0.,'relative_max':float(relative.max()) if len(relative) else 0.,'direction_max_degrees':float(direction.max()) if len(direction) else 0.,'projected_flow_error_max_m_s2':float(np.abs(projected).max()),'dry_vertex_max_m_s2':float(np.linalg.norm(delivered[dry],axis=1).max()) if dry.any() else 0.}
    result['passed']=all(result[key]<=BUDGET[key] for key in ['relative_mean','relative_p95','relative_max','direction_max_degrees','dry_vertex_max_m_s2'])
    return result

def water(kind,p):
    u=(p[:,1]+1)/2;w=np.zeros_like(p);support=np.ones(len(p))
    if kind=='step':w[:,0]=np.where(u<.5,.5,2.)
    elif kind=='smooth':w[:,0]=.5+1.5*u
    elif kind=='direction':w[:,0]=1-u;w[:,2]=u
    else:
        support=(np.abs(p[:,1])<=.25).astype(float);w[:,0]=2*support
    return w,support

def historical_replay():
    old=ROOT/'dist/water-push-cadence';poses=np.load(old/'constrained-positions.npz')['positions'];rows=json.loads((old/'constrained.json').read_text())['rows'];records=[]
    for j,pall in enumerate(poses):
        p=pall[162:243];hist=poses[max(0,j-2):j+1,162:243];times=[next(r['time'] for r in rows if r['case']=='heterogeneous_push' and r['frame']==k) for k in range(max(0,j-2),j+1)]
        v=np.zeros_like(p) if j==0 else ((hist[-1]-hist[-2])/(times[-1]-times[-2]) if j==1 else quadratic_velocity(hist,times))
        w=np.zeros_like(p);w[:,0]=.5+np.clip((-p[:,2]-8)/2,0,1);a=discrete_push(w,v,np.ones(len(p)),.1,1/24)
        lo=p.min(axis=0)-.1;hi=p.max(axis=0)+.1
        grid=idw(p,a,lo,hi,(13,13,5));delivered=sample(grid,lo,hi,p)
        records.append({'frame':j,'max_error_m_s2':float(np.linalg.norm(delivered-a,axis=1).max())})
    return {'control_hz':24,'purpose':'Historical replay only, not a change of the frozen new 48-Hz law','original_recorded_max_m_s2':max(r['maximum_grid_delivery_error_m_s2'] for r in rows if r['case']=='heterogeneous_push'),'reconstructed_max_m_s2':max(r['max_error_m_s2'] for r in records),'records':records}

def run():
    # Record acceptance thresholds BEFORE producing final test results.
    (OUT/'declared-budget.json').write_text(json.dumps(BUDGET,indent=2))
    n=17;p0=np.array([[0,y,z] for z in np.linspace(0,2,n) for y in np.linspace(-1,1,n)])
    results=[];datasets={};delivered_datasets={};constant_checks=[]
    for state in ['free','soft_pinned','gravity_pinned']:
        history=[]
        for t in [0,1/48,2/48]:
            p=p0.copy();u=(p[:,1]+1)/2;s=p[:,2]/2
            if state=='free':p[:,0]+=.15*t
            else:p[:,0]+=.04*np.sin(np.pi*u)*(1-s)+.25*t*(1-s)
            if state=='gravity_pinned':p[:,2]-=.6*t*(1-s)
            history.append(p)
        p=history[-1];v=quadratic_velocity(history,[0,1/48,2/48])
        for kind in ['step','smooth','direction','localized']:
            w,occupancy=water(kind,p);relative=w-v;a=discrete_push(w,v,occupancy,.1,1/48)
            key=state+'_'+kind
            # Target dataset is persisted before construction.
            datasets[key]=np.concatenate([p,v,w,occupancy[:,None],relative,a/48,a],axis=1)
            np.savez_compressed(OUT/'desired-vertices.npz',**datasets)
            lo,hi,pad=bounds(p,v,w,a,state=='gravity_pinned')
            for res in [32,64,100,150]:
                shape=layout(lo,hi,res)
                for name,fn in [('idw4',idw),('normalized_splat',splat)]:
                    started=time.perf_counter();grid=fn(p,a,lo,hi,shape);elapsed=time.perf_counter()-started
                    delivered=sample(grid,lo,hi,p)
                    delivered_datasets[f'{key}_{name}_{res}']=delivered
                    encoding=time.perf_counter();encoded=grid.tobytes();encoding=time.perf_counter()-encoding
                    item={'state':state,'field':kind,'method':name,'resolution':res,'dimensions_dhw':shape,'field_bytes':grid.nbytes,'construction_s':elapsed,'host_encoding_s':encoding,'motion_padding_xyz_m':pad.tolist(),'domain_min':lo.tolist(),'domain_max':hi.tolist(),**metrics(a,delivered,w,occupancy)}
                    if kind=='localized':
                        # Probe actual piecewise-linear cloth edges between wet
                        # and dry samples, beyond two node spacings from the
                        # analytic boundary (not just one-cell boundary bleed).
                        yy=np.linspace(.250001,.499999,100);row=p[8*n:9*n]
                        probes=np.column_stack([np.interp(yy,row[:,1],row[:,0]),yy,np.interp(yy,row[:,1],row[:,2])])
                        values=sample(grid,lo,hi,probes)
                        spacing=(hi[1]-lo[1])/(shape[1]-1)
                        far=yy>.25+2*spacing
                        item['off_vertex_dry_probe_max_m_s2']=float(np.linalg.norm(values,axis=1).max())
                        item['off_vertex_far_dry_probe_max_m_s2']=float(np.linalg.norm(values[far],axis=1).max()) if far.any() else None
                    results.append(item);del encoded
                    if kind=='smooth':
                        const=np.tile([2.,-3.,4.],(len(p),1));cg=fn(p,const,lo,hi,shape)
                        constant_checks.append({'state':state,'method':name,'resolution':res,'max_error_m_s2':float(np.linalg.norm(sample(cg,lo,hi,p)-const,axis=1).max())})
    # Connected folded sheet: the two panels share the top hinge. A paired
    # material-point velocity difference tapers to zero at the common hinge.
    panel=p0.copy();moving=np.zeros_like(panel);moving[:,0]=1-panel[:,2]/2
    # Persist the connected single-object topology, merging the shared hinge.
    connected_points=np.concatenate([panel,panel[:(n-1)*n]])
    faces=[]
    def second(i):return i if i>=(n-1)*n else len(panel)+i
    for z in range(n-1):
        for y in range(n-1):
            i=z*n+y
            for face in [[i,i+1,i+n],[i+1,i+n+1,i+n]]:
                faces.append(face);faces.append([second(k) for k in reversed(face)])
    np.savez_compressed(OUT/'connected-folded-sheet.npz',positions=connected_points,triangles=np.array(faces),velocity=np.concatenate([np.zeros_like(panel),moving[:(n-1)*n]]),shared_hinge_vertices=np.arange((n-1)*n,n*n))
    w=np.tile([1.,0.,0.],(len(panel),1));still=np.zeros_like(panel)
    target0=discrete_push(w,still,np.ones(len(panel)),.1,1/48)
    target1=discrete_push(w,moving,np.ones(len(panel)),.1,1/48)
    lo,hi,pad=bounds(panel,moving,w,np.concatenate([target0,target1]),False)
    folded=[]
    for gap in [0.,.0002,.001,.005]:
        other=panel.copy();other[:,0]+=gap
        # At finite gaps this is a layered-panel host cross-section; at zero
        # gap it is the exact doubled-back folded-sheet limiting geometry.
        points=np.concatenate([panel,other]);a=np.concatenate([target0,target1]);vel=np.concatenate([still,moving])
        datasets[f'folded_gap_{gap}']=np.concatenate([points,vel,np.tile(w,(2,1)),np.ones((len(points),1)),np.tile(w,(2,1))-vel,a/48,a],axis=1)
        for res in [32,64,100,150]:
            shape=layout(lo,hi,res);grid=splat(points,a,lo,hi,shape);delivered=sample(grid,lo,hi,points)
            delivered_datasets[f'folded_gap_{gap}_{res}']=delivered
            folded.append({'gap_m':gap,'resolution':res,**metrics(a,delivered,np.tile(w,(2,1)),np.ones(len(points))),'layer_pair_delivery_difference_max':float(np.abs(delivered[:len(panel)]-delivered[len(panel):]).max())})
    np.savez_compressed(OUT/'desired-vertices.npz',**datasets)
    np.savez_compressed(OUT/'delivered-vertices.npz',**delivered_datasets)
    gain=float(-np.expm1(-(1/48)/.1)/(1/48));contradiction={'coincident_pair_water_velocity':[1,0,0],'cloth_velocities':[[0,0,0],[1,0,0]],'desired_accelerations_m_s2':[[gain,0,0],[0,0,0]],'unavoidable_minimax_absolute_error_m_s2':gain/2,'slow_layer_minimum_acceptable_acceleration_m_s2':.8*gain,'moving_layer_acceleration_to_preserve_1percent_gate_m_s2':.01*48,'shared_field_after_nominal_tick_fast_speed_ratio_if_slow_layer_max_error_passes':1+.8*gain/48,'near_layer_required_gradient_for_0_2mm_m_s2_per_m':gain/.0002,'cannot_be_fixed_by_resolution':True}
    result={'control_hz':48,'tau':.1,'estimator':'nonuniform quadratic/BDF2; unchanged','budgets':BUDGET,'target_columns':['position_x','position_y','position_z','estimated_velocity_x','estimated_velocity_y','estimated_velocity_z','water_velocity_x','water_velocity_y','water_velocity_z','occupancy','relative_x','relative_y','relative_z','desired_delta_v_x','desired_delta_v_y','desired_delta_v_z','desired_acceleration_x','desired_acceleration_y','desired_acceleration_z'],'primary_geometry':'17x17 (289 vertices) 2m square; three manufactured deforming host states, not new native dynamics','material_constants_reference':{'density':1,'young_mod':1000,'poisson':.4,'bend':.83,'strain_limit':.04,'soft_pin_strength':100},'sampling_semantics':'inclusive boundary-node trilinear weights, float32 stored vectors, zero outside; CPU accumulation not claimed bit-identical to GPU','historical_replay':historical_replay(),'matrix':results,'constant_preservation':constant_checks,'self_overlap':folded,'representability_proof':contradiction,'classification':'C for general deforming/self-overlapping cloth: single-group spatial field cannot represent coincident velocity-dependent targets','stop':'No further tuning, native heterogeneous/constrained calibration, real FLIP reconstruction or production development after mandatory representability failure.'}
    (OUT/'host-audit.json').write_text(json.dumps(result,indent=2));print(json.dumps({'cases':len(results),'passed':sum(r['passed'] for r in results),'historical_error':result['historical_replay']['reconstructed_max_m_s2'],'proof':contradiction},indent=2))

if __name__=='__main__':run()
