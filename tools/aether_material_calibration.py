# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured reference cases for the Cloth NeXt -> AETHER Engine material mapping.

    set CLOTH_NEXT_AETHER_ENGINE=<path to AETHER Engine.exe>
    python tools/aether_material_calibration.py [--out report.json]

1. Hanging strip (membrane): a strip pinned along its top edge stretches
   under its own weight by delta = g L^2 / (2 * stretch_resistance) once the
   mapping undoes PPF's density normalization (E = stretch x weight).
2. Cantilever (bending): a strip clamped horizontally (two pinned columns)
   deflects at the tip by delta = rho g L^4 / (8 B) with the mapped bending
   stiffness B (small-deflection Euler-Bernoulli per unit width).

Both run through the real engine process (Fitting, 24 fps, settled with air
damping) and report simulated / analytic. Ratios near 1 confirm the mapping;
a systematic factor is reported, never hidden.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.aether_engine import client, materials  # noqa: E402
from cloth_next.materials.models import ShellMaterialSettings  # noqa: E402

G = 9.81


def grid(nx, ny, sx, sy, plane="xz", origin=(0.0, 0.0, 0.0)):
    pts = []
    for j in range(ny + 1):
        for i in range(nx + 1):
            u, v = sx * i / nx, sy * j / ny
            p = (origin[0] + u, origin[1], origin[2] - v) if plane == "xz" else (origin[0] + u, origin[1] + v,
                                                                                 origin[2])
            pts.append(p)
    tris = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            tris += [a, a + 1, a + nx + 2, a, a + nx + 2, a + nx + 1]
    return np.asarray(pts, np.float32), np.asarray(tris, np.int32)


def settle(exe, positions, tris, material, pins, frames=144):
    e = client.EngineProcess.start(exe)
    try:
        e.hello()
        s = e.create_session(mode="interactive", quality="fitting", fps=24.0, air_damping=2.0, self_collision=False,
                             cloths=[{"positions": positions, "triangles": tris, "material": material,
                                      "pins": np.asarray(pins, np.int32)}])
        e.request("step", {"count": frames})
        e.wait_event("frame", 600, lambda ev: ev["frame"] == frames)
        x, _v, _f = e.get_state()
        return x
    finally:
        e.shutdown()


def hanging_strip(exe, stretch, weight=0.2):
    L, w, nx, ny = 0.5, 0.05, 4, 40
    m = materials.map_material(ShellMaterialSettings(surface_weight=weight, stretch_resistance=stretch,
                                                     bend_resistance=0.01, sideways_response=0.0,
                                                     collision_gap=0.002))
    pos, tris = grid(nx, ny, w, L, "xz", (0.0, 0.0, 1.0))
    pins = list(range(nx + 1))
    x = settle(exe, pos, tris, m.as_protocol(), pins)
    bottom = slice(ny * (nx + 1), (ny + 1) * (nx + 1))
    simulated = float(pos[bottom, 2].mean() - x[bottom, 2].mean())
    analytic = G * L * L / (2.0 * stretch)
    return {"case": "hanging strip", "stretch_resistance": stretch, "surface_weight": weight,
            "membrane_N_per_m": m.youngs, "elongation_simulated_mm": simulated * 1e3,
            "elongation_analytic_mm": analytic * 1e3, "ratio": simulated / analytic}


def cantilever(exe, bend, weight=0.2, L=0.05):
    nx, ny, w = 20, 2, 0.02
    m = materials.map_material(ShellMaterialSettings(surface_weight=weight, stretch_resistance=20000.0,
                                                     bend_resistance=bend, sideways_response=0.0,
                                                     collision_gap=0.002))
    dx = L / nx
    pos, tris = grid(nx + 1, ny, L + dx, w, "xy", (-dx, 0.0, 1.0))
    pins = [j * (nx + 2) + i for j in range(ny + 1) for i in (0, 1)]   # clamp: two columns
    x = settle(exe, pos, tris, m.as_protocol(), pins)
    tip = [j * (nx + 2) + nx + 1 for j in range(ny + 1)]
    simulated = float(pos[tip, 2].mean() - x[tip, 2].mean())
    analytic = weight * G * L ** 4 / (8.0 * materials.physical_bending(
        ShellMaterialSettings(surface_weight=weight, bend_resistance=bend)))
    return {"case": "cantilever", "bend_resistance": bend, "surface_weight": weight, "length_m": L,
            "bending_N_m": m.bend, "physical_bending_N_m": materials.physical_bending(
                ShellMaterialSettings(surface_weight=weight, bend_resistance=bend)), "tip_deflection_simulated_mm": simulated * 1e3,
            "tip_deflection_analytic_mm": analytic * 1e3, "ratio": simulated / analytic}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    exe = os.environ.get("CLOTH_NEXT_AETHER_ENGINE")
    if not exe:
        raise SystemExit("set CLOTH_NEXT_AETHER_ENGINE")
    rows = [hanging_strip(exe, s) for s in (250.0, 1000.0, 4000.0)]
    rows += [cantilever(exe, b) for b in (1.0, 4.0, 10.0)]
    for r in rows:
        print(json.dumps(r))
    if a.out:
        a.out.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
