"""Compare actual wet and dry outputs with identical topology and frame timing."""
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.bake.pc2 import iter_frames
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--wet',type=Path,required=True);p.add_argument('--dry',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);a=p.parse_args()
wet=json.loads(a.wet.read_text());dry=json.loads(a.dry.read_text())
assert wet['result']==dry['result']=='PASS' and wet['frames']==dry['frames']
assert wet.get('fps',24)==dry.get('fps',24)
objects=[]
for before,after in zip(wet['objects'],dry['objects']):
    assert before['object']==after['object'] and before['vertices']==after['vertices']
    x=np.asarray(list(iter_frames(Path(before['pc2']))),dtype=float)
    y=np.asarray(list(iter_frames(Path(after['pc2']))),dtype=float)
    delta=np.linalg.norm(x-y,axis=2)
    objects.append(dict(object=before['object'],max_position_difference=float(delta.max()),
        final_rms_difference=float(np.sqrt(np.mean(delta[-1]**2)))))
assert len(objects)==len(wet['objects'])==len(dry['objects'])
assert any(o['max_position_difference']>1e-5 for o in objects)
a.output.write_text(json.dumps(dict(result='PASS',scope='Observed wet/dry difference in matching PC2 local coordinates; not an isolated repeated-run significance test',objects=objects),indent=2))
