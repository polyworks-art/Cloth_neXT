"""Read-only post-test integrity check against pre-reconstruction source hashes."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];cache=Path('F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache/bakefiles');evidence=root/'docs/validation/gaia026/hydrodynamics/flip-original';rows=[]
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
for r in json.loads((evidence/'grid-edge-130-160.json').read_text())['frames']:
 frame=r['frame'];provenance=json.loads((root/f'.hydro-test-tmp/flip-grid-edge-reference-{frame}/input-provenance.json').read_text());expected={f'{frame:06}.bobj':r['reference_sha256'],**provenance['source_particle_sha256']}
 for name,digest in expected.items():
  actual=sha(cache/name);assert actual==digest,(frame,name);rows.append(dict(frame=frame,file=name,sha256=actual,unchanged=True))
original=Path('F:/16_Cloth_neXt/SW04/SW04_NEON.blend');copy=root/'.hydro-test-tmp/blender-e2e-20261016/SW04_NEON-copy.blend'
historical=json.loads((root/'docs/validation/gaia026/hydrodynamics/blender-e2e/summary.json').read_text())['source_integrity']['original_blend_sha256']
original_sha=sha(original);copy_sha=sha(copy);assert copy_sha==historical
from datetime import datetime,timezone
result=dict(source_cache_payload_integrity='PASS',baseline_blend_snapshot_integrity='PASS',checked_source_payloads=len(rows),current_original_blend_sha256=original_sha,baseline_blend_sha256=copy_sha,historical_reference_sha256=historical,current_original_matches_baseline=original_sha==copy_sha,current_original_last_write_utc=datetime.fromtimestamp(original.stat().st_mtime,timezone.utc).isoformat(),scope='The current original blend differs from the historical reference; its last-write timestamp predates current tests. Tests intentionally use the preserved initial reference copy. No inference about who saved the original. Original FLIP payloads are unchanged.',files=rows)
(evidence/'post-test-source-integrity.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='files'},indent=2))
