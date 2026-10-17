"""Freeze source reconstruction for isolated Blender validation, never production."""
from pathlib import Path
import hashlib,json,shutil
root=Path(__file__).resolve().parents[1];evidence=root/'docs/validation/gaia026/hydrodynamics/flip-original'
sequence=json.loads((evidence/'grid-edge-130-160.json').read_text());assert len(sequence['frames'])==31
assert all(r['replay_topology']['watertight'] and r['replay_topology']['winding'] and r['reference_triangles']==r['replay_triangles'] and r['reference_topology']['components']==r['replay_topology']['components'] for r in sequence['frames'])
vertex_differences={r['frame']:r['replay_vertices']-r['reference_vertices'] for r in sequence['frames'] if r['replay_vertices']!=r['reference_vertices']}
assert vertex_differences=={149:-1,151:-1}
for frame in vertex_differences:
 replay=json.loads((root/f'.hydro-test-tmp/flip-original-reference-{frame}/geometry-comparison.json').read_text())
 assert replay['geometry_gate']=='PASS'
extra_controls=json.loads((evidence/'independent-occupancy-control-149-151.json').read_text())
assert len(extra_controls)==2 and all(r['near_cloth_occupancy_disagreements']==0 and all(w['screen']=='PASS' for w in r['wet_area']) for r in extra_controls)
assert json.loads((evidence/'reference-transport-comparison.json').read_text())['all_payloads_bit_identical']
wet=json.loads((evidence/'frozen-wet-area-sensitivity.json').read_text());assert len(wet['rows'])==9 and all(r['measurements']['fine7p826mm']['occupancy_screen']=='PASS' for r in wet['rows'])
sensitivity=json.loads((evidence/'native-sensitivity.json').read_text());assert all(r[k]=='PASS' for r in sensitivity['rows'] if r['case']!='coarse_15p65mm' for k in ('trajectory_screen','normal_load_screen','wet_area_screen','temporal_screen'))
controls=json.loads((evidence/'independent-occupancy-control.json').read_text());assert len(controls)==2 and all(r['near_cloth_occupancy_disagreements']==0 and all(w['screen']=='PASS' for w in r['wet_area']) for r in controls)
folder=root/'.hydro-test-tmp/flip-grid-edge-surfaces';folder.mkdir(exist_ok=True)
files={}
for r in sequence['frames']:
 frame=r['frame'];source=root/f'.hydro-test-tmp/flip-grid-edge-reference-{frame}/reconstructed.bobj';target=folder/f'{frame:06}.bobj';digest=hashlib.sha256(source.read_bytes()).hexdigest()
 if target.exists():assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
 else:shutil.copy2(source,target)
 files[target.name]=digest
manifest={'usage':'ISOLATED_BLENDER_VALIDATION_ONLY','release_approved':False,'surface_directory':str(folder),'files':files,'evidence':str(evidence),'method':'Original Individual FLIP 1.8.8 particle mesher; canonical grid-edge join; original 3 chunks, grid, radius, obstacle, smoothing; no integration step or hole fill.','vertex_join_differences':vertex_differences,'known_limits':['4 original zero-area triangles retained in157','Particle-control occupancy sampled near frozen131 cloth in130/135, not a full space proof','Long temporal and visual validation still pending','Existing native CPU/CUDA self-contact parity blocker remains open']}
(folder/'validation-manifest.json').write_text(json.dumps(manifest,indent=2))
