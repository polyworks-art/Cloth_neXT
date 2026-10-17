from pathlib import Path
import subprocess,json
r=Path.cwd();b=r/'.hydro-test-tmp/reproducibility-20261017';old=r.parent/'ppf-contact-solver-hydrodynamics';d=b/'patch-reconstruction-check';d.mkdir(exist_ok=True)
patchdir=r/'docs/validation/gaia026/reproducibility';baseline=(patchdir/'native-hydrodynamics-baseline.patch').read_text();research=(patchdir/'native-research.patch').read_text()
import re
files=set(re.findall(r'^--- a/(.+)$',baseline+'\n'+research,re.M))
for rel in files:
 z=subprocess.run(['git','-C',str(old),'show','7232f4033515e1e72359f4ce5145456ce7d156f7:'+rel],capture_output=True,check=True).stdout
 p=d/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z)
subprocess.run(['git','init','-q',str(d)],check=True)
for name in ['native-hydrodynamics-baseline.patch','native-research.patch']:
 z=subprocess.run(['git','-C',str(d),'apply','--whitespace=nowarn',str(patchdir/name)],capture_output=True,text=True)
 print(name,z.returncode,z.stderr[:300]);assert z.returncode==0
for rel in files|{'crates/ppf-cts-compute/cuda/research_order.hpp'}:
 assert (d/rel).read_text()==(b/'native-source'/rel).read_text(),rel
(patchdir/'patch-reconstruction-check.json').write_text(json.dumps(dict(result='PASS',files=len(files)+1,base='7232f4033515e1e72359f4ce5145456ce7d156f7',method='Apply baseline then research patch to original upstream files; compare every changed reconstructed source file to tested source'),indent=2))
print('All reconstructed sources match')
