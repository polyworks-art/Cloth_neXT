from pathlib import Path
import json,os,subprocess,time
r=Path.cwd();b=r/'.hydro-test-tmp/reproducibility-20261017';py='C:/Users/timch/AppData/Local/Programs/Python/Python311/python.exe'
while True:
 m=json.loads((b/'full-corrected-cached-five/manifest.json').read_text())
 if len(m['runs'])==5:break
 time.sleep(15)
def call(args,log,env=None):
 with (b/log).open('w') as f:
  c=subprocess.run(args,stdout=f,stderr=subprocess.STDOUT,env=env)
 print(log,c.returncode,flush=True)
 assert c.returncode==0,log
call([py,'tools/analyze_gaia_native_reproducibility.py','--suite',str(b/'full-corrected-cached-five'),'--fixture',str(b/'neon-fixture/session'),'--scene-cache',str(r/'.owner-test-tmp/20261017/published-2926-full-host/cache/.cloth_next_export/scene'),'--dependencies',str(r.parent/'ppf-contact-solver-hydrodynamics/.tools/python-deps'),'--output',str(b/'full-corrected-cached-five/comparison.json')],'analyze-final-five.log')
env={k:v for k,v in os.environ.items() if not k.startswith('PPF_REPRO_')};env.update(PPF_REPRO_ORDER_MODE='colored',PPF_REPRO_ORDER_FILTER='all',PPF_REPRO_FILTERED_CONTACT='1',PPF_REPRO_SERIAL_CSR='1',PPF_REPRO_SERIAL_STITCH='1');env['PATH']=str(b/'cached-cuda-build/lib')+os.pathsep+str(r/'.hydro-test-tmp/blender-e2e-20261016/runtime/bin')+os.pathsep+env['PATH']
call([str(b/'native-source/target/research-cuda/release/deps/ppf_contact_solver-7161953f37b63c8a.exe'),'hydrodynamic_cuda','--ignored','--nocapture','--test-threads','1'],'test-cached-cuda-hydrodynamics.log',env)
s=(b/'run-independent-five.py').read_text().replace("b/'clean-cuda-build/lib'","b/'cached-cuda-build/lib'").replace("b/'full-independent-five'","b/'full-independent-cached-five'")
(b/'run-independent-cached-five.py').write_text(s)
call([py,str(b/'run-independent-cached-five.py'),'--mode','ordered'],'full-independent-cached-five.log')
s=(b/'analyze-independent-five.py').read_text().replace('reproducibility-20261017/full-independent-five','reproducibility-20261017/full-independent-cached-five');(b/'analyze-independent-cached-five.py').write_text(s)
call([py,str(b/'analyze-independent-cached-five.py')],'analyze-independent-cached-five.log')
call([py,'tools/validate_hydrodynamic_sdf_bounds.py','--solver',str(b/'native-source/target/research-cuda/release/ppf-contact-solver.exe'),'--reference','docs/validation/gaia026/hydrodynamics/spatial-cuda-complete','--output',str(b/'cached-sdf-bounds')],'cached-sdf-bounds.log',env)
print('FINAL NATIVE CHECKS COMPLETED',flush=True)
