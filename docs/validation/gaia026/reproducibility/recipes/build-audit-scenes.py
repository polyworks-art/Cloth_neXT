"""Audit scenes for the feature groups the fast deterministic path does not cover yet.

Run with the bench runtime python (frontend from that tree). One small scene per
group, built with the upstream frontend API, no hydrodynamics:
  static   cloth draped over a fully pinned static collider  -> collision-mesh traversals
  pdrd     rigid (PDRD) bodies dropped onto a pinned sheet   -> pdrd_* reductions
  lock     tet bodies with translation locks onto a sheet     -> lock_* accumulations
  rod      rods (strands) falling into a bowl                 -> rod / Cosserat paths
"""
import argparse, shutil
from pathlib import Path


def sheet(app, scene, res=48):
    V, F = app.mesh.square(res=res, ex=[1, 0, 0], ey=[0, 0, 1])
    app.asset.add.tri("sheet", V, F)
    obj = scene.add("sheet").at(0, 0, 0)
    for corner in ([-1, 0, -1], [1, 0, 1], [1, 0, -1], [-1, 0, 1]):
        obj.pin(obj.grab(corner))
    return obj


def static(app, frames):
    V, F = app.mesh.square(res=48, ex=[1, 0, 0], ey=[0, 0, 1])
    app.asset.add.tri("sheet", V, F)
    V, F = app.mesh.icosphere(r=0.45, subdiv_count=4)
    app.asset.add.tri("sphere", V, F)
    scene = app.scene.create()
    collider = scene.add("sphere").at(0, -0.6, 0)
    collider.pin(); collider.update_static()
    for i in range(3):
        scene.add("sheet").at(0.05 * i, 0.12 * i, 0.03 * i)
    return scene, dict(dt=0.01, frames=frames)


def pdrd(app, frames):
    V, F = app.mesh.icosphere(r=0.12, subdiv_count=2)
    app.asset.add.tri("ball", V, F)
    scene = app.scene.create()
    sheet(app, scene)
    for i in range(3):
        for j in range(3):
            scene.add("ball").at(-0.5 + 0.5 * i, 0.3 + 0.05 * (i + j), -0.5 + 0.5 * j).as_pdrd()
    return scene, dict(dt=0.01, frames=frames)


def lock(app, frames):
    V, F, T = app.mesh.icosphere(r=0.12, subdiv_count=2).tetrahedralize()
    app.asset.add.tet("ball", V, F, T)
    scene = app.scene.create()
    sheet(app, scene)
    for i in range(3):
        for j in range(3):
            scene.add("ball").at(-0.5 + 0.5 * i, 0.3 + 0.05 * (i + j), -0.5 + 0.5 * j).lock_translation(0, 1, 0)
    return scene, dict(dt=0.01, frames=frames)


def rod(app, frames):
    V, E = app.mesh.line([0, 0.01, 0], [0.01, 3, 0], 240)
    app.asset.add.rod("strand", V, E)
    scene = app.scene.create()
    scene.add.invisible.sphere([0, 1, 0], 1.1).invert().hemisphere()
    for i in range(5):
        for j in range(5):
            obj = scene.add("strand").at(0.05 * (i - 2.5), 0, 0.05 * (j - 2.5))
            obj.param.set("contact-gap", 3e-3).set("contact-offset", 3e-3).set("friction", 0.05)
    return scene, dict(dt=0.01, frames=frames)


CASES = dict(static=static, pdrd=pdrd, lock=lock, rod=rod)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cases', nargs='+', default=list(CASES)); p.add_argument('--frames', type=int, default=60)
    a = p.parse_args()
    from frontend import App
    a.output.mkdir(parents=True, exist_ok=True)
    for name in a.cases:
        app = App.create('audit-' + name)
        scene, params = CASES[name](app, a.frames)
        session = app.session.create(scene.build())
        for k, v in params.items(): session.param.set(k, v)
        fixed = session.build()
        target = a.output/name
        if target.exists(): shutil.rmtree(target)
        shutil.copytree(Path(fixed.info.path), target)
        print(name, '->', target, sorted(q.name for q in (target/'bin').iterdir())[:40], flush=True)


if __name__ == '__main__':
    main()
