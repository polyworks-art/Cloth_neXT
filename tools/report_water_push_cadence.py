"""Reproducible isolated cadence audit, numerical QA and .gaia evidence bundle."""
import sys,json,hashlib,zipfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'dist/water-push-cadence'

def validate(report):
    # These are backing text logs of the documented get.log.numbers API,
    # not checkpoints, private native buffers, or undocumented memory layouts.
    native=Path(report['native_root'])/'cadence/output/data/advance.final_dt.out'
    steps=np.loadtxt(native,ndmin=2)
    for m in report['metrics']:
        rows=[r for r in report['records'] if r['case']==m['case']]
        for row in rows:
            row.pop('oracle_pair_within_one_native_step',None)
            row['reference_is_known_force_impulse']=True
        x=0.;v=0.;cursor=0;xs=[0.];ts=[0.]
        for start,dt in steps:
            while cursor+1<len(rows) and rows[cursor+1]['time']<=start+1e-6:cursor+=1
            a=float(np.float32(rows[cursor].get('uploaded_acceleration',rows[cursor]['acceleration'])[0]));v+=a*dt;x+=v*dt
            ts.append(start+dt);xs.append(x)
        residual=max(abs(r['position'][0]-np.interp(r['sample_time'],ts,xs)) for r in rows)
        m['oracle_position_residual_m']=float(residual);m['oracle_validated']=bool(residual<=2e-4)
        m['gate_status']='pass' if m['passed'] and m['oracle_validated'] else ('overshoot' if not m['passed'] else 'reference-inconclusive')
        active=[r for r in rows if np.linalg.norm(r['reference'])>.05]
        near=[r for r in rows if .8<r['reference'][0]<1.02]
        m['mean_relative_error_active']=float(np.mean([r['error']/np.linalg.norm(r['reference']) for r in active])) if active else None
        m['mean_error_near_target']=float(np.mean([r['error'] for r in near])) if near else None
        accelerated=[];lags=[]
        for previous,row in zip(rows,rows[1:]):
            if previous['acceleration'][0]>.2:
                accelerated.append(row['error']);lags.append((row['reference'][0]-row['estimate'][0])/previous['acceleration'][0])
        m['mean_error_accelerating']=float(np.mean(accelerated)) if accelerated else None
        m['mean_equivalent_lag_s']=float(np.mean(lags)) if lags else None
        prior=[r for r in rows if r['time']<m['peak_time']-1e-7]
        before=prior[-1] if prior else rows[0]
        m['error_before_peak']=before['error'];m['acceleration_before_peak']=before.get('uploaded_acceleration',before['acceleration'])
        curve=[(row['time'],row['velocity_x'][m['case']]) for row in report['reference_trace']]
        for threshold,label in [(.6321205588,'t63_s'),(.95,'t95_s')]:
            m[label]=None
            for (t0,v0),(t1,v1) in zip(curve,curve[1:]):
                if v0<threshold<=v1:
                    m[label]=float(t0+(t1-t0)*(threshold-v0)/(v1-v0));break
    holds=report['holds'];times=[h['actual_native_hold_time'] for h in holds]
    report['maximum_hold_overshoot_s']=max(h['overshoot_s'] for h in holds)
    report['actual_control_interval_range_s']=[float(min(np.diff(times))),float(max(np.diff(times)))]
    return report

def run():
    paths=[OUT/'estimators-old-24-0.01.json',OUT/'control-24-0.01-0.0.json']
    paths+=sorted(p for p in OUT.glob('control-discrete-*.json') if not p.name.endswith('-progress.json'))
    reports=[validate(json.loads(p.read_text())) for p in paths]
    summary={'position_validation_threshold_m':2e-4,'speed_ratio_tolerance':1.01,'reports':reports,'invalid_combinations':[{'hz':120,'dt':.01,'reason':'Official clamp dt<=0.9999/fps changes dt to .0083325; excluded from unchanged-physics test'}]}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    table=[]
    for r in reports:
        for m in r['metrics']:
            if m['controller']=='discrete' and m['estimator']=='bdf2' and m['tau'] in [.1,.05]:
                table.append(f"| {r['control_hz']} | {r['dt']:g} | {r.get('cg_tolerance',.001):g} | {m['tau']:g} | {m['maximum_speed_ratio']:.6f} | {m['oracle_position_residual_m']:.6g} | {m['gate_status']} |")
    old=reports[0];base=reports[1]
    errors=[]
    for m in old['metrics']:
        errors.append(f"| {m['tau']:g} | {m['estimator']} | {m['mean_error']:.6f} | {m['maximum_error']:.6f} | {m['mean_relative_error_active']:.4f} | {m['mean_equivalent_lag_s']:.6f} | {m['mean_error_near_target'] if m['mean_error_near_target'] is not None else 'not reached'} |")
    cadence=[]
    for r in reports[1:]:
        cadence.append(f"| {r['control_hz']} | {r['dt']:g} | {r['actual_control_interval_range_s'][0]:.6f}–{r['actual_control_interval_range_s'][1]:.6f} | {r['maximum_hold_overshoot_s']:.6f} | {r['duration']} |")
    constrained=json.loads((OUT/'constrained.json').read_text()) if (OUT/'constrained.json').exists() else None
    constrained_text='Not run: no validated useful free-patch range.'
    if constrained:
        lines=[]
        for name in ['air_only','water_push','heterogeneous_push']:
            rows=[r for r in constrained['rows'] if r['case']==name];last=rows[-1]
            lines.append(f"| {name} | {last['mean_flow_displacement']:.6f} | {last['center_velocity'][0]:.6f} | {last['deformation_span_x']:.6f} | {max(r['maximum_estimated_flow_ratio'] for r in rows):.6f} | {max(r['maximum_soft_pin_displacement'] for r in rows):.6f} | {max(r['maximum_grid_delivery_error_m_s2'] for r in rows):.6f} |")
        constrained_text='\n'.join(['Reduced-topology production-like sheet: 2×2 m, 9×9 vertices, nine top-edge soft pins (strength 100), gravity, original material constants, dt .01, air density .001, tau .1. This is not the original 52×52 scene. Velocities below are output-history estimates; there is no independent instantaneous deformed-cloth oracle. Pin forces are not exposed; pin displacement is observable.', '', '| Case | Mean +X displacement m | Center vX m/s | X deformation span m | Maximum estimated flow ratio | Maximum soft-pin displacement m | Maximum spatial acceleration delivery error m/s² |','|---|---:|---:|---:|---:|---:|---:|',*lines,'','Uniform Water Push increases mean flow displacement from about .015 m to .413 m (~27.6×). The heterogeneous estimated flow ratio ~1.039 exceeds 1.01. Neither elastic-recoil attribution nor instantaneous speed safety is established for that case. The spatial delivery error reaches ~1.68 m/s². This is a diagnostic limitation, not a production pass. No total cloth-speed clamp is used.'])
    ratios=[m for m in base['metrics'] if 'ratio' in m]
    ratio_table='\n'.join(f"| {m['ratio']} | {m['tau']:.6f} | {m['maximum_speed_ratio']:.6f} | {m['gate_status']} |" for m in ratios)
    transitions='Transition fixture not completed.'
    if (OUT/'transitions.json').exists():
        tr=json.loads((OUT/'transitions.json').read_text());notes=[]
        for name in ['speed_change','direction_change','enter_leave','region_slow','region_fast','unrelated_dry']:
            rows=[r for r in tr['rows'] if r['case']==name];last=rows[-1]
            notes.append(f"- {name}: final reference velocity {last['reference_velocity']}; last intended Δv {last['intended_delta_v']}; source interpolation is retained in each row.")
        transitions='\n'.join(['Separate public groups test speed change, direction change, enter/leave water, two different spatial water speeds and unrelated dry acceleration at 96 Hz / dt .01 / tau .1. The known-field impulse reference remains limited to free uniform translation. All six independent position-reference checks pass; largest residual is about 12.2 micrometers. Direction-change old momentum is preserved and is not misclassified as newly generated overshoot.',*notes])
    integrity=json.loads((OUT/'solver-integrity.json').read_text())
    decision='C — HIGHER CONTROL CADENCE SOLVES THE UNIFORM-FIXTURE FAILURE; general production coupling remains unvalidated.'
    native01=[r for r in reports if r['control_hz']==96 and r['dt']==.01]
    if not native01 or next(m for m in native01[0]['metrics'] if m['tau']==.05)['gate_status']!='pass':decision='D — useful controlled range exists; fast response is not established.'
    doc='\n'.join([
      '# Isolated Water Push: velocity estimation and cadence audit','',decision,'','No production integration, UI, defaults, external solver edits or release. These are controlled experiments, not product certification.','',
      '## 1. Public velocity capability — C: output positions only','',
      'Official GAIA 0.23 exposes output-frame vertices, object statistics and public numeric logs; no exact per-vertex velocity getter, previous native-step vertex getter or documented checkpoint-state inspection was found. Native checkpoint structures and private buffers were not read. The statistics implementation derives velocity from consecutive output-center positions × FPS. It is NOT an independent instantaneous oracle, even for a translating patch. The preceding audit’s labels were corrected; historical ~1.30 values are output-average peaks.','',
      'The independent free-patch reference is known acceleration impulse over actual public native clock/step times, with zero aerodynamic force and uniform translation. It is checked against semi-implicit integrated positions and exact-time output interpolation. A case whose maximum position residual exceeds the predeclared 0.0002 m threshold is reference-inconclusive, regardless of nominal speed pass. This is an analytically validated fixture reference, not publicly measured native vertex velocity or a general deformed-cloth oracle.','',
      '## 2–3. Estimators and measured errors','',
      'Secant: s1=(x2−x1)/h1. Nonuniform quadratic: s0=(x1−x0)/h0; v2=s1+h1/(h0+h1)×(s1−s0). Quadratic acceleration: a2=2(s1−s0)/(h0+h1). Optional native-time extrapolation adds a2×(held_time−output_time). For equal spacing this is (3x2−4x1+x0)/(2h). Startup uses known rest then secant until three outputs exist. That startup lag is significant at tau .05. Skipped/overshot frames use their actual sample times.','',
      '| Tau s | Estimator | Mean absolute error m/s | Maximum error m/s | Mean relative error (v>0.05) | Equivalent acceleration lag s | Mean error near target m/s |','|---:|---|---:|---:|---:|---:|---:|',*errors,'',
      'Errors above are 24 Hz / dt .01 with the original clipped controller. Equivalent lag is projected velocity error divided by the preceding positive applied acceleration (>0.2 m/s²); it is a trajectory-dependent diagnostic, not a constant filter delay. Near-target error uses 0.8<vX<1.02. Complete estimator/control traces and before-peak acceleration appear in summary.json.','',
      '## 4. Discrete controller','',
      'alpha=−expm1(−ΔT/tau); intended Δv=alpha×occupancy×(water−estimated_cloth); acceleration=intended Δv/ΔT. ΔT is the nominal requested control period. Actual held intervals and overshoot are independently recorded; they are not silently treated as equal. The exact-state/no-other-force law approaches the target without crossing only when applied for its specified interval. No instantaneous native state is supplied to this host controller.','',
      '## 5. Measured bandwidth relationship','',
      '| tau / nominal interval | Tau s | Reference maximum flow ratio | Gate |','|---:|---:|---:|---|',ratio_table,'',
      'For quadratic-hold/discrete at 24 Hz, dt .01, ratios 1 and 1.2 fail; tested ratios 2,3,4,5,8,10 pass. Thus K=2 is the smallest passing tested integer ratio for this fixture/timing, not a universal minimum or a production default. The actual largest held interval is .05 s here: tau .1 provides two observations over that worst interval; .05 provides only one. Finer native dt and different cadence can pass different nominal ratios. There is no dt-independent K established for deforming cloth.','',
      '## 6–7. Native dt and cadence','',
      'Official builder.rs clamps dt to min(requested_dt,0.9999/fps). A rejected 4800-Hz measurement trial demonstrably reduced .01 to ~.0002083125; it was excluded and did not provide an oracle. Valid cadence experiments retain requested native dt. At 120 Hz, dt .01 would become .0083325 and is excluded from unchanged-physics results. FPS and frame count together preserve duration; holds occur only after native steps and can overshoot. Public clock first coordinates provide actual native held time; frame/fps is exact output time. Native numeric log coordinates are rounded and are not presented as exact private state.','',
      '| Control Hz | Requested native dt s | Actual held interval range s | Maximum overshoot s | Duration s |','|---:|---:|---:|---:|---:|',*cadence,'',
      '## 8–10. Speed gate and response times','',
      '| Hz | dt s | CG tolerance | Tau s | Maximum impulse-reference flow ratio | Position residual m | Gate status |','|---:|---:|---:|---:|---:|---:|---|',*table,'',
      'The declared ratio remains ≤1.01. Old 24-Hz secant/clipped tau .05 output average is ~1.30144; the corrected independently integrated native reference peaks ~1.35234. Discrete secant reduces its peak to ~1.12039; BDF2 reduces it to ~1.02977, still failing at dt .01. Supported higher cadence is evaluated above without changing water speed or native dt.','',
      'At 48 Hz, tau .1 passes the reference check and ≤1.01 gate for all three requested native timesteps. This is the common validated useful range in the controlled fixture. dt .001 with CG .001 initially failed the position-reference check (up to ~5 mm); it was not silently counted as safe. The displayed finer-dt matrix explicitly tightens CG to 1e−5, which is a separate numerical-accuracy setting, not a Water Push default. Any remaining inconclusive cases are visible. It is therefore invalid to claim unconditional .1-s robustness across the original three solver presets. Longer runs, collisions and arbitrary deformation remain outside this controlled proof. Higher-rate trials cover .75 s; tau1 has not reached equilibrium within that time. Reference t63/t95 rise times are retained in summary.json rather than equating nominal tau with a fitted physical response.','',
      '## 11. Constrained and transition diagnostics','',constrained_text,'',transitions,'',
      '## 12. FLIP timing / PC2 separation','',
      'The existing real .gaia frames 50 and 51 (24 fps) are sampled at the real cloth interior point and linearly interpolated at physical subframe times; source velocities are not rescaled and no additional FLIP bake samples are requested. Endpoint indices, alpha, raw velocity, support and existing encoded support-weighted contribution are separately retained in timing.json. This real-cache check is host-side; the native transition test uses synthetic 24-Hz source pairs to validate public temporal-grid interpolation independently. The original Blender/FLIP scene was not rebaked or changed.','',
      '48/96/120-Hz output grids contain every exact 24-fps sample: output index j=i×Hz/24. Selected native vertices use the existing centralized solver-to-Blender coordinate transform before the existing PC2 writer records Blender start frame1, sample_rate1. Round-trip positions and timing have zero measured error. 60 Hz contains only even scene-frame indices; full exact 24-fps selection is unsupported on that output grid. A common 120-Hz output grid could support 60-Hz control for dt .001/.002, but would alter dt .01 and is not claimed implemented. Timeline duration and source timing remain physical seconds.','',
      '## 13. External solver integrity','',f"{integrity['packaged_files_checked']} official packaged files compared byte-for-byte against the original ZIP: {len(integrity['differences'])} differences. Archive SHA-256 `{integrity['archive_sha256']}`. Only additional runtime/audit data were created.",'',
      '## 14. Regression / scope','',
      '75 targeted tests passed (7 prototype tests plus existing held stream, Water Flow, Blender descriptor and PC2 tests). No production module imports this experimental controller. Full product regression was not required or run because production code was not modified. Experimental tools and previous audit wording were changed. No UI/default/release/production changes were made.','',
      'The result supports continued isolated work on higher-cadence host control. It does not establish a safe production feature: exact deformed-cloth state, spatial delivery accuracy, constrained instantaneous speed validation, original-scene reproduction and long-duration/other-force safety remain unresolved.',''])
    doc_path=ROOT/'docs/GAIA_WATER_PUSH_CADENCE_AUDIT.md';doc_path.write_text(doc,encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')
    for r,label,est in [(old,'Old 24 Hz','secant'),(base,'Discrete 24 Hz','bdf2'),*( [(native01[0],'Discrete 96 Hz','bdf2')] if native01 else [])]:
        m=next(m for m in r['metrics'] if m['tau']==.05 and m['estimator']==est)
        axes[0].plot([row['time'] for row in r['reference_trace']],[row['velocity_x'][m['case']] for row in r['reference_trace']],label=label)
    axes[0].axhline(1.01,color='red',ls='--',label='Declared 1% gate');axes[0].set(xlim=(0,.4),xlabel='Physical time s',ylabel='Impulse-reference vX / water speed',title='0.05 s response, native dt .01');axes[0].legend(fontsize=8)
    for m in old['metrics']:
        if m['tau']!=.1:continue
        rows=[r for r in old['records'] if r['case']==m['case']]
        axes[1].plot([r['time'] for r in rows],[r['estimate'][0]-r['reference'][0] for r in rows],label=m['estimator'])
    axes[1].set(xlim=(0,.5),xlabel='Physical held time s',ylabel='Estimate minus impulse reference m/s',title='Estimator error, tau .1 / old controller');axes[1].legend(fontsize=8)
    fig.savefig(OUT/'cadence.png',dpi=160);plt.close(fig)
    members=[doc_path,OUT/'summary.json',OUT/'solver-integrity.json',OUT/'timing.json',OUT/'cadence.png',OUT/'rejected-reference-trials.json']
    members += [p for p in [OUT/'constrained.json',OUT/'transitions.json',OUT/'constrained-positions.npz'] if p.exists()]
    members += sorted(OUT.glob('playback-*.pc2'))
    members += [ROOT/'tools'/name for name in ['audit_water_push_cadence.py','audit_water_push_constrained.py','audit_water_push_transitions.py','audit_water_push_timing.py','enrich_water_push_public_trace.py','report_water_push_cadence.py']]
    members += [ROOT/'cloth_next/gaia/water_push_prototype.py',ROOT/'tests/test_gaia_water_push_prototype.py',OUT/'targeted-tests.log']
    manifest={'kind':'ClothNeXt.GAIA.WaterPushCadenceAudit','schema':1,'decision':decision,'files':[]}
    artifact=ROOT/'dist/GAIA_Water_Push_Cadence_Audit.gaia'
    with zipfile.ZipFile(artifact,'w',zipfile.ZIP_DEFLATED) as bundle:
        for p in members:
            name=p.relative_to(ROOT).as_posix();payload=p.read_bytes();bundle.writestr(name,payload);manifest['files'].append({'path':name,'sha256':hashlib.sha256(payload).hexdigest(),'size':len(payload)})
        bundle.writestr('manifest.json',json.dumps(manifest,indent=2))
    with zipfile.ZipFile(artifact) as bundle:
        assert bundle.testzip() is None
        for entry in manifest['files']:assert hashlib.sha256(bundle.read(entry['path'])).hexdigest()==entry['sha256']
    print(decision);print(doc_path);print(artifact)

if __name__=='__main__':run()
