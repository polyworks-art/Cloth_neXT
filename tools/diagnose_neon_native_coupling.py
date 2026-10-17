"""Measure native packed fluid at real PC2 vertices with upstream interpolation.

This reports host expectations, never a measured solver force. Source archives,
PC2 files and upstream implementation are read only.
"""
import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.native_fluid import pack_frame, AXES
from cloth_next.bake.pc2 import iter_frames


def upstream_sampler(frontend):
    package = types.ModuleType('_neon_official')
    package.__path__ = [str(frontend)]
    sys.modules[package.__name__] = package
    name = package.__name__ + '._field_eval_'
    spec = importlib.util.spec_from_file_location(name, frontend / '_field_eval_.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.fluid_at


def packed_volume(field, time):
    table, bricks = pack_frame(field, cell_centers=True)
    return dict(resolution=[list(field.influence.shape[::-1])], tables=[table],
        bricks=[bricks], axes=[AXES],
        corner=[[field.minimum[0], field.minimum[2], -field.minimum[1]]],
        cell=[((np.asarray(field.maximum)-field.minimum) /
               np.asarray(field.influence.shape[::-1])).tolist()], start=time, step=1/30)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--frontend', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--frames', type=int, nargs='+', default=[1, 30, 60, 125, 250])
    p.add_argument('--all-frames', action='store_true')
    args = p.parse_args()
    if args.all_frames:
        args.frames = list(range(1, 251))
    sample = upstream_sampler(args.frontend)
    snapshot = json.loads(args.snapshot.read_text())
    fps = snapshot['fps']
    results = []
    for index, obj in enumerate(snapshot['objects']):
        settings = obj['settings']
        local = np.asarray(list(iter_frames(args.cache / f'cn_test_cloth_216c98d6c6dd_{index:02}.pc2')))
        matrices = np.asarray(obj['matrices'])
        world = np.einsum('fij,fvj->fvi', matrices[:, :3, :3], local) + matrices[:, None, :3, 3]
        velocity = np.zeros_like(world)
        velocity[1:] = np.diff(world, axis=0)*fps
        velocity[0] = velocity[1]
        rows = []
        with GaiaContainer(settings['water_flow_container']) as container:
            manifest = container.manifest
            frame_indices = {r['frame']: i for i, r in enumerate(manifest['frames'])}
            for frame in args.frames:
                field = container.frame(frame_indices[frame])
                volume = packed_volume(field, (frame-1)/fps)
                points = world[frame-1]
                solver_points = points[:, [0, 2, 1]] * [1, 1, -1]
                u, rho, presence = sample(volume, solver_points, (frame-1)/fps)
                u = u[:, [0, 2, 1]] * [1, -1, 1]
                relative = u-settings['water_flow_native_flow']*velocity[frame-1]
                speed = np.linalg.norm(u, axis=1)
                strength = settings['water_flow_coupling_strength']
                weight = settings['water_flow_influence']*rho
                expected = strength*weight[:, None]*relative
                ids = sorted(set([0, len(points)//2, len(points)-1, int(rho.argmax())]))
                rows.append(dict(frame=frame, inside_domain_fraction=float(presence.mean()),
                    occupied_fraction=float(np.mean(rho > 0)),
                    influenced_fraction=float(np.mean(np.linalg.norm(expected, axis=1) > 1e-12)),
                    mean_occupancy=float(rho.mean()), max_occupancy=float(rho.max()),
                    mean_fluid_speed=float(speed.mean()), max_fluid_speed=float(speed.max()),
                    mean_vertex_speed=float(np.linalg.norm(velocity[frame-1], axis=1).mean()),
                    mean_relative_speed=float(np.linalg.norm(relative, axis=1).mean()),
                    vertices=[dict(index=i, world_position=points[i].tolist(),
                        vertex_velocity=velocity[frame-1, i].tolist(), fluid_velocity=u[i].tolist(),
                        occupancy=float(rho[i]), effective_field_weight=float(weight[i]),
                        coupling_strength=strength, relative_velocity=relative[i].tolist(),
                        expected_acceleration=expected[i].tolist()) for i in ids]))
                print(obj['name'], frame, rows[-1]['occupied_fraction'], rows[-1]['max_fluid_speed'], flush=True)
        results.append(dict(object=obj['name'], settings=settings, frames=rows,
            sampled_contact_frames=sum(r['occupied_fraction'] > 0 for r in rows),
            source_water=manifest['water']))
    args.output.write_text(json.dumps(dict(scope='Host expectation using official spatial and temporal sampler on native packed export; historical final upload not retained',
        objects=results), indent=2))


if __name__ == '__main__':
    main()
