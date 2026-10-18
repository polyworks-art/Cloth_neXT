"""Build non-NEON contact benchmark sessions with the upstream frontend (no solve).

Run with the runtime python, PYTHONPATH=<native-source>. Writes one session per
case below --output and prints its directory. Scenes follow the upstream
examples (headless sheets over a moving sphere; twisted cylinder) with only the
resolution/frames varied, so they cover mesh size, contact density and complex
self-collision. No hydrodynamics.
"""
import argparse, json, shutil, sys
from pathlib import Path


def sheets(app, res, frames):
    V, F = app.mesh.square(res=res, ex=[0, 0, 1], ey=[0, 1, 0])
    app.asset.add.tri("sheet", V, F)
    V, F = app.mesh.icosphere(r=0.5, subdiv_count=4)
    app.asset.add.tri("sphere", V, F)
    scene = app.scene.create()
    for i in range(5):
        obj = scene.add("sheet")
        obj.at(i * 0.25, 0, 0)
        obj.pin(obj.grab([0, 1, 0]))
        obj.param.set("strain-limit", 0.05)
    # Fixed jitter-free placement keeps the scene byte-reproducible.
    scene.add("sphere").at(-1, 0, 0).pin().move_by([8, 0, 0], t_start=0.0, t_end=5)
    session = app.session.create(scene.build())
    session.param.set("dt", 0.01).set("min-newton-steps", 8).set("frames", frames)
    return session


def twist(app, n, frames):
    V, F = app.mesh.cylinder(r=0.5, min_x=-1.0, max_x=1.0, n=n)
    app.asset.add.tri("cylinder", V, F)
    scene = app.scene.create()
    obj = scene.add("cylinder")
    obj.param.set("bend", 500).set("contact-gap", 4e-3).set("young-mod", 10000)
    angular, delta, t_end = 360.0, 0.2, 10
    scale = 1 - t_end * 0.01
    (obj.pin(obj.grab([-1, 0, 0])).spin(axis=[1, 0, 0], angular_velocity=angular)
        .scale(scale, t_start=0.0, t_end=t_end).move_by([delta, 0, 0], t_start=0.0, t_end=t_end))
    (obj.pin(obj.grab([1, 0, 0])).spin(axis=[-1, 0, 0], angular_velocity=angular)
        .scale(scale, t_start=0.0, t_end=t_end).move_by([-delta, 0, 0], t_start=0.0, t_end=t_end))
    session = app.session.create(scene.build())
    session.param.set("dt", 1 / 60).set("frames", frames).set("gravity", [0, 0, 0]).set("csrmat-max-nnz", 35000000)
    return session


CASES = {
    'sheets-32': (sheets, 32), 'sheets-64': (sheets, 64), 'sheets-128': (sheets, 128),
    'twist-120': (twist, 120), 'twist-240': (twist, 240), 'twist-360': (twist, 360),
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cases', nargs='+', default=list(CASES))
    p.add_argument('--frames', type=int, default=60)
    a = p.parse_args()
    from frontend import App
    a.output.mkdir(parents=True, exist_ok=True)
    for name in a.cases:
        builder, size = CASES[name]
        app = App.create('bench-' + name)
        session = builder(app, size, a.frames).build()
        source = Path(session.info.path)
        target = a.output/name
        if target.exists(): shutil.rmtree(target)
        shutil.copytree(source, target)
        print(name, source, '->', target, flush=True)


if __name__ == '__main__':
    main()
