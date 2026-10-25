# SPDX-License-Identifier: GPL-3.0-or-later
"""Benchmark the bundled TETHYS Engine on deforming sheets (no Blender).

    python tools/bench_tethys.py --engine <path> --output <dir> [--frames 48]

Sheets of ~5k, 20k and 50k vertices wave and stretch while a water level
rises over them and recedes. Each size runs with four Material Zones and
with one material, to measure the interface overhead. Reports engine time,
time per frame, peak engine memory, cache bytes per frame and input sizes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.tethys import profiles, protocol  # noqa: E402


def sheet(n):
    xs = np.linspace(-1.0, 1.0, n)
    gx, gy = np.meshgrid(xs, xs)
    base = np.stack((gx.ravel(), gy.ravel(), np.zeros(n * n)), axis=1)
    index = np.arange(n * n).reshape(n, n)
    a, b, c, d = index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, 1:].ravel(), index[1:, :-1].ravel()
    triangles = np.empty((2 * len(a), 3), np.int32)
    triangles[0::2] = np.stack((a, b, c), 1)
    triangles[1::2] = np.stack((a, c, d), 1)
    polygon_of = np.repeat(np.arange(len(a)), 2)
    uv = (base[:, :2] * 0.5 + 0.5)[triangles].reshape(-1, 6)
    return base, triangles, polygon_of, len(a), uv


def frames(base, count):
    for k in range(count):
        t = k / 24.0
        p = base.copy()
        p[:, 2] = 0.15 * np.sin(3 * p[:, 0] + 4 * t) * np.cos(2 * p[:, 1]) + 0.4 * p[:, 1]  # tilted, waving
        p[:, 0] *= 1.0 + 0.05 * np.sin(2 * t)                                              # stretching
        yield p


def run(engine, folder, n, zones, frame_count):
    folder.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    base, triangles, polygon_of, polygons, uv = sheet(n)
    pc2 = folder / "cloth.pc2"
    with pc2.open("wb") as stream:
        stream.write(struct.pack("<12siiffi", b"POINTCACHE2\0", 1, len(base), 0.0, 1.0, frame_count))
        for p in frames(base, frame_count):
            stream.write(np.ascontiguousarray(p, "<f4").tobytes())
    stripe = np.minimum(3, ((base[:, 0][triangles[::2, 0]] + 1.0) * 2).astype(int)) if zones else None
    owners = (stripe + 0).tolist() if zones else [0] * polygons
    zone_list = [profiles.ZoneWetness(1, "Nylon", True, "WATERPROOF", ()),
                 profiles.ZoneWetness(2, "Leather", True, "LEATHER", ()),
                 profiles.ZoneWetness(3, "Lining", True, "ABSORBENT_LINING", ())] if zones else []
    rows, material_of = profiles.resolve_materials("Bench", "COTTON", (), zone_list, owners, polygon_of.tolist())

    def blocks():
        z = np.linspace(-1.0, 1.0, 41)
        for k in range(frame_count):
            level = -1.0 + 1.6 * min(1.0, k / (frame_count * 0.4)) if k < frame_count * 0.6 else -2.0
            influence = np.broadcast_to((z < level)[:, None, None], (41, 41, 41)).astype(np.float32)
            yield protocol.WaterBlock((-1.2, -1.2, -1.0), (0.06, 0.06, 0.05), influence,
                                      np.zeros((41, 41, 41, 3), np.float32))

    env = folder / "water.tethysev"
    protocol.write_env(env, 1, frame_count, 24.0, blocks())
    request = protocol.EngineRequest(
        settings=protocol.EngineSettings(fps=24.0, gravity=(0.0, 0.0, -9.81), evaporation=100.0),
        first_frame=1, last_frame=frame_count, pc2_first_sample=0, vertex_count=len(base), triangles=triangles,
        polygon_of=polygon_of, polygon_count=polygons, material_of=np.asarray(material_of), materials=rows,
        matrix_world=np.eye(4), pc2_path=str(pc2), env_path=str(env), output_path=str(folder / "out.tethys"),
        cancel_path=str(folder / "cancel"), identity="{}", uv=uv)
    path = folder / "request.tethysrq"
    path.write_bytes(protocol.encode_request(request))
    prepared = time.perf_counter() - started
    wall = time.perf_counter()
    result = subprocess.run([str(engine), "run", str(path)], capture_output=True, text=True, timeout=3600)
    wall = time.perf_counter() - wall
    events = [json.loads(line[7:]) for line in result.stdout.splitlines() if line.startswith("TETHYS ")]
    complete = next((e for e in events if e["event"] == "complete"), None)
    start = next((e for e in events if e["event"] == "start"), {})
    if complete is None:
        raise RuntimeError(events[-1] if events else result.stderr)
    cache_bytes = (folder / "out.tethys").stat().st_size
    return {"vertices": len(base), "triangles": len(triangles), "zones": bool(zones),
            "interface_links": start.get("interface_links"), "frames": frame_count,
            "engine_seconds": complete["seconds"], "wall_seconds": wall,
            "ms_per_frame": complete["seconds"] / (frame_count - 1) * 1000.0,
            "substeps": complete["substeps"], "limited_substeps": complete["limited_substeps"],
            "peak_memory_mib": complete["peak_memory"] / 2 ** 20,
            "cache_bytes_per_frame": cache_bytes / frame_count, "request_bytes": path.stat().st_size,
            "water_input_bytes": env.stat().st_size, "python_prepare_seconds": prepared,
            "residual": complete["residual"], "injected": complete["injected"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=48)
    args = parser.parse_args()
    results = []
    for n in (71, 141, 224):            # ~5k, 20k, 50k vertices
        for zones in (True, False):
            row = run(args.engine, args.output / f"n{n}_{'zones' if zones else 'single'}", n, zones, args.frames)
            results.append(row)
            print(json.dumps(row), flush=True)
    (args.output / "bench.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
