"""Isolated, frame-wise resolution benchmark from the verified real FLIP sample."""
import argparse, ctypes, io, json, os, subprocess, sys, time, zipfile
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import write_water_container
from cloth_next.gaia.water_field import grid_plan, grid_diagnostics, reconstruct, solver_grid

OUT=ROOT/'dist/gaia-resolution'
SOURCE=ROOT/'dist/water-coordinate-audit.gaia'


def peak_rss():
    if os.name != 'nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
            ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
    psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    info=Counters();info.cb=ctypes.sizeof(info)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(info),info.cb):raise ctypes.WinError()
    return int(info.PeakWorkingSetSize)


def sample(field,p,contribution=True):
    p=np.asarray(p,float);lo=np.asarray(field.minimum);hi=np.asarray(field.maximum)
    if np.any(p<lo) or np.any(p>hi):return [0.,0.,0.]
    dims=np.asarray(field.velocity.shape[:3][::-1]);q=(p-lo)/(hi-lo)*(dims-1)
    base=np.minimum(q.astype(int),dims-2);frac=q-base;value=np.zeros(3)
    for z in (0,1):
        for y in (0,1):
            for x in (0,1):
                offset=np.asarray((x,y,z));index=base+offset
                weight=np.prod(np.where(offset,frac,1-frac));xx,yy,zz=index
                v=field.velocity[zz,yy,xx]
                if contribution:v=v*field.influence[zz,yy,xx]
                value+=weight*v
    return value.tolist()


def worker(resolution):
    with zipfile.ZipFile(SOURCE) as archive:
        data=np.load(io.BytesIO(archive.read('audit/source-frame150.npz')))
        positions,velocities=data['positions'],data['velocities']
    lo,hi=[-4.,-4.,0.],[4.,4.,4.]
    if resolution in (6,24):
        dims=(resolution,resolution,int(np.ceil(.5*(resolution-1)))+1)
        layout=grid_diagnostics(lo,hi,dims,particle_count=len(positions),requested=resolution)
    else:
        layout=grid_plan(lo,hi,str(resolution));dims=layout['dimensions']
        layout.update(grid_diagnostics(lo,hi,dims,particle_count=len(positions),requested=resolution))
    started=time.perf_counter();field=reconstruct(positions,velocities,lo,hi,dims,
        support_threshold=layout.get('support_threshold',1.))
    conversion=time.perf_counter()-started
    probes={'fast_interior':[3.3,-3.1,.2],'fast_low':[3.3,-3.1,.3],'fast_mid':[3.3,-3.1,.55],
            'fast_high':[3.3,-3.1,.8],'interior':[0.,0.,.25],
            'above_water':[3.3,-3.1,1.3],'dry_in_domain':[0.,0.,2.], 'outside':[5.,0.,.5]}
    values={name:{'position':p,'velocity':sample(field,p,False),'contribution':sample(field,p)} for name,p in probes.items()}
    active=field.influence>.01
    occupied=int(np.count_nonzero(active));smooth=[]
    for axis in range(3):
        a=[slice(None)]*3;b=a.copy();a[axis]=slice(1,None);b[axis]=slice(None,-1)
        valid=active[tuple(a)]&active[tuple(b)]
        delta=np.linalg.norm(field.velocity[tuple(a)]-field.velocity[tuple(b)],axis=-1)
        smooth.append(float(delta[valid].mean()) if valid.any() else 0.)
    path=OUT/f'field-{resolution}.gaia';started=time.perf_counter()
    write_water_container(path,{'grid_layout':layout,'source_frame':150,'resolution':resolution},[(150,0.,field)])
    cache_seconds=time.perf_counter()-started
    started=time.perf_counter();lo,hi,encoded=solver_grid(field);encode_seconds=time.perf_counter()-started
    report={'resolution':resolution,**layout,'source_particles':len(positions),'occupied_cells':occupied,
        'conversion_seconds':conversion,'cache_write_seconds':cache_seconds,'cache_bytes':path.stat().st_size,
        'axis_encode_seconds':encode_seconds,'peak_working_set_bytes':peak_rss(),'probes':values,
        'neighbor_velocity_difference':smooth,'supported_bake_frames_32mib':(32*1024**2)//layout['encoded_bytes']}
    with zipfile.ZipFile(path,'a',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('audit/resolution.json',json.dumps(report,indent=2))
    (OUT/f'report-{resolution}.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:report[k] for k in ('resolution','dimensions','conversion_seconds','cache_bytes','peak_working_set_bytes')}),flush=True)


def framewise(count):
    with zipfile.ZipFile(SOURCE) as archive:
        data=np.load(io.BytesIO(archive.read('audit/source-frame150.npz')))
        positions,velocities=data['positions'],data['velocities']
    layout=grid_plan([-4.,-4.,0.],[4.,4.,4.])
    def sequence():
        for index in range(count):
            field=reconstruct(positions,velocities,[-4.,-4.,0.],[4.,4.,4.],layout['dimensions'])
            yield 150+index,index/60.,field
            del field
    path=OUT/f'framewise-100-{count}.gaia';start=time.perf_counter()
    write_water_container(path,{'grid_layout':layout,'source':'repeated frame-150 memory benchmark'},sequence())
    report={'frames':count,'seconds':time.perf_counter()-start,'peak_working_set_bytes':peak_rss(),'cache_bytes':path.stat().st_size}
    with zipfile.ZipFile(path,'a') as archive:archive.writestr('audit/framewise.json',json.dumps(report))
    (OUT/'framewise.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--resolution',type=int);parser.add_argument('--framewise',type=int)
    args=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    if args.framewise:framewise(args.framewise)
    elif args.resolution:worker(args.resolution)
    else:
        for resolution in (6,24,100,150,200):
            subprocess.run([sys.executable,__file__,'--resolution',str(resolution)],check=True)
