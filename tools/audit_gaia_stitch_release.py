"""Isolated public-API CUDA checkpoint/stitch-release feasibility experiment.

Run using the unchanged official bundle Python from its root. No production UI.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid
import numpy as np
from frontend import App


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--keep-stitches', action='store_true')
    args = parser.parse_args()
    report = {'result': 'INCOMPLETE', 'events': []}
    App.set_backend('cuda')
    app = App.create('cnx-release-audit-' + uuid.uuid4().hex)
    vertices, triangles = [], []
    for left in (0.0, 0.4):
        base = len(vertices)
        vertices += [[left, 0, 0], [left+.2, 0, 0],
                     [left+.2, 0, .2], [left, 0, .2]]
        triangles += [[base, base+1, base+2], [base, base+2, base+3]]
    app.asset.add.tri('panels', np.asarray(vertices, float), np.asarray(triangles))
    ind = np.asarray([[a,a,a,b,b,b] for a,b in ((1,4),(2,7))])
    w = np.asarray([[1,0,0,1,0,0]] * 2, float)
    app.asset.add.stitch('boundary', (ind, w))
    scene = app.scene.create('release')
    cloth = scene.add('panels')
    cloth.param.set('density', 1000.).set('stitch-stiffness', 100.)
    cloth.stitch('boundary')
    fixed = scene.build()
    session = app.session.create(fixed, 'release')
    session.param.set('gravity', [0.,0.,0.])
    session.param.set('frames', 35).set('fps', 1000.).set('dt', .0001)
    current = session.build()
    binary = Path(current.info.path) / 'bin'
    indices = np.fromfile(binary/'stitch_ind.bin', np.uint64).reshape(-1,6)
    weights = np.fromfile(binary/'stitch_w.bin', np.float32).reshape(-1,6)
    strength = np.fromfile(binary/'stitch_stiffness.bin', np.float32)
    try:
        current.run_until_frame(10, timeout=60)
        assert current.held_frame() == 10
        # This experiment releases on measured seam contraction, not a claim
        # of material fracture. Production damage thresholds are unaudited.
        for boundary, row in ((10,0),(20,1)):
            current.save_and_quit()
            deadline = time.monotonic()+60
            while current.is_running():
                if time.monotonic()>deadline: raise TimeoutError('save-and-quit')
                time.sleep(.05)
            output = Path(current.info.path)/'output'
            checkpoint = output/f'state_{boundary}.bin.gz'
            assert checkpoint.is_file()
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            pose = current.get.vertex(boundary)[0].copy()
            if not args.keep_stitches:
                strength[row] = 0.
            fixed.set_stitch(indices, weights, stiffness=strength)
            current = session.build(preserve_output=True)
            assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == digest
            np.testing.assert_array_equal(current.get.vertex(boundary)[0], pose)
            # Explicit resume avoids the public run_until_frame -> start ->
            # auto-resume path clearing its own pending hold on the second start.
            current.resume(frame=boundary, blocking=False)
            current.run_until_frame(boundary+1, timeout=60)
            # save-and-quit also leaves finished.txt; run_until_frame can see
            # that stale marker before the resumed process clears it.
            deadline = time.monotonic()+60
            while current.held_frame() is None and current.is_running():
                if time.monotonic()>deadline: raise TimeoutError('resumed hold')
                time.sleep(.05)
            assert current.held_frame() == boundary+1, (
                current.held_frame(), current.get.latest_frame(), current.is_running())
            report['events'].append({'frame': boundary, 'released_row': row,
                                     'checkpoint_sha256': digest,
                                     'strengths': strength.tolist()})
            if boundary == 10: current.run_until_frame(20, timeout=60)
        current.release(blocking=True)
        positions = np.asarray([current.get.vertex(f)[0] for f in range(36)])
        assert np.isfinite(positions).all()
        gaps = np.asarray([[np.linalg.norm(x[int(a)]-x[int(b)])
                            for a,b in indices[:,[0,3]]] for x in positions])
        # Both sides must continue moving with zero springs after final release.
        motion = float(np.linalg.norm(positions[-1]-positions[21]))
        assert motion > 1e-7
        report.update(result='PASS', gaps=gaps.tolist(), positions=positions.tolist(),
                      control=args.keep_stitches, motion_after_release=motion,
                      project_root=current.session.app_root,
                      scope='checkpoint retention and sequential zero-stiffness resume; not full tear certification')
    except Exception as exc:
        report.update(result='FAIL', error=repr(exc))
        raise
    finally:
        if current.is_running(): current.save_and_quit()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
