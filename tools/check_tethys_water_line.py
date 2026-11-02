# SPDX-License-Identifier: GPL-3.0-or-later
"""Where does the TETHYS Engine see the water line? (no Blender, CPU only)

    python tools/check_tethys_water_line.py --engine <tethys-engine> --output <new dir>

A vertical sheet hangs half into still water whose surface is at z = 0.
Liquid particles fill the pool and are splatted into a GAIA field with the
add-on's own reconstruction; the pool's surface mesh is a closed box. The
engine bakes the sheet twice, once from the particle support alone and once
from the liquid surface, and reports the height on the sheet where the
engine's contact channel crosses 50 %. The real water line is 0.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.gaia.water_field import grid_dimensions, reconstruct  # noqa: E402
from cloth_next.tethys import contact_export, profiles, protocol  # noqa: E402

FRAMES = 6


def sheet(n=121):
    xs, zs = np.linspace(-0.25, 0.25, n), np.linspace(-0.3, 0.3, n)
    gx, gz = np.meshgrid(xs, zs)
    base = np.stack((gx.ravel(), np.full(n * n, 0.0123), gz.ravel()), axis=1)
    index = np.arange(n * n).reshape(n, n)
    a, b, c, d = index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, 1:].ravel(), index[1:, :-1].ravel()
    triangles = np.empty((2 * len(a), 3), np.int32)
    triangles[0::2] = np.stack((a, b, c), 1)
    triangles[1::2] = np.stack((a, c, d), 1)
    return base, triangles, np.repeat(np.arange(len(a)), 2), len(a)


def pool(top=0.0, half=0.6, bottom=-0.8):
    corners = np.array([[x, y, z] for x in (-half, half) for y in (-half, half) for z in (bottom, top)], float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return corners, np.array([t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))])


def run(engine, folder, use_surface, resolution):
    folder.mkdir(parents=True, exist_ok=True)
    base, triangles, polygon_of, polygons = sheet()
    pc2 = folder / "cloth.pc2"
    with pc2.open("wb") as stream:
        stream.write(struct.pack("<12siiffi", b"POINTCACHE2\0", 1, len(base), 0.0, 1.0, FRAMES))
        for _ in range(FRAMES):
            stream.write(np.ascontiguousarray(base, "<f4").tobytes())
    lo, hi = np.full(3, -1.0), np.full(3, 1.0)
    dims = grid_dimensions(lo, hi, resolution)
    reference = grid_dimensions(lo, hi, 100)
    threshold = float(np.prod((np.asarray(reference) - 1) / (np.asarray(dims) - 1)))
    spacing = float(((hi - lo) / (np.asarray(dims) - 1))[2])
    # Two particles per FLIP cell along each axis, FLIP cell = GAIA cell.
    s = spacing / 2
    rng = np.random.default_rng(7)
    g = np.arange(-0.55, 0.55, s)
    points = np.stack(np.meshgrid(g, g, np.arange(-0.75, 0.0, s) + s / 2, indexing="ij"), -1).reshape(-1, 3)
    points += rng.uniform(-s / 2, s / 2, points.shape)
    points[:, 2] = np.minimum(points[:, 2], -1e-6)
    field = reconstruct(points, np.zeros_like(points), lo, hi, dims, support_threshold=threshold)
    surface = pool() if use_surface else None
    block = contact_export.crop(field, base.min(0), base.max(0), surface)
    env = folder / "water.tethysev"
    protocol.write_env(env, 1, FRAMES, 24.0, (block for _ in range(FRAMES)))
    rows, material_of = profiles.resolve_materials("Check", "COTTON", (), [], [0] * polygons, polygon_of.tolist())
    request = protocol.EngineRequest(
        settings=protocol.EngineSettings(fps=24.0, gravity=(0.0, 0.0, -9.81), debug_channels=True),
        first_frame=1, last_frame=FRAMES, pc2_first_sample=0, vertex_count=len(base), triangles=triangles,
        polygon_of=polygon_of, polygon_count=polygons, material_of=np.asarray(material_of), materials=rows,
        matrix_world=np.eye(4), pc2_path=str(pc2), env_path=str(env), output_path=str(folder / "out.tethys"),
        cancel_path=str(folder / "cancel"), identity="{}")
    path = folder / "request.tethysrq"
    path.write_bytes(protocol.encode_request(request))
    result = subprocess.run([str(engine), "run", str(path)], capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stdout[-2000:] + result.stderr[-2000:])
    with protocol.CacheReader(folder / "out.tethys", verify_all=True) as reader:
        contact = np.asarray(reader.read(FRAMES).channels["contact"], float)
    centre_z = np.round(base[triangles[::2]].mean(axis=1)[:, 2], 6)   # one polygon = two triangles
    heights = np.unique(centre_z)
    rows_mean = np.array([contact[centre_z == z].mean() for z in heights])
    crossing = float(np.interp(0.5, rows_mean[::-1], heights[::-1]))
    return {"source": "liquid surface" if use_surface else "particle support", "grid": int(resolution),
            "cell_cm": spacing * 100, "water_line_cm": crossing * 100, "water_line_cells": crossing / spacing}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for resolution in (64, 100):
        for use_surface in (False, True):
            row = run(args.engine, args.output / f"g{resolution}_{'surface' if use_surface else 'particles'}",
                      use_surface, resolution)
            rows.append(row)
            print(json.dumps(row), flush=True)
    (args.output / "water_line.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
