"""Freeze an independent cloth/animated-collider/one-way fluid research fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-root', type=Path, required=True)
    parser.add_argument('--dependencies', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.dependencies), str(args.frontend_root), str(Path(__file__).resolve().parents[1])]
    import numpy as np
    from frontend import App
    from cloth_next.gaia.hydrodynamic_transport import ExperimentalHydroWriter, dense_bricks

    args.output.mkdir(parents=True, exist_ok=False)
    divisions = 20
    points = np.array([[0., y, z] for z in np.linspace(0., .4, divisions+1) for y in np.linspace(0., .4, divisions+1)])
    faces = []
    for j in range(divisions):
        for i in range(divisions):
            q = j*(divisions+1)+i
            faces.extend(((q, q+1, q+divisions+1), (q+1, q+divisions+2, q+divisions+1)))
    faces = np.asarray(faces)
    spec = dict(kind='GAIA.IndependentReproducibilityFixture.v1', cloth_vertices=len(points), cloth_triangles=len(faces),
        cloth_extent_m=[.4, .4], pinned_edge='z=0.4', density=1., young_modulus=1000., poisson_ratio=.3, bend=.1,
        deformation_damping=0., bending_damping=0., gravity=[0., 0., 0.], fluid_density_kg_m3=1000., Cn=1., Ct=0.,
        uniform_fluid_velocity_m_s=[.15, 0., 0.], collider_start_x_m=.08, collider_translation_m=[-.02, 0., 0.],
        collider_animation_seconds=[0., 2.], fps=30., last_output=59, dt=.01, cg_tol=.01,
        purpose='Independent native repeatability and collision regression; no NEON assets or owner manifest. Parameters fixed before test execution.')
    (args.output/'specification.json').write_text(json.dumps(spec, indent=2))
    App.set_backend('cuda')
    app = App.create('gaia-independent-repro', cache_dir=str(args.output/'frontend-cache'))
    app.asset.add.tri('canvas', points, faces)
    body = np.array([[.08, y, z] for z in (-.1, .5) for y in (-.1, .5)])
    app.asset.add.tri('animated_plane', body, np.array([[0, 1, 2], [1, 3, 2]]))
    scene = app.scene.create('independent')
    cloth = scene.add('canvas')
    for key, value in {'density':1., 'young-mod':1000., 'poiss-rat':.3, 'bend':.1, 'deformation-damping':0., 'bending-damping':0.}.items():
        cloth.param.set(key, value)
    cloth.pin(np.flatnonzero(points[:, 2] == .4).tolist())
    scene.add('animated_plane').pin().move_by([-.02, 0., 0.], t_start=0., t_end=2., transition='linear')
    session = app.session.create(scene.build(), 'independent')
    for key, value in {'frames':59, 'fps':30., 'dt':.01, 'gravity':[0., 0., 0.], 'air-density':0.,
                       'disable-contact':False, 'cg-tol':.01, 'min-newton-steps':1, 'max-dx':1.}.items():
        session.param.set(key, value)
    fixed = session.build()
    source = Path(fixed.info.path)
    shutil.copytree(source, args.output/'fixture', ignore=shutil.ignore_patterns('output', 'stdout.log', 'error.log', 'fixed_session.pickle', 'command.bat'))
    writer = ExperimentalHydroWriter(args.output/'volumes', start=0., step=2.1,
        provenance=dict(kind='analytic uniform fluid interior', fixture='independent canvas and animated plane',
                        limits='Constant full occupancy away from domain boundaries; tests solver reproducibility, not surface reconstruction accuracy.'))
    n = 16
    velocity = np.zeros((n, n, n, 4), dtype='<f4')
    velocity[..., 0] = .15
    velocity[..., 3] = 1.
    sdf = np.zeros_like(velocity)
    sdf[..., 0] = -.3
    sdf[..., 3] = 1.
    for _ in range(2):
        for role, values in ((0, velocity), (1, sdf)):
            table, bricks, resolution = dense_bricks(values)
            writer.append(role, table, bricks, resolution, [-1., -1., -1.], np.eye(3), [.125, .125, .125])
    writer.finish()
    (args.output/'model.json').write_text(json.dumps(dict(density_kg_m3=1000., normal_coefficient=1.,
        tangential_coefficient=0., wet_area_fraction=1., velocity_m_s=[0., 0., 0.], volume_mode=1)))
    hashes = {str(p.relative_to(args.output)):hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ('fixture', 'volumes') for p in sorted((args.output/folder).rglob('*')) if p.is_file()}
    (args.output/'input-hashes.json').write_text(json.dumps(hashes, indent=2))
    print(json.dumps(spec, indent=2))


if __name__ == '__main__':
    main()
