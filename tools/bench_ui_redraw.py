# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reproducible Cloth NeXt Properties-redraw benchmark (headless, fake bpy).

Drives the real ``Panel.draw()`` bodies of the Solver, Cache, and Pinning
panels against instrumented synthetic meshes and reports what each redraw
actually costs: wall time plus the number of full mesh scans, topology
hashes, pin scans, and settings fingerprints it triggered.

The panels are the production classes — only ``bpy`` and the mesh data are
synthetic — so a regression that puts mesh work back into a draw path shows
up here as a non-zero scan count.

    python tools/bench_ui_redraw.py
    python tools/bench_ui_redraw.py --redraws 100 --sizes 10000 100000 500000
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import fake_bpy, mesh_fixtures  # noqa: E402

CASES = ("A_disabled", "B_enabled", "C_pinning", "D_cached")
DEFAULT_SIZES = (10_000, 100_000, 500_000)
PIN_GROUP = "Pins"


class _Layout:
    """Swallows the draw calls; records nothing but keeps the API honest."""

    def __init__(self):
        self.enabled = True
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = False
        self.alert = False

    def label(self, *_a, **_kw):
        return None

    def operator(self, *_a, **_kw):
        return SimpleNamespace()

    def prop(self, *_a, **_kw):
        return None

    def prop_search(self, *_a, **_kw):
        return None

    def menu(self, *_a, **_kw):
        return None

    def separator(self, *_a, **_kw):
        return None

    def row(self, **_kw):
        return _Layout()

    def column(self, **_kw):
        return _Layout()

    def box(self, **_kw):
        return _Layout()


def _fresh_environment():
    for name in list(sys.modules):
        if name == "bpy" or name.startswith("cloth_next"):
            del sys.modules[name]
    bpy = fake_bpy.make_module()
    sys.modules["bpy"] = bpy
    registration = importlib.import_module("cloth_next.blender.registration")
    registration.register()
    return bpy, registration


def _build_scene(bpy, counters, *, size, case):
    cloth = bpy.types.Object(name="Cloth", type="MESH")
    cloth.data = mesh_fixtures.build_mesh(
        size, counters=counters, name="ClothMesh",
        pinned_fraction=0.25 if case == "C_pinning" else 0.0)
    cloth.animation_data = None
    cloth.matrix_world = tuple(tuple(1.0 if r == c else 0.0 for c in range(4))
                               for r in range(4))
    cloth.vertex_groups = mesh_fixtures.VertexGroups(
        (PIN_GROUP,) if case == "C_pinning" else ())

    collider = bpy.types.Object(name="Collider", type="MESH")
    collider.data = mesh_fixtures.build_mesh(64, counters=counters,
                                             name="ColliderMesh")
    collider.animation_data = None
    collider.constraints = ()
    collider.vertex_groups = mesh_fixtures.VertexGroups()

    for obj in (cloth, collider):
        obj.cloth_next.enabled = case != "A_disabled"
    cloth.cloth_next.role = "CLOTH"
    collider.cloth_next.role = "COLLIDER"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 24

    if case == "C_pinning":
        cloth.cloth_next.pinning_enabled = True
        cloth.cloth_next.pin_group = PIN_GROUP

    if case == "D_cached":
        modifier = cloth.modifiers.new("Cloth NeXt Test Cache", "MESH_CACHE")
        modifier.filepath = "/fake/cache/cn_test_cloth_bench.pc2"
        modifier.cloth_next_owner = "cloth_next_playback_v1"
        cloth.cloth_next_cache_path = modifier.filepath
        cloth.cloth_next.baked_settings_fingerprint = "seeded-by-benchmark"

    return cloth, collider


def _context(bpy, cloth, collider):
    prefs = SimpleNamespace(auto_launch_bake_window=True,
                            telemetry_refresh_seconds=1.0,
                            external_solver_path="", developer_tools=False)
    scene = SimpleNamespace(objects=[cloth, collider], frame_start=1,
                            frame_end=24, frame_current=1,
                            render=SimpleNamespace(fps=24),
                            gravity=(0.0, 0.0, -9.81), use_gravity=True,
                            cloth_next_quality=None)
    return SimpleNamespace(
        object=cloth, active_object=cloth, scene=scene,
        preferences=SimpleNamespace(
            addons={"cloth_next": SimpleNamespace(preferences=prefs)}))


def _panels(physics_ui):
    return (("solver", physics_ui.CLOTHNEXT_PT_solver),
            ("cache", physics_ui.CLOTHNEXT_PT_cache),
            ("pinning", physics_ui.CLOTHNEXT_PT_pinning))


def _instrument(solver_test, counters):
    """Count the expensive calls wherever they still live."""
    for attribute, counter in (("_snapshot_static_pin", "pin_scans"),
                               ("current_settings_fingerprint",
                                "settings_fingerprints"),
                               ("mesh_topology_signature", "topology_hashes")):
        original = getattr(solver_test, attribute, None)
        if original is None:
            continue

        def wrapper(*args, _o=original, _c=counter, **kwargs):
            setattr(counters, _c, getattr(counters, _c) + 1)
            return _o(*args, **kwargs)

        setattr(solver_test, attribute, wrapper)


def run_case(case: str, size: int, redraws: int) -> dict:
    bpy, registration = _fresh_environment()
    physics_ui = sys.modules["cloth_next.blender.physics_ui"]
    solver_test = sys.modules["cloth_next.blender.solver_test"]

    counters = mesh_fixtures.MeshCounters()
    _instrument(solver_test, counters)

    # A configured solver keeps the benchmark on the real draw path instead of
    # short-circuiting on "not configured".
    physics_ui._solver_status = lambda _c: physics_ui._SolverStatus(
        True, "Ready · Protocol 0.11", ("Schema 1",))

    cloth, collider = _build_scene(bpy, counters, size=size, case=case)
    context = _context(bpy, cloth, collider)

    panels = _panels(physics_ui)
    if case == "A_disabled":
        panels = ()  # poll() is False; Blender never calls draw()

    for _name, panel_cls in panels:  # warm import caches, exclude from timing
        panel = panel_cls()
        panel.layout = _Layout()
        panel.draw(context)

    counters.reset()
    durations: dict[str, list[float]] = {name: [] for name, _ in panels}
    tracemalloc.start()
    start = time.perf_counter()
    for _ in range(redraws):
        for name, panel_cls in panels:
            panel = panel_cls()
            panel.layout = _Layout()
            begin = time.perf_counter()
            panel.draw(context)
            durations[name].append(time.perf_counter() - begin)
    total = time.perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    per_redraw = [sum(values[i] for values in durations.values())
                  for i in range(redraws)] if durations else [0.0] * redraws
    registration.unregister()

    return {
        "case": case,
        "vertices": size,
        "redraws": redraws,
        "total_seconds": total,
        "avg_redraw_ms": (sum(per_redraw) / len(per_redraw)) * 1000,
        "max_redraw_ms": max(per_redraw) * 1000 if per_redraw else 0.0,
        "panels_ms": {name: {
            "avg": (sum(values) / len(values)) * 1000 if values else 0.0,
            "max": max(values) * 1000 if values else 0.0}
            for name, values in durations.items()},
        "peak_python_kib": peak / 1024,
        **counters.snapshot(),
        "full_mesh_scans": counters.full_mesh_scans,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redraws", type=int, default=100)
    parser.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES)
    parser.add_argument("--cases", nargs="+", default=CASES, choices=CASES)
    parser.add_argument("--json", type=Path,
                        help="also write the raw results to this file")
    args = parser.parse_args(argv)

    results = []
    header = (f"{'case':<12}{'verts':>9}{'avg ms':>10}{'max ms':>10}"
              f"{'scans':>9}{'topo':>7}{'pin':>6}{'fp':>6}{'peak KiB':>11}")
    print(header)
    print("-" * len(header))
    for size in args.sizes:
        for case in args.cases:
            result = run_case(case, size, args.redraws)
            results.append(result)
            print(f"{result['case']:<12}{result['vertices']:>9}"
                  f"{result['avg_redraw_ms']:>10.3f}"
                  f"{result['max_redraw_ms']:>10.3f}"
                  f"{result['full_mesh_scans']:>9}"
                  f"{result['topology_hashes']:>7}"
                  f"{result['pin_scans']:>6}"
                  f"{result['settings_fingerprints']:>6}"
                  f"{result['peak_python_kib']:>11.1f}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
