# SPDX-License-Identifier: GPL-3.0-or-later
"""Existing Blender cancel/restart/resume/Start Fresh gates with prepared water.

Inject only environmental input into the existing test plan; recovery operators,
worker, checkpoints, partial PC2 validation and process ownership stay unchanged.
"""
from dataclasses import replace
import json
import hashlib
import ctypes
from pathlib import Path
import sys
import bpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools import blender_recovery_integration as gate
from cloth_next.gaia.container import GaiaContainer
from cloth_next.ppf.schema import envelope

cache=ROOT/'dist/gaia-streaming/control-4-5.gaia'
with GaiaContainer(cache) as container:
    water=container.manifest['water']
    first=container.manifest['frames'][0]['frame']
    last=container.manifest['frames'][-1]['frame']

original_load=gate._load_addon
def load(args):
    module=original_load(args)
    original_build=module._build_run_plan_impl
    def build(context,**options):
        plan=original_build(context,**options)
        tree=envelope.loads_envelope(plan.scene.param_payload,envelope.KIND_PARAM,schema_version=2)
        tree['group'][0][0]['force-field-weight']=1.
        tree['scene']['air-density']=.1
        payload=envelope.dumps_envelope(envelope.KIND_PARAM,tree,schema_version=2)
        descriptor={'version':1,'first':first,'last':last,'fps':water['fps'],
            'targets':[{'path':str(cache),'fingerprint':water['fingerprint'],
                        'groups':[0],'dimensions':water['dimensions'],
                        'influence':1.,'velocity_scale':1.}]}
        scene=replace(plan.scene,param_payload=payload,param_hash=envelope.payload_sha256(payload),
                      official_bridge_json='',water_stream_json=json.dumps(descriptor,sort_keys=True))
        return replace(plan,scene=scene)
    module._build_run_plan_impl=build
    return module
gate._load_addon=load
original_make=gate._make_scene
def make(args):
    cloth=original_make(args)
    cloth.cloth_next.bake_start=first;cloth.cloth_next.bake_end=last
    bpy.context.scene.render.fps=int(water['fps'])
    return cloth
gate._make_scene=make

def source_sha256():
    with cache.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def peak_memory():
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD),
            *[(name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize',
            'QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]]
    counters=Counters();counters.cb=ctypes.sizeof(counters)
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
    psapi=ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize

def instrument(original):
    def phase(args,module):
        before=source_sha256()
        original(args,module)
        after=source_sha256()
        assert before==after,'Prepared environmental cache changed during Bake/Recovery'
        report=json.loads(args.report.read_text(encoding='utf-8'))
        report.update(prepared_cache_sha256=after,prepared_cache_unchanged=True,
                      peak_blender_rss_bytes=peak_memory())
        records=list(args.cache.glob('.cloth_next_recovery/*/metadata.json'))
        projects=[json.loads(path.read_text(encoding='utf-8')).get('project',{}) for path in records]
        report['water_recovery_records']=[value.get('water_stream_state',{}) for value in projects
                                          if value.get('project_id')==report['project']]
        args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return phase

for name in ('cancel_phase','resume_phase','fresh_phase'):
    setattr(gate,name,instrument(getattr(gate,name)))

if __name__=='__main__':gate.main()
