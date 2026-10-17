from pathlib import Path
import difflib,hashlib,json
root=Path.cwd();base=root/'.hydro-test-tmp/reproducibility-20261017';old=root.parent/'ppf-contact-solver-hydrodynamics';new=base/'native-source';out=root/'docs/validation/gaia026/reproducibility';out.mkdir(parents=True,exist_ok=True)
files=['crates/ppf-cts-solver/src/mesh.rs','crates/ppf-cts-solver/src/driver/step.rs','crates/ppf-cts-compute/seam/kernelgen.py','crates/ppf-cts-compute/cuda/research_order.hpp','crates/ppf-cts-compute/cuda/backend/backend.cu'];patch=[];manifest={}
for rel in files:
 a=old/rel;b=new/rel;before=a.read_text() if a.exists() else '';after=b.read_text();patch.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+rel if a.exists() else '/dev/null',tofile='b/'+rel));manifest[rel]=dict(baseline_sha256=hashlib.sha256(a.read_bytes()).hexdigest() if a.exists() else None,research_sha256=hashlib.sha256(b.read_bytes()).hexdigest())
(out/'native-research.patch').write_text(''.join(patch));(out/'source-hashes.json').write_text(json.dumps(manifest,indent=2))
print('Preserved',len(files),'research source files as reviewable patch')
