"""Re-measure recorded native outputs and produce a reproducible diagnostic report."""
import sys,json,csv,zipfile,hashlib
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import _cbor_bridge_ as bridge
from tools.audit_water_response_controlled import measure
OUT=ROOT/'dist/water-response'
def read_csv(name):return np.genfromtxt(OUT/name,delimiter=',',names=True)
def main():
    old=json.loads((OUT/'controlled-summary.json').read_text());results=[]
    roots=list(dict.fromkeys(case['native_root'] for case in old))
    for root in roots:
        cases=[case for case in old if case['native_root']==root]
        native_params=Path(root)/'response/param.toml'
        (OUT/('native-params-'+Path(root).name+'.toml')).write_bytes(native_params.read_bytes())
        session=bridge.load_pickle_payload(str(Path(root)/'response/fixed_session.pickle'),bridge.KIND_FIXED_SESSION)
        poses=[]
        for frame in range(73):
            item=session.get.vertex(frame)
            if item is None:break
            poses.append(item[0])
        poses=np.array(poses)[:,:,[0,2,1]];poses[:,:,1]*=-1
        for index,case in enumerate(cases):
            mask=(poses[0,:,1]>=index*2-1e-4)&(poses[0,:,1]<=index*2+.2001)
            assert mask.sum()==9
            config={'speed':case['speed'],'weight':case['weight'],'density':case['surface_density'],'normal':case['normal']}
            result=measure(poses[:,mask],config,case['air_density'],case['dt'],case['seconds'],root)
            data=read_csv(result['case']+'.csv');q=data['velocity_ratio']/case['weight'];valid=(q>.05)&(q<.8)&(data['time_s']>0)
            result['fitted_tau_s']=float(np.median(-data['time_s'][valid]/np.log(1-q[valid]))) if valid.any() else None
            result['mean_acceleration_alignment_to_source']=float(np.mean(data['acceleration_alignment'][1:]))
            results.append(result)
    (OUT/'controlled-summary-final.json').write_text(json.dumps(results,indent=2))
    real=json.loads((OUT/'real-summary.json').read_text());particles=json.loads((OUT/'particle-diagnostics.json').read_text())
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(12,8),constrained_layout=True)
    r=read_csv('real-wet_free.csv')
    axes[0,0].plot(r['time_s'],r['water_speed'],label='Water at current cloth position')
    axes[0,0].plot(r['time_s'],r['cloth_speed'],label='Baked cloth speed')
    axes[0,0].plot(r['time_s'],r['relative_speed'],label='Relative speed',linestyle=':')
    axes[0,0].set(title='Real pinned scene: interior vertex',ylabel='m/s');axes[0,0].legend(fontsize=8)
    axes[0,1].plot(r['time_s'],r['cloth_x'],label='Cloth velocity X')
    axes[0,1].set(title='Real cloth oscillates instead of following flow',ylabel='m/s');axes[0,1].axhline(0,color='gray',lw=.5)
    for case in results:
        if case['weight']!=1 or case['speed']!=1:continue
        d=read_csv(case['case']+'.csv')
        label=f"mass {case['surface_density']:g}, normal {case['normal']}, air {case['air_density']:g}, dt {case['dt']:g}"
        axes[1,0].plot(d['time_s'],d['velocity_ratio'],label=label)
        axes[1,1].plot(d['time_s'],d['relative_speed'],label=label)
    axes[1,0].set(title='Free patch: local velocity ratio',ylabel='cloth / water');axes[1,0].legend(fontsize=7)
    axes[1,1].set(title='Free patch: relative-velocity decay',ylabel='m/s')
    for ax in axes.flat:ax.set_xlabel('Seconds');ax.grid(alpha=.2)
    fig.savefig(OUT/'response.png',dpi=150);plt.close(fig)
    lines=['# GAIA Water Flow response diagnosis','',
      'Read-only analysis of `F:\\GAIA_Watertest.blend`, its current baked PC2, and `F:\\16_Cloth_neXt\\cache_AD\\Plane_WaterFlow.gaia`. Official GAIA 0.23 CUDA remains unchanged. No production parameters or force model were changed.','',
      '## Decision: C — force-field-weight is not enough','',
      'The existing path correctly accelerates a free patch toward its local flow. The production pinned sheet scarcely follows the water. Increasing the official field weight changes the target air velocity, so it overshoots the physical source velocity rather than independently strengthening coupling. A separate Water Push / acceleration component is a candidate for a later task, with response-time and speed-cap validation; it is not implemented here. Do not tune a production default from this audit.','',
      '## Production measurement','',
      'Water is sampled by trilinear interpolation of the prepared occupancy-weighted grid at each actual evaluated world-space cloth position, every source frame 1–250. Cloth velocity and acceleration use centered output-frame differences (24 fps); endpoints use one-sided differences. No initial-position-only sampling. The below means use valid occupied frames with water speed >0.05 m/s.','',
      '| Point | Vertex | Wet frames | Mean water m/s | Mean cloth m/s | Mean ratio | Mean alignment | Minimum relative m/s |',
      '|---|---:|---:|---:|---:|---:|---:|---:|']
    for label,val in real.items():
        def fmt(key):return '—' if val[key] is None else f'{val[key]:.5g}'
        lines.append(f"| {label} | {val['vertex']} | {val['wet_frames']} | {fmt('mean_water_speed_wet')} | {fmt('mean_cloth_speed_wet')} | {fmt('mean_velocity_ratio_wet')} | {fmt('mean_flow_alignment_wet')} | {fmt('minimum_relative_speed_wet')} |")
    lines += ['', 'The interior and edge velocities oscillate about zero; their mean alignment therefore approaches zero. They are part of a sheet coupled to 52 soft-pinned vertices and stiff neighboring faces. Negative instantaneous acceleration alignment is not proof of reversed Water Flow: internal forces and gravity also act. The pinned control is above the liquid, so it is not a wet-pin response test.','',
      '## Representative per-frame table','',
      'Full XYZ positions, water/cloth/relative velocity vectors, acceleration vectors, both alignments, occupancy and world normals are in `real-wet_free.csv`, `real-wet_edge.csv`, `real-pinned.csv`. Compact interior sample:','',
      '| Frame | Time s | Position XYZ m | Water XYZ m/s | Cloth XYZ m/s | Relative m/s | Acceleration XYZ m/s² | Alignment | Ratio | Occupancy |',
      '|---:|---:|---|---|---|---:|---|---:|---:|---:|']
    for frame in [50,100,150,200,250]:
        row=real['wet_free']['frames'][str(frame)]
        vec=lambda prefix:', '.join(f'{row[prefix+"_"+axis]:.4f}' for axis in 'xyz')
        lines.append(f"| {frame} | {row['time_s']:.3f} | {vec('position')} | {vec('water')} | {vec('cloth')} | {row['relative_speed']:.4f} | {vec('acceleration')} | {row['flow_alignment']:.4f} | {row['velocity_ratio']:.5f} | {row['occupancy']:.3f} |")
    near=[row for row in particles['nearest'] if row['vertex']==real['wet_free']['vertex']]
    errors=[np.linalg.norm(np.array(row['particle_velocity'])-row['field_at_particle']) for row in near]
    lines += ['', '## FLIP reconstruction cross-check','',
      f"Frames 150–155, interior vertex: nearest particle distance {min(row['nearest_distance'] for row in near):.4f}–{max(row['nearest_distance'] for row in near):.4f} m; mean vector error between raw particle velocity and field at its position {np.mean(errors):.4f} m/s. Raw source velocity is never rescaled. See `particle-diagnostics.json` for the local neighborhood and all selected vertices.",'',
      'The supplied `flip_id` values repeat within a single frame; unique UID files are absent. Consequently no single-particle trajectory is presented as uniquely tracked. Nearest same-frame samples and neighborhoods are the secondary diagnostic. The hidden FLIP cache has a stale identity matrix in background Blender; the diagnostic resolves its authored parent/inverse/basis matrix using FLIP’s existing transform. It does not alter FLIP.','',
      '## Controlled response','',
      'Nine-vertex 0.2 × 0.2 m patches, no pins, gravity, contact or colliders; all source fields are uniform physical +X velocities, occupancy 1, with broad bounds that contain the entire trajectory. Independent groups run together; no contacts couple patches. Measurements use each patch’s center vertex. Identical structural material parameters across cases.','',
      '| Water m/s | Weight | Surface density | Normal | Air density | dt s | Final cloth m/s | Ratio | t25 s | t50 s | t75 s | t90 s | Fitted tau s | Min relative m/s | Peak acceleration m/s² | Alignment |',
      '|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for case in results:
        f=lambda val:'—' if val is None else f'{val:.4g}'
        t=case['threshold_times_s']
        lines.append('| '+' | '.join(str(case[key]) for key in ['speed','weight','surface_density','normal','air_density','dt'])+' | '+' | '.join([f(case['final_cloth_speed']),f(case['final_velocity_ratio']),*[f(t[k]) for k in ['25','50','75','90']],f(case['fitted_tau_s']),f(case['minimum_relative_speed']),f(case['peak_acceleration']),f(case['mean_flow_alignment'])])+' |')
    lines += ['', 'The JSON records 10/25/50/75/90% and 63% thresholds in seconds and frames. Thresholds for weights >1 refer to the original physical water speed; reaching them once does not mean following that speed thereafter. Response CSVs also record the effective weighted target. Three-second observation; a dash means not reached, not impossible.','',
      '## Parameters and semantics','',
      'Production material metadata: surface density 1, Baraff–Witkin, stretch 1000, sideways response 0.4, bend 0.83, stretch limit 4%, shape/fold damping 0, soft pin strength 100, 52 pins, 24 fps, gravity −Z 9.81. Water influence and velocity scale are both 1.','',
      'Air-density and air-friction are absent from the persisted production Param payload: official defaults are 0.001 and 0.2; isotropic-air-friction defaults to 0. These are distinct from the unused object Force UI defaults (0.01 and 0.2). Weight for Water Flow is explicitly 1. The saved blend currently has Water Flow off, but completed recovery telemetry proves all 249 windows were uploaded.','',
      'Provenance caveat: PC2 metadata reports dt 0.001 and CG tolerance 0.001, whereas the current blend and most recent cached base Param report dt 0.01 and CG tolerance 0.01. The finished native project was removed by normal cleanup. The actual native dt cannot be re-read directly; do not treat the current settings as an exact record of the completed bake. The controlled fixtures record their actual native `param.toml` and both drag parameters explicitly.','',
      'Official `external_field.kernel.cpp` multiplies the air-velocity sample by field weight. `momentum.kernel.cpp` adds that result to wind without gust modulation. `air_damper.hpp` then applies normal response plus air-friction × tangential response, scaled by vertex area × air-density. Thus at friction 0.2 a parallel surface has about one fifth of the directional response coefficient. Both drag and inertial energies divide by dt²: this is a discrete velocity relaxation, not a calibrated water-density drag law. Its response time depends on integration timestep; this must be accounted for in later coupling design.','',
      'The real wet face has |normal·flow direction| close to 1: orientation attenuation is not its main issue. Controlled mass and tangential cases verify the expected sensitivity. Uniform source speeds 0.5/1/2/4 m/s produce nearly identical normalized response and acceleration proportional to speed. High field weights retain the same relaxation coefficient while changing the asymptotic target; they are unsuitable as a physical coupling-strength control.','',
      '## Artifacts','',
      'All measurements, plots and this report are bundled into `GAIA_Water_Response_Audit.gaia`. The original blend, PC2, water container and external solver remain unchanged.']
    (ROOT/'docs/GAIA_WATER_FLOW_RESPONSE_AUDIT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    artifact=ROOT/'dist/GAIA_Water_Response_Audit.gaia'
    with zipfile.ZipFile(artifact,'w',compression=zipfile.ZIP_DEFLATED) as z:
        files=[p for p in OUT.iterdir() if p.suffix in {'.csv','.json','.png','.npz','.toml'}]
        manifest={'kind':'ClothNeXt.GAIA.WaterResponseAudit','schema':1,'files':[]}
        for p in files:
            raw=p.read_bytes();z.writestr(p.name,raw);manifest['files'].append({'name':p.name,'sha256':hashlib.sha256(raw).hexdigest()})
        z.write(ROOT/'docs/GAIA_WATER_FLOW_RESPONSE_AUDIT.md','report.md');z.writestr('audit-manifest.json',json.dumps(manifest,indent=2))
    print('WATER_RESPONSE_REPORT_COMPLETE',flush=True)
if __name__=='__main__':main()
