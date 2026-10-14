"""Read completed scene caches using their exported world transforms; no scene mutation."""
import argparse,hashlib,json,sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.bake.pc2 import read_header,iter_frames
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--case',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
plans=[json.loads(path.read_text())['plan'] for path in (a.case/'.cloth_next_export/scene').rglob('metadata.json')
       if 'plan' in json.loads(path.read_text())]
assert len(plans)==1
plan=plans[0];records=[]
for target in plan['targets']:
    meta=next(json.loads(path.read_text()) for path in a.case.glob('*.meta.json')
        if json.loads(path.read_text())['identities']['object']['object_key']==target['name'])
    cache=a.case/meta['cache_file'];header=read_header(cache)
    assert meta['completion_state']=='complete' and header.frame_count==plan['frame_count']
    assert hashlib.sha256(cache.read_bytes()).hexdigest()==meta['cache_sha256']
    local=np.asarray(list(iter_frames(cache)),dtype=float);assert np.isfinite(local).all()
    matrix=np.asarray(target['world_matrix'])
    world=local@matrix[:3,:3].T+matrix[:3,3]
    speed=np.linalg.norm(np.diff(world,axis=0)*plan['fps'],axis=2)
    record=dict(object=target['name'],vertices=header.vertex_count,frames=header.frame_count,
        finite=True,max_speed_m_s=float(speed.max()),
        speed_max_per_frame_m_s=speed.max(axis=1).tolist(),
        speed_p99_per_frame_m_s=np.quantile(speed,.99,axis=1).tolist(),
        centroid_world_per_frame=world.mean(axis=1).tolist(),
        coordinate_scope='Exported world transform; animated transforms and Blender playback validated separately')
    records.append(record)
    fig,axes=plt.subplots(2,3,figsize=(12,7))
    fig.suptitle(target['name'])
    minimum=world.min(axis=(0,1));maximum=world.max(axis=(0,1))
    padding=np.maximum((maximum-minimum)*.03,.001)
    indices=[0,header.frame_count//2,header.frame_count-1]
    for axis,index in zip(axes[0],indices):
        points=world[index];axis.scatter(points[:,0],points[:,2],s=.25)
        axis.set_title(f'Frame {index+plan["frame_start"]}')
        axis.set_xlim(minimum[0]-padding[0],maximum[0]+padding[0])
        axis.set_ylim(minimum[2]-padding[2],maximum[2]+padding[2])
        axis.set_xlabel('X (m)');axis.set_ylabel('Z (m)');axis.set_aspect('equal')
    axes[1,0].plot(np.arange(1,len(speed)+1)/plan['fps'],speed.max(axis=1),label='Max')
    axes[1,0].plot(np.arange(1,len(speed)+1)/plan['fps'],np.quantile(speed,.99,axis=1),label='P99')
    axes[1,0].set_xlabel('Zeit (s)');axes[1,0].set_ylabel('Geschwindigkeit (m/s)');axes[1,0].legend()
    for axis in axes[1,1:]:axis.axis('off')
    fig.tight_layout();fig.savefig(a.output/(target['name']+'.png'),dpi=150);plt.close(fig)
(a.output/'checks.json').write_text(json.dumps(dict(result='PASS',scope='Finite PC2, checksum, complete frame count and exported-world speed histories',objects=records),indent=2))
