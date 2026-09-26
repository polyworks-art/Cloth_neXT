# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender regression for idle Cloth NeXt validation work.

Run in Blender's normal event loop (not ``--background``) so ``bpy.app.timers``
and depsgraph handlers execute with production scheduling semantics::

    blender --factory-startup --python tools/blender_idle_validation_regression.py

The process closes itself after the observation window.  Assignment and the
resulting cheap DIRTY transition happen before counters are reset; during the
measured idle period no authoritative validation or mesh scan is allowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IDLE_SECONDS = 3.0
SIDE = 260  # 67,600 vertices: large enough to expose a main-thread hitch.


def _grid(name: str, side: int):
    vertices = [(float(x), float(y), 0.0)
                for x in range(side) for y in range(side)]
    faces = [(x * side + y, (x + 1) * side + y,
              (x + 1) * side + y + 1, x * side + y + 1)
             for x in range(side - 1) for y in range(side - 1)]
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _wrap(module, name: str, counters: dict[str, int]):
    original = getattr(module, name)

    def counted(*args, **kwargs):
        counters[name] += 1
        return original(*args, **kwargs)

    for marker in ("_clothnext_validation_handler", "_bpy_persistent"):
        if hasattr(original, marker):
            setattr(counted, marker, getattr(original, marker))
    setattr(module, name, counted)
    return counted


def main() -> None:
    from cloth_next.blender import registration, solver_test, validation_state
    from cloth_next.blender.playback_cache import ensure_simulation_modifier

    bpy.ops.wm.read_factory_settings(use_empty=True)
    counters = {name: 0 for name in (
        "_on_depsgraph_update", "validate_scene", "mesh_topology_signature",
        "_snapshot_static_pin")}
    _wrap(validation_state, "_on_depsgraph_update", counters)
    _wrap(solver_test, "validate_scene", counters)
    _wrap(solver_test, "mesh_topology_signature", counters)
    _wrap(solver_test, "_snapshot_static_pin", counters)

    registration.register()
    cloth = _grid("IdleCloth", SIDE)
    collider = _grid("IdleCollider", 12)
    collider.location.z = -1.0
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    collider.cloth_next.enabled = True
    collider.cloth_next.role = "COLLIDER"
    ensure_simulation_modifier(cloth)
    ensure_simulation_modifier(collider)
    pins = cloth.vertex_groups.new(name="Pins")
    pins.add(list(range(SIDE)), 1.0, "REPLACE")
    cloth.cloth_next.pinning_enabled = True
    cloth.cloth_next.pin_group = pins.name
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    bpy.context.view_layer.update()

    for name in counters:
        counters[name] = 0

    def finish():
        record = validation_state.record_for(cloth)
        result = {
            "idle_seconds": IDLE_SECONDS,
            "vertices": len(cloth.data.vertices),
            "state": record.state.value,
            "validation_timer_present": hasattr(
                validation_state, "_validation_pump"),
            "counts": counters,
        }
        print("CLOTHNEXT_IDLE_VALIDATION=" + json.dumps(result, sort_keys=True),
              flush=True)
        failures = []
        for name in ("validate_scene", "mesh_topology_signature",
                     "_snapshot_static_pin"):
            if counters[name]:
                failures.append(f"{name} ran {counters[name]} time(s)")
        if hasattr(validation_state, "_validation_pump"):
            failures.append("validation timer architecture is still present")
        if record.state.value != "DIRTY":
            failures.append(f"idle assignment state is {record.state.value}, expected DIRTY")
        registration.unregister()
        if failures:
            print("CLOTHNEXT_IDLE_VALIDATION_FAILURE=" + "; ".join(failures),
                  flush=True)
            bpy.context.scene["cloth_next_idle_regression_failed"] = True
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(finish, first_interval=IDLE_SECONDS)


main()
