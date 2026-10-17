"""Run source/candidate topology investigation; does not approve transport."""
import argparse,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);a=p.parse_args()
root=Path(__file__).resolve().parents[1];tmp=root/'.hydro-test-tmp';cache=Path('F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache');rows=[]
for frame in range(130,161):
 case=tmp/f'flip-grid-edge-reference-{frame}'
 if not case.exists():
  subprocess.run([sys.executable,str(root/'tools/stage_original_flip_reference.py'),'--cache',str(cache),'--frame',str(frame),'--output',str(case)],check=True,stdout=subprocess.DEVNULL)
 if not (case/'reconstructed.bobj').exists():
  with (case/'native-mesher.log').open('w') as log:subprocess.run([str(tmp/'flip-grid-edge-mesher-r1/build/cnx_flip_mesher.exe'),str(case)],check=True,stdout=log,stderr=subprocess.STDOUT)
 if not (case/'geometry-comparison.json').exists():
  with (case/'comparison.log').open('w') as log:subprocess.run([sys.executable,str(root/'tools/compare_original_flip_reconstruction.py'),'--reference',str(cache/f'bakefiles/{frame:06}.bobj'),'--case',str(case),'--dependencies',a.dependencies],check=True,stdout=log,stderr=subprocess.STDOUT)
 row=json.loads((case/'geometry-comparison.json').read_text());rows.append(row)
 out=root/'docs/validation/gaia026/hydrodynamics/flip-original/grid-edge-130-160.json'
 out.write_text(json.dumps({'transport_approved':False,'purpose':'Geometry/topology investigation only; closed candidate is not sufficient physical acceptance.','frames':rows},indent=2))
 print(frame,row['geometry_gate'],row['replay_topology'],flush=True)
