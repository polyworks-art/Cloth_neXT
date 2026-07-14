# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Real-Blender Properties-redraw performance check (run inside Blender).

Reproduces the reported workflow on genuinely complex meshes — real ``bpy``
data, a real depsgraph, real vertex groups, real property update callbacks —
and measures what a Properties redraw actually costs after Cloth NeXt is
enabled.

    blender --background --factory-startup --python tools/blender_ui_perf_check.py

Exits non-zero if a redraw triggers mesh work, or if draw time scales with
mesh size. Headless by design: it drives the production ``Panel.draw()``
bodies against a recording layout, which is the same code Blender's own draw
pipeline calls.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SIZES = (10_000, 100_000, 500_000)
REDRAWS = 100
PIN_GROUP = "Pins"


def _sizes_from_argv():
    """Sizes after a lone ``--`` separator, e.g. `-- 10000 100000`."""
    if "--" not in sys.argv:
        return SIZES
    values = sys.argv[sys.argv.index("--") + 1:]
    return tuple(int(value) for value in values) or SIZES


class RecordingLayout:
    """Stands in for Blender's UILayout; the draw() bodies are the real ones."""

    def __init__(self):
        self.enabled = True
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = False
        self.alert = False
        self.labels = []

    def label(self, text="", **_kw):
        self.labels.append(text)

    def operator(self, *_a, **_kw):
        return type("Op", (), {"role": "", "tooltip": ""})()

    def prop(self, *_a, **_kw):
        pass

    def prop_search(self, *_a, **_kw):
        pass

    def menu(self, *_a, **_kw):
        pass

    def separator(self, *_a, **_kw):
        pass

    def row(self, **_kw):
        return self

    def column(self, **_kw):
        return self

    def box(self, **_kw):
        return self


class Counters:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pin_scans = 0
        self.topology_hashes = 0
        self.validations = 0


def instrument(solver_test, counters):
    for attribute, name in (("_snapshot_static_pin", "pin_scans"),
                            ("mesh_topology_signature", "topology_hashes"),
                            ("validate_scene", "validations")):
        original = getattr(solver_test, attribute)

        def wrapper(*args, _o=original, _n=name, **kwargs):
            setattr(counters, _n, getattr(counters, _n) + 1)
            return _o(*args, **kwargs)

        setattr(solver_test, attribute, wrapper)


def build_grid(name: str, vertex_count: int):
    """A real Blender grid mesh with roughly ``vertex_count`` vertices.

    Built with ``from_pydata`` rather than ``bpy.ops.mesh.primitive_grid_add``:
    the operator goes through the full edit-mesh/undo machinery and takes
    minutes at half a million vertices.
    """
    side = max(2, int(vertex_count ** 0.5))
    step = 2.0 / (side - 1)
    vertices = [(-1.0 + x * step, -1.0 + y * step, 0.0)
                for x in range(side) for y in range(side)]
    faces = [(x * side + y, (x + 1) * side + y,
              (x + 1) * side + y + 1, x * side + y + 1)
             for x in range(side - 1) for y in range(side - 1)]
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_scene(vertex_count: int):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    cloth = build_grid("Cloth", vertex_count)
    collider = build_grid("Collider", 64)
    collider.location = (0.0, 0.0, -2.0)
    bpy.context.view_layer.objects.active = cloth

    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    collider.cloth_next.enabled = True
    collider.cloth_next.role = "COLLIDER"

    group = cloth.vertex_groups.new(name=PIN_GROUP)
    quarter = len(cloth.data.vertices) // 4
    group.add(list(range(quarter)), 1.0, "REPLACE")
    return cloth, collider


def panels(physics_ui):
    return (physics_ui.CLOTHNEXT_PT_physics, physics_ui.CLOTHNEXT_PT_overview,
            physics_ui.CLOTHNEXT_PT_solver, physics_ui.CLOTHNEXT_PT_material,
            physics_ui.CLOTHNEXT_PT_pinning, physics_ui.CLOTHNEXT_PT_damping,
            physics_ui.CLOTHNEXT_PT_collisions, physics_ui.CLOTHNEXT_PT_cache,
            physics_ui.CLOTHNEXT_PT_advanced)


class _PanelSelf:
    """A stand-in ``self`` for a Panel.draw call.

    A registered ``bpy.types.Panel`` subclass is an RNA struct and cannot be
    instantiated from Python (``bpy_struct.__new__`` rejects it). ``draw`` is
    still an ordinary Python function on the class, so calling it with a stub
    that provides ``layout`` runs the exact production draw body — the same
    code Blender's own draw pipeline invokes.
    """

    def __init__(self):
        self.layout = RecordingLayout()


def redraw_once(physics_ui, context):
    for panel_cls in panels(physics_ui):
        if not panel_cls.poll(context):
            continue
        panel_cls.draw(_PanelSelf(), context)


def measure(physics_ui, context, counters, redraws=REDRAWS):
    counters.reset()
    durations = []
    for _ in range(redraws):
        start = time.perf_counter()
        redraw_once(physics_ui, context)
        durations.append(time.perf_counter() - start)
    return {
        "avg_ms": sum(durations) / len(durations) * 1000,
        "max_ms": max(durations) * 1000,
        "pin_scans": counters.pin_scans,
        "topology_hashes": counters.topology_hashes,
        "validations": counters.validations,
    }


def main() -> int:
    from cloth_next.blender import physics_ui, registration, solver_test
    from cloth_next.blender import validation_state

    # The debounced background validation would otherwise fire mid-measurement
    # and blur exactly what this check is trying to isolate.
    validation_state.set_auto_validate(False)

    registration.register()
    counters = Counters()
    instrument(solver_test, counters)

    context = bpy.context
    failures = []
    results = {}

    for vertex_count in _sizes_from_argv():
        cloth, _collider = make_scene(vertex_count)
        actual = len(cloth.data.vertices)

        # Pinning ON, as in the reported workflow.
        cloth.cloth_next.pinning_enabled = True
        cloth.cloth_next.pin_group = PIN_GROUP

        result = measure(physics_ui, context, counters)
        results[actual] = result
        print(f"[{actual:>7} verts] {REDRAWS} redraws: "
              f"avg {result['avg_ms']:.3f} ms  max {result['max_ms']:.3f} ms  "
              f"pin_scans={result['pin_scans']} "
              f"topology_hashes={result['topology_hashes']}")

        if result["pin_scans"] or result["topology_hashes"]:
            failures.append(f"{actual} verts: redraws did mesh work "
                            f"({result['pin_scans']} pin scans, "
                            f"{result['topology_hashes']} topology hashes)")

        # Timeline scrub + object move + material edit: all must stay cheap and
        # must not trigger a scan, only a dirty mark.
        counters.reset()
        for frame in range(1, 25):
            context.scene.frame_set(frame)
        cloth.location.x += 0.5
        cloth.cloth_next.material.bend_resistance = 12.0
        bpy.context.view_layer.update()
        redraw_once(physics_ui, context)
        if counters.pin_scans or counters.topology_hashes:
            failures.append(f"{actual} verts: scrub/move/edit triggered mesh work")
        state = validation_state.record_for(cloth).state
        if state is not validation_state.ValidationState.DIRTY:
            failures.append(f"{actual} verts: edits did not mark DIRTY (got {state})")

        # The one place a full scan is allowed: an explicit validation.
        counters.reset()
        started = time.perf_counter()
        snapshot = solver_test.validate_scene(context)
        elapsed = (time.perf_counter() - started) * 1000
        print(f"          full bake validation: {elapsed:.1f} ms  "
              f"pinned={len(snapshot.pin_membership.vertex_indices)}  "
              f"(1 topology hash, 1 pin scan)")
        if counters.topology_hashes != 1 or counters.pin_scans != 1:
            failures.append(f"{actual} verts: validation was not single-pass "
                            f"({counters.topology_hashes} hashes, "
                            f"{counters.pin_scans} pin scans)")
        if validation_state.record_for(cloth).state is not \
                validation_state.ValidationState.VALID:
            failures.append(f"{actual} verts: validation did not record VALID")

        # A validated cache must be reported honestly after a real mesh edit.
        counters.reset()
        cloth.data.vertices[0].co.z += 1.0   # a real depsgraph geometry update
        bpy.context.view_layer.update()
        redraw_once(physics_ui, context)
        if counters.pin_scans or counters.topology_hashes:
            failures.append(f"{actual} verts: depsgraph update triggered mesh work")

    # Draw cost must not scale with mesh size.
    sizes = sorted(results)
    smallest, largest = results[sizes[0]]["avg_ms"], results[sizes[-1]]["avg_ms"]
    growth = largest / smallest if smallest else float("inf")
    print(f"\ndraw-time growth {sizes[0]} -> {sizes[-1]} verts: {growth:.2f}x")
    if growth > 3.0:
        failures.append(f"draw time grew {growth:.1f}x with mesh size")

    # Disable and re-enable, as an artist would after an update.
    registration.unregister()
    if validation_state.handler_count() != 0:
        failures.append("handlers survived unregister")
    registration.register()
    registration.unregister()
    registration.register()
    if validation_state.handler_count() != 4:
        failures.append(f"reload produced {validation_state.handler_count()} "
                        "handlers (expected 4)")
    registration.unregister()
    print("add-on disable/re-enable cycle: handlers clean")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nBlender UI performance check PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
