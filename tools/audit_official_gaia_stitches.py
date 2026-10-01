"""Isolated official-frontend experiment, not a production integration.

Run with the verified upstream bundle's Python, from that bundle's root.
Only public frontend APIs and documented exported files are used. No source
patches, private-state access, pins, mesh splitting, or weight scaling.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

import cbor2
import numpy as np
from frontend import App


def read_rows(session):
    directory = Path(session.info.path) / "bin"
    indices = np.fromfile(directory / "stitch_ind.bin", dtype=np.uint64).reshape(-1, 6)
    weights = np.fromfile(directory / "stitch_w.bin", dtype=np.float32).reshape(-1, 6)
    strengths = np.fromfile(directory / "stitch_stiffness.bin", dtype=np.float32)
    mapping = cbor2.loads((Path(session.info.path) / "map.pickle").read_bytes())
    assert mapping["kind"] == "VertexMap" and mapping["version"] == 2
    assert len(mapping["payload"]) == 1, "experiment must retain one simulated object"
    assert indices.shape == weights.shape == (4, 6)
    assert strengths.shape == (4,)
    return indices, weights, strengths


def separations(vertices, indices):
    return np.array([np.linalg.norm(vertices[row[0]] - vertices[row[3]])
                     for row in indices], dtype=float)


def run_bake(session, indices, expected):
    _, _, actual = read_rows(session)
    np.testing.assert_array_equal(actual, expected)
    session.run(blocking=True)
    assert not session.is_running(), "solver must have exited"
    assert session.get.latest_frame() >= 30
    gaps = []
    for frame in range(31):
        result = session.get.vertex(frame)
        assert result is not None and result[1] == frame
        assert np.isfinite(result[0]).all()
        gaps.append(separations(result[0], indices))
    gaps = np.asarray(gaps)
    low = gaps[:, :2].mean(axis=1)
    high = gaps[:, 2:].mean(axis=1)
    assert low[-1] < low[0] and high[-1] < high[0], "both seams must close"
    assert np.max(low - high) > 0.005, "independent strength must affect motion"
    return {"frames": len(gaps), "initial": gaps[0].tolist(),
            "final": gaps[-1].tolist(), "maximum_low_minus_high": float(np.max(low-high)),
            "gaps": gaps.tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    App.set_backend("cuda")
    name = "cnx-stitch-contract-" + uuid.uuid4().hex
    app = App.create(name)
    vertices, triangles = [], []
    # Four disconnected panels authored as ONE mesh object, two symmetric
    # intra-seams. No object decomposition or topology edits are performed.
    for height in (0.0, 1.0):
        for left in (0.0, 0.4):
            base = len(vertices)
            vertices.extend([[left, 0, height], [left+0.2, 0, height],
                             [left+0.2, 0, height+0.2], [left, 0, height+0.2]])
            triangles.extend([[base, base+1, base+2], [base, base+2, base+3]])
    app.asset.add.tri("cloth", np.asarray(vertices, dtype=float),
                      np.asarray(triangles, dtype=np.int64))
    local_indices = np.asarray([[a, a, a, b, b, b]
                                for a, b in ((1, 4), (2, 7), (9, 12), (10, 15))])
    weights = np.asarray([[1, 0, 0, 1, 0, 0]] * 4, dtype=float)
    app.asset.add.stitch("two-intra-seams", (local_indices, weights))
    scene = app.scene.create("cloth-scene")
    cloth = scene.add("cloth")
    # Keep the two panels inertially slow enough to resolve the two spring
    # responses over several frames rather than both snapping shut immediately.
    cloth.param.set("density", 1000.0)
    cloth.param.set("stitch-stiffness", 1.0)
    cloth.stitch("two-intra-seams")
    fixed = scene.build()
    session = app.session.create(fixed, "two-seams")
    session.param.set("gravity", [0.0, 0.0, 0.0])
    session.param.set("frames", 30).set("fps", 1000.0).set("dt", 0.0001)
    initial_session = session.build()
    indices, original_weights, original_stiffness = read_rows(initial_session)
    np.testing.assert_array_equal(original_stiffness, np.ones(4))
    # Public documented per-row setter. Normalized endpoint weights stay intact.
    expected = np.asarray([0.2*500, 0.2*500, 0.9*500, 0.9*500], dtype=np.float32)
    fixed.set_stitch(indices, original_weights, stiffness=expected)
    first = session.build()
    app.save()
    first_result = run_bake(first, indices, expected)
    # Reload the saved graph through the official public API and rebake it.
    reloaded = App.load(name)
    second = reloaded.session.select("two-seams").build()
    second_indices, second_weights, second_strengths = read_rows(second)
    np.testing.assert_array_equal(second_indices, indices)
    np.testing.assert_array_equal(second_weights, original_weights)
    np.testing.assert_array_equal(second_strengths, expected)
    second_result = run_bake(second, indices, expected)
    report = {"result": "PASS", "scope": "public API prototype, not TCMD integration",
              "backend": App.get_backend(), "project": name,
              "project_root": first.session.app_root,
              "stiffness": expected.tolist(), "first_bake": first_result,
              "reload_rebake": second_result}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("first_bake", "reload_rebake")}), flush=True)


if __name__ == "__main__":
    main()
