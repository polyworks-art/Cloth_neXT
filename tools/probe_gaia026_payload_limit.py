"""Reach the unchanged 1 GiB native-file cap with real FLIP array data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.native_fluid import native_fluid_payload_file,MAX_FILE_PAYLOAD_BYTES
from cloth_next.ppf.schema import envelope

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gaia',type=Path,required=True)
    p.add_argument('--pc2',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    identities={str(x):digest(x) for x in (a.gaia,a.pc2)}
    with GaiaContainer(a.gaia) as c:
        water=c.manifest['water']
        array_digest=hashlib.sha256(json.dumps(c.manifest['frames'],sort_keys=True,
            separators=(',',':'),allow_nan=False).encode()).hexdigest()
    target=dict(path=str(a.gaia),fingerprint=water['fingerprint'],arrays_digest=array_digest,
        groups=[0],strength=.2,influence=1.,flow=1.)
    descriptor=dict(version=1,field_kind='native-file',first=1,last=250,fps=24.,solver_fps=24.,
                    packing_recipe=1,targets=[target.copy() for _ in range(3)])
    base=envelope.dumps_envelope(envelope.KIND_PARAM,{'group':[],'scene':{},
        'force_field':{'fields':[],'scripts':[]}},schema_version=2)
    output=a.output/'oversized.cbor';started=time.monotonic();frames=[]
    try:
        native_fluid_payload_file(base,descriptor,output,progress=lambda f:frames.append(f))
    except ValueError as exc:
        assert '1 GiB' in str(exc),str(exc)
        assert not output.exists() and not list(a.output.glob('native-fluid-spool-*'))
        assert identities=={str(x):digest(x) for x in (a.gaia,a.pc2)}
        report=dict(result='PASS',expected_error=str(exc),unchanged_budget_bytes=MAX_FILE_PAYLOAD_BYTES,
            complete_field_samples_processed=len(frames),seconds=time.monotonic()-started,
            source_gaia_unchanged=True,previous_pc2_unchanged=True,partials_and_spools_removed=True,
            scope='Real native-file encoder budget gate; intentionally oversized three-field schedule, not a completed solver bake')
        (a.output/'report.json').write_text(json.dumps(report,indent=2));print(report)
    else:raise AssertionError('Oversized real fluid data unexpectedly accepted')

if __name__=='__main__':main()
