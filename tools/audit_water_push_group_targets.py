"""Host proof of official group targeting, without native dynamics or private state."""
import sys,json,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend._force_field_ import ForceField,resolve_targets
from tools.audit_water_response import sample
from cloth_next.gaia.water_push_prototype import discrete_push

def run():
    gain=float(discrete_push([[1,0,0]],[[0,0,0]],[1],.1,1/48)[0,0])
    field=ForceField();strong=np.zeros((2,2,2,3),dtype='f4');strong[...,0]=gain
    field.grid(strong,[-2]*3,[2]*3,groups=['cloth_A'])
    field.grid(np.zeros_like(strong),[-2]*3,[2]*3,groups=['cloth_B'])
    groups={'cloth_A':list(range(289)),'cloth_B':list(range(289,578))}
    grids,_,mask=resolve_targets(field,groups,578)
    points=np.tile([0,.25,.5],(578,1));delivered=np.zeros_like(points,dtype=float)
    for g,bit in grids:
        reached=(mask & np.uint32(1<<bit))!=0
        delivered[reached]+=sample(g.values[0],np.array(g.box_min),np.array(g.box_max),points[reached])
    assert np.all(mask[:289]==1) and np.all(mask[289:]==2)
    assert np.max(np.abs(delivered[:289,0]-gain))<1e-6
    assert np.max(np.abs(delivered[289:]))==0
    result={'supported_public_api':'ForceField.grid(groups=...), Object.group(label), documented resolve_targets helper','same_world_position':points[0].tolist(),'A_initial_velocity':[0,0,0],'B_initial_velocity':[1,0,0],'A_desired_acceleration':gain,'A_delivered_acceleration':delivered[0].tolist(),'B_desired_acceleration':0.,'B_delivered_acceleration':delivered[289].tolist(),'group_cross_contamination':0.,'target_masks':[int(mask[0]),int(mask[289])],'native_validation':False,'limitation':'Object groups resolve all vertices of each object. No documented public per-layer selector in one existing connected cloth object. Private built-scene context/masks were not changed. This is official host targeting validation, not a native response measurement.'}
    (ROOT/'dist/water-push-spatial/group-targeting.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':run()
