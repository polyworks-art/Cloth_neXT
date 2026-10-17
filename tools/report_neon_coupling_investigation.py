"""Summarize real native NEON controls in authoritative vertex-map order."""
import json
import sys
import tomllib
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.ppf.schema import envelope, cbor_codec
from tools.diagnose_neon_native_coupling import upstream_sampler
from cloth_next.bake.pc2 import write_pc2, iter_frames

OUT = ROOT / 'docs/validation/gaia026/coupling-investigation'
FRONTEND = Path('C:/Users/timch/AppData/Local/ClothNeXt/solver/versions/official-2026-10-13-12-32-win64/frontend')
SOURCE = Path('F:/16_Cloth_neXt/cache_AD/.cloth_next_export/scene/00/006a0ef0ba8466634b590c5dd7bb9f2a7e7295e2ecc7438d129afa5beebf175c')
PLAN = json.loads((SOURCE/'metadata.json').read_text())['plan']
SAMPLE = upstream_sampler(FRONTEND)


def load_case(folder, label):
    report = json.loads((OUT/folder/'report.json').read_text())
    record = next(c for c in report['cases'] if c['label'] == label)
    project = Path(record['native_project'])/'session'
    mapping = envelope.loads_envelope((project/'map.pickle').read_bytes(), envelope.KIND_VERTEX_MAP, schema_version=2)
    raw = np.load(OUT/folder/label/'native-positions.npy', mmap_mode='r')
    # Get.vertex returns native solver world coordinates, reordered by map.
    points = {t['name']: np.asarray(raw[:,mapping[t['uuid']]], dtype=float) for t in PLAN['targets']}
    initial_errors = {}
    for t in PLAN['targets']:
        local = np.fromfile(SOURCE/t['initial_artifact'],dtype='<f4').reshape(t['initial_shape'])
        matrix = np.asarray(t['world_matrix'])
        world = local@matrix[:3,:3].T+matrix[:3,3]
        expected = world[:,[0,2,1]]*[1,1,-1]
        initial_errors[t['name']] = float(np.max(np.abs(points[t['name']][0]-expected)))
    assert max(initial_errors.values()) < 1e-6
    body = np.asarray(raw[:, mapping['b4175c13-e11f-f4e5-8f86-4cee05a3ba0e']], dtype=float)
    body_change = float(np.linalg.norm(body-body[0], axis=2).max())
    # The captured body is stationary in these frames. Thus body-relative
    # position differences are the same vectors as world differences.
    assert body_change < 1e-8
    return dict(record=record, project=project, points=points, body_change=body_change, initial_errors=initial_errors)


def norms(x):
    d = np.linalg.norm(x, axis=-1)
    return dict(max=float(d.max()), mean=float(d.mean()), rms=float(np.sqrt(np.mean(d*d))),
                p99=float(np.quantile(d,.99)))


def compare(a,b):
    return {name: dict(position_m=norms(b['points'][name]-x),
        velocity_m_s=norms(np.diff(b['points'][name]-x,axis=0)*30),
        max_centroid_difference_m=float(np.linalg.norm((b['points'][name]-x).mean(axis=1),axis=1).max()))
        for name,x in a['points'].items()}


def adjacent_faces(tri):
    seen = {}; pairs = []
    for i,t in enumerate(tri):
        for a,b in ((t[0],t[1]),(t[1],t[2]),(t[2],t[0])):
            key = tuple(sorted((int(a),int(b))))
            if key in seen:
                pairs.append((seen[key],i))
            else:
                seen[key] = i
    return np.asarray(pairs)


def metrics(case):
    frames = [cbor_codec.loads(p.read_bytes())['payload'] for p in
              sorted((case['project']/'output').glob('statistics_[0-9]*.cbor'))]
    manifest = cbor_codec.loads((case['project']/'output/statistics_manifest.cbor').read_bytes())['payload']
    ids = {o['object_name']:o['object_index'] for o in manifest['objects']}
    result = {}
    for name,x in case['points'].items():
        geometry = np.load(OUT/(name+'-mesh.npz'))
        edges,tri = geometry['edges'],geometry['triangles']
        lengths = np.linalg.norm(x[:,edges[:,0]]-x[:,edges[:,1]],axis=2)
        rest = lengths[0]
        ratios = lengths[:,rest>1e-12]/rest[rest>1e-12]
        normals = np.cross(x[:,tri[:,1]]-x[:,tri[:,0]],x[:,tri[:,2]]-x[:,tri[:,0]])
        areas = np.linalg.norm(normals,axis=2)
        normals /= np.maximum(areas[...,None],1e-30)
        pairs = adjacent_faces(tri)
        angle = np.arccos(np.clip(np.sum(normals[:,pairs[:,0]]*normals[:,pairs[:,1]],axis=2),-1,1))
        change = np.degrees(np.abs(angle-angle[0]))
        counts = [o['contact_count'] for f in frames for o in f['objects'] if o['object_index']==ids[name]]
        result[name] = dict(displacement_m=norms(x-x[0]), speed_m_s=norms(np.diff(x,axis=0)*30),
            contact_peak=max(counts), contact_final=next(o['contact_count'] for f in frames if f['solver_frame']==29 for o in f['objects'] if o['object_index']==ids[name]),
            max_edge_ratio=float(ratios.max()), min_edge_ratio=float(ratios.min()),
            mean_abs_edge_strain=float(np.abs(ratios-1).mean()),
            bend_change_degrees=dict(mean=float(change.mean()),p99=float(np.quantile(change,.99)),max=float(change.max())),
            minimum_triangle_area=float(areas.min()/2), finite=bool(np.isfinite(x).all()))
    return result


def actual_fluid(case):
    directory = case['project']/'bin/force_field'
    record = tomllib.loads((directory/'force_field.toml').read_text())['field'][0]
    f = record['fluid']
    transform = np.fromfile(directory/f['transform'],dtype='<f8').reshape(-1,15)
    volume = dict(resolution=f['resolution'], start=f['start'],step=f['step'],
        corner=transform[:,:3],axes=transform[:,3:12].reshape(-1,3,3).transpose(0,2,1),
        cell=transform[:,12:],tables=[np.fromfile(directory/p,dtype='<u4') for p in f['tables']],
        bricks=[np.fromfile(directory/p,dtype='<f4').reshape(-1,512,4) for p in f['bricks']])
    targets = np.fromfile(directory/'targets.bin',dtype='<u4')
    mapping = envelope.loads_envelope((case['project']/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2)
    results = {}
    for t in PLAN['targets']:
        name=t['name'];x=case['points'][name];v=np.diff(x,axis=0)*30
        rows=[]
        for frame in (1.,15.,15.5,30.):
            offset=frame-1;left=int(offset);right=min(left+1,29);alpha=offset-left
            position=(1-alpha)*x[left]+alpha*x[right]
            vertex_velocity=v[min(left,28)]
            u,rho,presence=SAMPLE(volume,position,offset/30)
            rows.append(dict(frame=frame,occupied_fraction=float(np.mean(rho>0)),
                mean_occupancy=float(rho.mean()), max_occupancy=float(rho.max()),
                mean_fluid_speed=float(np.linalg.norm(u,axis=1).mean()),
                max_fluid_speed=float(np.linalg.norm(u,axis=1).max()),
                mean_vertex_speed=float(np.linalg.norm(vertex_velocity,axis=1).mean()),
                mean_relative_speed=float(np.linalg.norm(u-vertex_velocity,axis=1).mean())))
        results[name]=dict(target_mask_values=np.unique(targets[mapping[t['uuid']]]).tolist(), frames=rows)
    return dict(scope='Actual official solver input bricks, transforms and masks, sampled by unchanged official spatial/temporal reference; host expectation', objects=results)


def main():
    specs={'original_dry1':('zone30-bound','dry1'),'original_dry2':('zone30-bound','dry2'),
        'original_wet1':('zone30-bound','wet1'), 'original_wet2':('repeat30-bound','wet2'),
        'original_wet5':('ab30-bound','wet5'),
        'soft_dry1':('material30-bound','softdry1'),'soft_dry2':('material30-bound','softdry2'),
        'soft_wet1':('material30-bound','softwet1'), 'soft_wet2':('repeat30-bound','softwet2'),
        'contact_off_dry':('contact30-bound','nocontactdry'),'contact_off_wet':('contact30-bound','nocontactwet')}
    cases={name:load_case(*spec) for name,spec in specs.items()}
    comparisons={label:compare(cases[a],cases[b]) for label,a,b in (
        ('original_dry_noise','original_dry1','original_dry2'),
        ('original_wet_dry','original_dry1','original_wet1'),
        ('original_wet_repeat_noise','original_wet1','original_wet2'),
        ('original_second_wet_dry','original_dry1','original_wet2'),
        ('soft_dry_noise','soft_dry1','soft_dry2'),('soft_wet_dry','soft_dry1','soft_wet1'),
        ('material_wet_difference','original_wet1','soft_wet1'),
        ('soft_wet_repeat_noise','soft_wet1','soft_wet2'),
        ('soft_second_wet_dry','soft_dry1','soft_wet2'),
        ('contact_off_wet_dry','contact_off_dry','contact_off_wet'))}
    result=dict(scope='30-frame native controls at 30 FPS; source scene and captured animations unchanged; original controls include saved Inlay zone, soft material uniform. Wet5/contact-off exploratory runs use base materials without Inlay.',
        comparisons=comparisons, cases={name:dict(metrics=metrics(case),body_max_movement_m=case['body_change'],initial_world_transform_errors_m=case['initial_errors'],source_record=case['record']) for name,case in cases.items()},
        native_fluid={name:actual_fluid(cases[name]) for name in ('original_wet1','soft_wet1','contact_off_wet')})
    (OUT/'numerical-summary.json').write_text(json.dumps(result,indent=2))
    previews=[]
    for label in ('original_wet1','original_dry1','soft_wet1','soft_dry1'):
        folder=OUT/'preview-pc2'/label;folder.mkdir(parents=True,exist_ok=True)
        for t in PLAN['targets']:
            # Inverse of the actual exported local-mm -> solver-world matrix.
            z2y=np.array([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]])
            matrix=z2y@np.asarray(t['world_matrix']);inverse=np.linalg.inv(matrix)
            world=cases[label]['points'][t['name']]
            local=world@inverse[:3,:3].T+inverse[:3,3]
            path=folder/(t['name']+'.pc2');write_pc2(path,list(local))
            decoded=np.asarray(list(iter_frames(path)))
            recovered=decoded@matrix[:3,:3].T+matrix[:3,3]
            error=float(np.max(np.abs(recovered-world)))
            assert error<5e-7
            previews.append(dict(case=label,object=t['name'],path=str(path),frames=len(local),
                max_roundtrip_world_error_m=error))
    (OUT/'preview-pc2/manifest.json').write_text(json.dumps(dict(scope='Verified PC2 serialization of native controls; no claim of completed Blender operator import',caches=previews),indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(12,4))
    labels=['Dry repeat','Original wet/dry','Soft wet/dry','Soft dry repeat']
    keys=['original_dry_noise','original_wet_dry','soft_wet_dry','soft_dry_noise']
    for axis,name in zip(axes,cases['original_dry1']['points']):
        axis.bar(range(4),[comparisons[k][name]['position_m']['rms']*1000 for k in keys],color=['#888888','#3b82b0','#df8d26','#bbbbbb'])
        axis.set_title(name.replace('S04_NEON_',''));axis.set_ylabel('Vertex difference RMS (mm)')
        axis.set_xticks(range(4),labels,rotation=35,ha='right')
    fig.suptitle('NEON: native Wet/Dry controls versus measured run variation, frames 1–30')
    fig.tight_layout();fig.savefig(OUT/'material-comparison.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(12,6))
    for axis,name in zip(axes,cases['original_dry1']['points']):
        dry=cases['soft_dry1']['points'][name][-1];wet=cases['soft_wet1']['points'][name][-1]
        delta=np.linalg.norm(wet-dry,axis=1)*1000
        plot=axis.scatter(wet[:,0],wet[:,1],c=delta,s=1,cmap='magma',vmin=0,vmax=8)
        axis.set_title(name.replace('S04_NEON_',''));axis.set_aspect('equal');axis.set_xlabel('World X (m)');axis.set_ylabel('World height (m)')
    fig.colorbar(plot,ax=list(axes),label='Wet/Dry difference (mm)')
    fig.suptitle('Soft material, frame 30: actual native world geometry; no displacement exaggeration')
    fig.savefig(OUT/'soft-wet-dry-geometry.png',dpi=160,bbox_inches='tight');plt.close(fig)
    print(json.dumps(comparisons,indent=2))


if __name__=='__main__':
    main()
