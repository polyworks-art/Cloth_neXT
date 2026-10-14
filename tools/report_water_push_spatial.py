"""Report the spatial audit and mandatory architectural stop; no solver execution."""
import sys,json,hashlib,zipfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));OUT=ROOT/'dist/water-push-spatial'

def integrity():
    archive=Path(r'C:\Users\timch\AppData\Local\ClothNeXt\solver\downloads\ppf-contact-solver-2026-09-27-20-44-win64.zip');official=ROOT/'dist/gaia-streaming/official';differences=[];count=0
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if item.is_dir():continue
            count+=1;p=official/item.filename
            if not p.is_file() or hashlib.sha256(p.read_bytes()).digest()!=hashlib.sha256(package.read(item)).digest():differences.append(item.filename)
    with archive.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    result={'archive_sha256':digest,'packaged_files_checked':count,'differences':differences};(OUT/'solver-integrity.json').write_text(json.dumps(result,indent=2));assert not differences
    return result

def run():
    r=json.loads((OUT/'host-audit.json').read_text());g=json.loads((OUT/'group-targeting.json').read_text());i=integrity()
    assert max(x['max_error_m_s2'] for x in r['constant_preservation'])<1e-6
    assert g['group_cross_contamination']==0
    folded=np.load(OUT/'connected-folded-sheet.npz');tri=folded['triangles'];count=len(folded['positions']);neighbors=[set() for _ in range(count)]
    for face in tri:
        for a in face:neighbors[a].update(int(b) for b in face)
    visited={0};pending=[0]
    while pending:
        for b in neighbors[pending.pop()]-visited:visited.add(b);pending.append(b)
    assert len(visited)==count
    tables=[]
    for method in ['idw4','normalized_splat']:
        for res in [32,64,100,150]:
            rows=[x for x in r['matrix'] if x['method']==method and x['resolution']==res]
            tables.append(f"| {method} | {res} | {sum(x['passed'] for x in rows)}/12 | {max(x['relative_mean'] for x in rows)*100:.4f}% | {max(x['relative_p95'] for x in rows)*100:.4f}% | {max(x['relative_max'] for x in rows)*100:.4f}% | {max(x['direction_max_degrees'] for x in rows):.4f} | {max(x['absolute_max_m_s2'] for x in rows):.6f} | {max(x['dry_vertex_max_m_s2'] for x in rows):.6f} |")
    perf=[]
    for method in ['idw4','normalized_splat']:
        for res in [32,64,100,150]:
            rows=[x for x in r['matrix'] if x['method']==method and x['resolution']==res]
            perf.append(f"| {method} | {res} | {max(x['field_bytes'] for x in rows)/1024**2:.3f} | {max(x['construction_s'] for x in rows)*1000:.3f} | {max(x['host_encoding_s'] for x in rows)*1000:.3f} |")
    overlap=[]
    for gap in [0.,.0002,.001,.005]:
        rows=[x for x in r['self_overlap'] if x['gap_m']==gap]
        best=min(rows,key=lambda x:x['absolute_max_m_s2'])
        overlap.append(f"| {gap:g} | {best['resolution']} | {best['absolute_max_m_s2']:.6f} | {best['relative_max']*100:.2f}% | {sum(x['passed'] for x in rows)}/4 |")
    dry=[]
    for res in [32,64,100,150]:
        row=next(x for x in r['matrix'] if x['state']=='free' and x['field']=='localized' and x['method']=='normalized_splat' and x['resolution']==res)
        dry.append(f"| {res} | {row['dry_vertex_max_m_s2']:.6f} | {row['off_vertex_dry_probe_max_m_s2']:.6f} | {row['off_vertex_far_dry_probe_max_m_s2']:.6f} |")
    proof=r['representability_proof'];old=r['historical_replay']
    text='\n'.join([
      '# Water Push spatial architecture audit','',
      '**Classification C for general deforming/self-overlapping cloth. Stop development on the present spatial, object-group-targeted acceleration architecture.**','',
      'This is narrower than saying Water Push has no useful effect: nonoverlapping point targets can be fitted and separate objects can be isolated. The required same-object overlap case is significant and fails a representability gate that grid resolution cannot repair. No production code, UI, default, source water, normal Water Flow or external GAIA file was changed.','',
      '## Frozen experiment and thresholds','',
      '48 Hz, tau .10 s, existing nonuniform quadratic/BDF2 estimator, alpha=1−exp(−ΔT/tau), desired Δv=alpha×occupancy×(water−estimated cloth), acceleration=Δv/ΔT. Only the historical baseline replay uses its original 24-Hz timing to reproduce that recorded error; it is not a new working point.','',
      'Acceptance thresholds written before test execution: mean relative error ≤5%, P95 ≤10%, maximum ≤20%, maximum direction error ≤5° for targets ≥.1 m/s². Relative statistics exclude smaller targets; their absolute errors remain recorded. Dry diagnostic vertices require ≤1e−6 m/s². No threshold was relaxed. Constant inputs must be preserved within floating-point tolerance.','',
      '## Current method and the 1.68 m/s² root cause','',
      'The preceding prototype constructs a 5×13×13 grid over cloth bounds ±.1 m. Each grid node receives an inverse-squared-distance average of the four nearest cloth vertices. The same grid is then trilinearly sampled back at the vertices. This is two interpolations, not interpolation constraints satisfied at each vertex. Grid nodes mix different correction velocities; fixed coarse spacing and padding smooth gradients. Occupancy is only present in the target samples, not an independent off-vertex wet/dry gate.','',
      f"Historical replay of the actual saved 81-vertex gravity/soft-pin trajectory reproduces maximum error **{old['reconstructed_max_m_s2']:.12f} m/s²**, exactly matching the recorded {old['original_recorded_max_m_s2']:.12f}. The cause is reconstruction/double sampling, not native integration error: this replay does not advance a solver.",'',
      '## Synthetic reference fields and target data','',
      'Four analytic Blender-world fields: step +X .5/2.0 m/s; smooth +X .5→2.0; direction gradient +X→+Z; center strip +X2 with zero occupancy and zero water outside. Source fields are analytic and unchanged across Push resolutions. They do not inherit WaterVelocityField resolution.','',
      'Primary patch: 17×17, 289 material points over a 2 m square. Three manufactured position-history states represent a free translating/deforming patch, a top-edge constrained shape and a gravity-like constrained shape. They are host geometry/velocity states, not new native pinned/gravity simulations. BDF2 uses the actual three sample times. Existing production-like material constants are retained as reference metadata; host reconstruction itself does not simulate that material.','',
      'For all 12 states/fields the target dataset is persisted before reconstruction. desired-vertices.npz records position, estimated velocity, water, occupancy, relative velocity, desired Δv and desired acceleration, with the column mapping in host-audit.json. delivered-vertices.npz retains per-vertex samples for every method/resolution and overlap test. Metrics compare the same float32 grid at the same positions, using inclusive boundary-node trilinear weights and zero outside, as documented by official GAIA. CPU accumulation is not claimed bit-identical to GPU floating-point execution.','',
      '## Resolution and reconstruction comparison','',
      'Candidate: normalized trilinear splatting to eight corner nodes, followed by deterministic nearest occupied-node extension into empty nodes. No smoothing gain, ML system, altered water/occupancy or estimator is used. Both methods preserve constant vectors; all 24 constant checks have maximum error below 1e−6 m/s².','',
      'The table aggregates the worst statistic across the 12 states/fields, rather than hiding a difficult field in a global average. Pass counts apply to the supplied diagnostic vertices only.','',
      '| Method | Longest-axis nodes | Supplied-point passes | Worst mean relative | Worst P95 relative | Worst maximum relative | Maximum direction ° | Maximum absolute m/s² | Maximum dry-vertex force m/s² |','|---|---:|---:|---:|---:|---:|---:|---:|---:|',*tables,'',
      'Normalised splatting fits these non-overlapping supplied targets much better. This does not certify between-vertex delivery or behavior while vertices move. Resolution improves IDW, but its mathematics still mixes the targets and bleeds force at dry vertices.','',
      '## Local domain and motion padding','',
      'Push domain is the current participating cloth bounds, independent of the entire FLIP domain. Per-axis padding includes .001 m numerical margin, maximum current estimated velocity, analytic water velocity, 1/48+.01 s control/native-overshoot window, desired acceleration and gravity contribution. Dimensions preserve near-isotropic spacing and use longest-axis targets 32/64/100/150. Bounds and actual dimensions are retained for every test.','',
      'This is a host prospective-motion budget. Unknown collision/elastic impulses are not bounded by this formula; no native claim that a future vertex cannot leave the box is made.','',
      '## Dry leakage: supplied vertices versus unsampled cloth','',
      'Splatting gives exactly zero at supplied dry vertices, but nearest occupied-node extension can apply large forces to dry points between samples. Additional probes follow actual piecewise-linear cloth edges outside the analytic strip. “Far” excludes the first two grid spacings from the wet boundary, so this is not merely one-cell legitimate boundary interpolation.','',
      '| Resolution | Force at supplied dry vertices | Maximum dry off-vertex force m/s² | Maximum far-dry force m/s² |','|---:|---:|---:|---:|',*dry,'',
      'The high-resolution candidate still reaches approximately 16.7 m/s² on dry off-vertex points. Thus passing reconstruction at currently supplied vertices does not establish controlled dry-region delivery throughout a control interval. No occupancy workaround or new source scaling was introduced after observing this failure.','',
      '## Different objects / official group isolation','',
      f"The documented ForceField.grid(groups=...) and official resolve_targets helper produce independent masks 1 and 2 for two coincident object point sets. At exactly the same position, stationary A receives {g['A_delivered_acceleration'][0]:.8f} m/s² while already-matched B receives zero. Cross-group contamination is exactly zero in this official host targeting test. The native kernel checks the target bit before sampling each grid; no private mask or built-scene context was changed.",'',
      'This verifies the supported host routing, not a new native two-object dynamics run. Current public Object.group selects a whole object; update_force_field uses the built scene group map. Separate objects can therefore receive distinct fields; the same single cloth object cannot dynamically label its overlapping layers through that interface.','',
      '## Mandatory single-object self-overlap / representability gate','',
      f"The folded fixture is one connected mesh with {count} vertices and {len(tri)} triangles, sharing a 17-vertex top hinge. The second panel folds back onto the first; velocity difference tapers to zero at the shared hinge. The zero-gap geometry is a limiting architectural test, not a valid collision-resolved native initial state. Additional host layered cross-sections test .2 mm, 1 mm and 5 mm gaps. Connectivity was independently checked.",'',
      '| Layer gap m | Best tested resolution by maximum absolute error | Maximum absolute error m/s² | Maximum relative error | Passes |','|---:|---:|---:|---:|---:|',*overlap,'',
      f"At a coincident pair with water +X1 m/s and cloth velocities 0 and +X1 m/s, the desired accelerations are **{proof['desired_accelerations_m_s2'][0][0]:.6f} and 0 m/s²**. Any single-valued a(x,t) returns the same acceleration to both. Even its optimal minimax compromise has at least **{proof['unavoidable_minimax_absolute_error_m_s2']:.6f} m/s²** error. This contradiction applies to every resolution and every reconstruction method.",'',
      f"To stay within the 20% maximum target error on the stationary layer requires at least {proof['slow_layer_minimum_acceptable_acceleration_m_s2']:.6f} m/s². Applying that same correction to the already-matched layer contributes at least a **{proof['shared_field_after_nominal_tick_fast_speed_ratio_if_slow_layer_max_error_passes']:.6f} speed ratio after one nominal tick**, exceeding 1.01. This is an analytical Water-Push contribution, not a measured instantaneous native speed. Preserving the matched layer's 1% limit would permit only .48 m/s² and grossly miss the slow layer's target.",'',
      f"Distinct positive-gap points are not mathematically identical: in principle an arbitrarily steep field could distinguish them. At .2 mm the exact target jump requires about {proof['near_layer_required_gradient_for_0_2mm_m_s2_per_m']:.0f} m/s² per metre. The tested bounded grids still mix those layers badly. Folded/contacting multilayer cloth is materially relevant to general cloth, not a rare out-of-domain point. Object group separation does not resolve the same-object case.",'',
      '## Stop and deliberately unrun phases','',
      'The supplied request explicitly says: “If this limitation is significant in realistic cloth: STOP and report it. Do not hide it with grid resolution.” The mandatory architectural gate fails, and off-vertex dry leakage remains uncontrolled. Further tuning on this architecture is stopped.','',
      '- Native heterogeneous validation and the new ≤1.01 heterogeneous speed test: not run; no claim that the old estimated 1.039 issue is fixed.','- New 48-Hz constrained comparison (air-only/current/improved): not run after the stop. The preceding 24-Hz observed .015/.413 m transport comparison remains historical evidence, not a new success result.','- Real FLIP host reconstruction and full original-scene simulation: not run after the representability/dry-delivery failure. Original FLIP/cache/scene data remain untouched.','- New native encoding/export and additional held-update overhead: not measured after the stop. Only host construction and byte encoding are measured below.','',
      '## Host performance at the frozen 48-Hz working point','',
      '| Method | Resolution | Maximum final field MiB | Maximum construction ms | Maximum host byte encoding ms |','|---|---:|---:|---:|---:|',*perf,'',
      'The per-control budget is 20.833 ms. A final field is at most about 4.38 MiB in this matrix; intermediate working allocations are additional and no full-process RSS claim is made. High-resolution construction can exceed the entire budget before native export/held overhead. Byte encoding is an in-memory contiguous serialization measurement, not a disk/native update timing. Lower resolutions are cheaper, but correctness and architectural failure prevent a practical product claim or default selection.','',
      '## Integrity and verification','',
      f"All {i['packaged_files_checked']} official packaged files are byte-identical to the original ZIP: zero differences. ZIP SHA-256 `{i['archive_sha256']}`. No native solver was executed for this spatial task.",'',
      '75 targeted existing controller/held-stream/Water Flow/PC2 regression tests passed. Additional numerical checks cover 24 constant-preservation cases, 96 supplied-target reconstruction cases, 16 overlap cases, official group-mask isolation and connected folded topology. Production code was not modified, so a full product regression was not run.','',
      '**Final answer: C for general deforming cloth under the current single-object group-targeted spatial API.** Restricted non-overlapping point targets and separate object groups are useful demonstrations, not completion of the stated general-cloth requirements. A future architecture would need supported per-vertex/velocity-aware coupling or genuinely separate material-layer identity; this audit does not implement or propose a solver patch.',''])
    doc=ROOT/'docs/GAIA_WATER_PUSH_SPATIAL_AUDIT.md';doc.write_text(text,encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4),layout='constrained')
    for method in ['idw4','normalized_splat']:
        values=[max(x['relative_max'] for x in r['matrix'] if x['method']==method and x['resolution']==res)*100 for res in [32,64,100,150]]
        axes[0].plot([32,64,100,150],values,'o-',label=method)
    axes[0].axhline(20,color='red',ls='--',label='20% maximum budget');axes[0].set(xlabel='Push longest-axis nodes',ylabel='Worst supplied-point relative error %',title='Non-overlapping supplied targets');axes[0].legend(fontsize=8)
    axes[1].bar(['Required slow','Required matched','Best shared'],[proof['desired_accelerations_m_s2'][0][0],0,proof['unavoidable_minimax_absolute_error_m_s2']],color=['tab:blue','tab:green','tab:orange'])
    axes[1].set(ylabel='Acceleration m/s²',title='Same position / same group: incompatible targets')
    fig.savefig(OUT/'spatial.png',dpi=160);plt.close(fig)
    members=[doc,*[OUT/name for name in ['declared-budget.json','host-audit.json','group-targeting.json','solver-integrity.json','desired-vertices.npz','delivered-vertices.npz','connected-folded-sheet.npz','spatial.png','targeted-tests.log']],*[ROOT/'tools'/name for name in ['audit_water_push_spatial.py','audit_water_push_group_targets.py','report_water_push_spatial.py']]]
    manifest={'kind':'ClothNeXt.GAIA.WaterPushSpatialAudit','schema':1,'classification':'C','architecture_stopped':True,'files':[]};artifact=ROOT/'dist/GAIA_Water_Push_Spatial_Audit.gaia'
    with zipfile.ZipFile(artifact,'w',zipfile.ZIP_DEFLATED) as z:
        for p in members:
            payload=p.read_bytes();name=p.relative_to(ROOT).as_posix();z.writestr(name,payload);manifest['files'].append({'path':name,'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload)})
        z.writestr('manifest.json',json.dumps(manifest,indent=2))
    with zipfile.ZipFile(artifact) as z:
        assert z.testzip() is None
        for f in manifest['files']:assert hashlib.sha256(z.read(f['path'])).hexdigest()==f['sha256']
    print('C — mandatory architecture stop');print(doc);print(artifact)

if __name__=='__main__':run()
