"""Recover completed safety measurements, including failed diagnostic runs."""
import sys,json,csv,hashlib,zipfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import _cbor_bridge_ as bridge
from cloth_next.gaia.water_push_prototype import limited_push,output_velocity
OUT=ROOT/'dist/water-push-prototype'
def main():
    summaries=[];traces=[]
    for name,air in [('native-statistics.json',.001),('push-only-native-statistics.json',0.)]:
        record=json.loads((OUT/name).read_text());root=Path(record['root'])
        fixed=bridge.load_pickle_payload(str(root/'gate/fixed_session.pickle'),bridge.KIND_FIXED_SESSION)
        (OUT/f'actual-params-air{air}.toml').write_bytes((root/'gate/param.toml').read_bytes())
        frames=fixed.get.statistics_frames();rows=[]
        previous=None;old=None
        for frame in frames:
            p,identity=fixed.get.vertex(frame);assert identity==frame
            v=np.zeros_like(p) if previous is None else output_velocity(p,previous,frame,old,24.)
            stats=fixed.get.statistics(frame)
            for k,response in enumerate([1.,.1,.05]):
                desired,applied=limited_push(np.tile([1.,0.,0.],(9,1)),v[k*9:(k+1)*9],np.ones(9),response,1/24+.01)
                actual=stats['objects'][f'patch{k}']['values']
                rows.append({'frame':frame,'time':stats['time'],'response_time':response,'position':p[k*9+4].tolist(),'cloth_velocity_estimate':v[k*9+4].tolist(),'actual_object_velocity':[actual['velocity_'+axis] or 0. for axis in 'xyz'],'water_velocity':[1.,0.,0.],'relative_velocity':([1.,0.,0.]-v[k*9+4]).tolist(),'desired_acceleration':desired[4].tolist(),'applied_grid_acceleration':applied.mean(axis=0).tolist() if frame!=frames[-1] else None,'occupancy':1.,'control_trace_provenance':'retrospectively reconstructed from actual output and unchanged gate law; final row has no next window'})
            previous=p;old=frame
        (OUT/f'debug-air{air}.json').write_text(json.dumps(rows,indent=2))
        with (OUT/f'debug-air{air}.csv').open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['frame','time_s','response_time_s','position_x','position_y','position_z','estimated_vx','estimated_vy','estimated_vz','actual_object_vx','actual_object_vy','actual_object_vz','water_vx','water_vy','water_vz','relative_vx','relative_vy','relative_vz','desired_ax','desired_ay','desired_az','applied_ax','applied_ay','applied_az','occupancy','actual_speed_ratio','overshoot_ratio'])
            for row in rows:
                actual=row['actual_object_velocity'][0]
                writer.writerow([row['frame'],row['time'],row['response_time'],*row['position'],*row['cloth_velocity_estimate'],*row['actual_object_velocity'],*row['water_velocity'],*row['relative_velocity'],*row['desired_acceleration'],*(row['applied_grid_acceleration'] or [None]*3),1.,actual,max(0.,actual-1.)])
        for response in [1.,.1,.05]:
            selected=[row for row in rows if row['response_time']==response]
            peak=max(selected,key=lambda row:row['actual_object_velocity'][0])
            summaries.append({'air_density':air,'response_time':response,'dt':.01,'frames_recorded':len(frames),'max_projected_speed_ratio':peak['actual_object_velocity'][0],'peak_frame':peak['frame'],'peak_time':peak['time'],'passed_1_percent_tolerance':peak['actual_object_velocity'][0]<=1.01})
        traces.append((air,rows))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4),constrained_layout=True)
    for ax,(air,rows) in zip(axes,traces):
        for response in [1.,.1,.05]:
            r=[row for row in rows if row['response_time']==response]
            ax.plot([row['time'] for row in r],[row['actual_object_velocity'][0] for row in r],label=f'Response {response:g} s')
        ax.axhline(1,color='black',linestyle='--',label='Physical source water speed')
        ax.axhline(1.01,color='red',linestyle=':',label='Declared 1% tolerance')
        ax.set(title='Air velocity + Water Push' if air else 'Water Push only (air field retained)',xlabel='Seconds',ylabel='Actual solver object velocity X (m/s)');ax.legend(fontsize=8);ax.grid(alpha=.2)
    fig.savefig(OUT/'speed-cap-failure.png',dpi=150);plt.close(fig)
    summary={'result':'STOP_SPEED_CAP_FAILURE','tolerance_declared_before_run':.01,'cases':summaries,'regressions':'57 passed','production_integration':False,'release':False}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    report=['# Water Push controlled prototype: stopped at safety gate','','No production integration, new UI, default, or release. The mandatory speed-cap stop condition was reached before calibration and constrained-cloth testing. Official source velocity is unchanged.','',
      '## Control law','',
      '`relative = sampled_water_velocity − estimated_cloth_velocity`',
      '`desired = relative × occupancy / response_time`',
      '`applied = relative × min(occupancy / response_time, 1 / window_upper_bound)`','',
      'The prospective window bound is 1/24 s + the native maximum timestep (0.01 s), allowing the native solver to overshoot an output hold by one step. This limits the calculated velocity increment against the estimated velocity. It does not limit the increment against an unavailable instantaneous per-vertex velocity.','',
      '## Velocity source and measurements','',
      'Public output positions and object-statistics velocities both use output-interval secants. The statistics are NOT an independent instantaneous oracle, even for uniform translation. Historical actual_object_velocity labels denote output-averaged statistics. See the subsequent cadence audit for the corrected independently validated reference.','',
      '## Spatial delivery and isolation','',
      'The exact previous nine-vertex 0.2 × 0.2 m patch geometry and density 1 / Baraff–Witkin / stretch 1000 / sideways response 0.4 / bend 0.83 / strain limit 4% are used. Three separate groups retain their original +X 1 m/s air-velocity grids and receive separate uniform acceleration grids. Uniformity is checked (velocity spread ≤0.001 m/s); heterogeneous velocity samples are rejected. This is an exact spatial representation for the uniform safety fixture only, not a claimed general per-vertex force implementation. No general reconstruction is promoted to production. Group targeting prevents one patch’s state driving another.','',
      '## Declared tolerance and results','',
      'Maximum projected speed ratio ≤1.01 (1% numerical overshoot), declared in the gate source before either run. Native dt=0.01, output fps=24, water speed=1 m/s, response times 1.0 / 0.1 / 0.05 s.','',
      '| Air density | Response s | Maximum speed ratio | Peak frame | Peak time s | Pass |',
      '|---:|---:|---:|---:|---:|---|']
    for case in summaries:report.append(f"| {case['air_density']} | {case['response_time']} | {case['max_projected_speed_ratio']:.6f} | {case['peak_frame']} | {case['peak_time']:.6f} | {'yes' if case['passed_1_percent_tolerance'] else 'NO'} |")
    report += ['', 'The Water-Push-only trial keeps the original air-velocity field uploaded but disables its aerodynamic force solely in this isolated diagnostic (air-density=0). No production/global air-density workaround is used. Its overshoot reproduces the failure without the existing aerodynamic coupling.','',
      '## Failure mechanism and blocker','',
      'The secant velocity is the mean over the previous output interval. During rapid acceleration it lags current velocity. Both output-secant measurements show approximately 30% overshoot at response 0.05 s; instantaneous peaks are not measured by this historical report. The algebraic clamp passes unit tests but is not a robust physical speed limiter.','',
      'The official acceleration API evaluates `a(x,t)` and its script interface accepts x/y/z/time, not a vertex velocity. Object statistics cannot replace per-vertex state on deformed cloth. A lag-compensated estimator or a supported velocity-aware control interface would need a separate safety proof, particularly with pins, deformation, collisions and changing flow. This audit does not claim every possible controller is impossible; it establishes that this boundary-secant/clamped-acceleration prototype fails the mandatory requirement. No smaller gains or arbitrary constants are used to conceal that failure.','',
      '## Technical failures','',
      'The first combined diagnostic completed its native simulation, then its summary writer rejected the null initial statistic. Data were recovered from public native outputs; the writer now treats the known stationary first state correctly. The isolated trial hit a Windows sharing violation while replacing the public hold marker after 16 recorded output frames; the owned native process was checkpointed and shut down. Overshoot had already occurred before that transport failure. The gate now retries this public operation on a bounded schedule. No solver code was patched.','',
      'Debug CSV/JSON include positions, estimated and actual velocities, local water, relative velocity, desired/applied acceleration and occupancy. Control-law entries are explicitly labeled retrospective reconstructions from the actual outputs, rather than a claimed retained live trace; terminal samples do not claim a subsequent applied field.','',
      '## Validation status','',
      '57 targeted tests passed, covering the prototype algebra, skipped boundary velocity estimates, dry support, matched-velocity zero push, existing held stream, Water Flow streaming/window and Blender descriptors. This is not a full product regression run. The old bake path and normal UI remain unchanged; the prototype module has no production imports.','',
      'Not run after the speed-cap stop: full 0.5/1/2/4 m/s × response-time calibration; dt=0.001/0.002 comparison; constrained patch; changing direction; enter/exit native tests; unrelated-Wind numerical comparison; general heterogeneous spatial reconstruction. Dry/matched behavior is algebraically tested only. Response-time calibration, timestep independence, constrained response, and transition stability are therefore NOT established.','',
      '## External solver integrity','',
      'All 14,606 packaged files were compared byte-for-byte with the original official 2026-09-27-20-44 Windows ZIP: zero differences. Archive SHA-256: `fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`. Ordinary runtime sessions are additional audit data, not patched package files. See `solver-integrity.json`.','',
      'No production default or release is selected. The correct next step is resolving velocity estimation and speed limiting before any artist-facing Water Push feature.']
    path=ROOT/'docs/GAIA_WATER_PUSH_PROTOTYPE_AUDIT.md';path.write_text('\n'.join(report)+'\n',encoding='utf-8')
    with zipfile.ZipFile(ROOT/'dist/GAIA_Water_Push_Prototype_Audit.gaia','w',compression=zipfile.ZIP_DEFLATED) as archive:
        members=[]
        for p in OUT.iterdir():
            if p.suffix not in {'.json','.csv','.png','.toml'}:continue
            raw=p.read_bytes();archive.writestr(p.name,raw);members.append({'name':p.name,'sha256':hashlib.sha256(raw).hexdigest()})
        archive.write(path,'report.md');archive.writestr('audit-manifest.json',json.dumps({'kind':'ClothNeXt.GAIA.WaterPushPrototypeAudit','schema':1,'members':members},indent=2))
    print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
