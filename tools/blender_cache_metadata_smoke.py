# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender + PPF smoke for Phase-4 cache publication and attachment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.bake import cache_metadata  # noqa: E402
from cloth_next.blender import physics_ui, registration, solver_test  # noqa: E402


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def main() -> None:
    args = _arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    registration.register()
    try:
        scene = bpy.context.scene
        scene.render.fps = 24
        scene.frame_start = 1
        scene.frame_end = 3

        bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3,
                                       size=2.0, location=(0, 0, 1.0))
        cloth = bpy.context.object
        cloth.name = "Phase4Cloth"
        bpy.ops.clothnext.add_physics()
        cloth.cloth_next.role = "CLOTH"
        cloth.cloth_next.bake_start = 1
        cloth.cloth_next.bake_end = 3
        cloth.cloth_next.cache_directory = str(args.output)

        bpy.ops.mesh.primitive_plane_add(size=8.0, location=(0, 0, 0))
        collider = bpy.context.object
        collider.name = "Phase4Floor"
        bpy.ops.clothnext.add_physics()
        collider.cloth_next.role = "COLLIDER"

        bpy.context.view_layer.objects.active = cloth
        cloth.select_set(True)
        collider.select_set(False)
        plan = solver_test.build_run_plan(bpy.context)
        solver_test._cancel_event.clear()
        solver_test._run_started_at = time.monotonic()
        solver_test._worker_main(plan)

        messages = []
        while not solver_test._queue.empty():
            messages.append(solver_test._queue.get_nowait())
        terminal = next((message for message in reversed(messages)
                         if message[0] in {"finished", "error", "cancelled"}),
                        None)
        if terminal is None or terminal[0] != "finished":
            raise RuntimeError(f"Phase-4 worker failed: {terminal!r}")
        header = terminal[1]
        inspection = cache_metadata.inspect_cache(
            plan.pc2_path,
            settings_fingerprint=plan.settings_fingerprint,
            geometry_fingerprint=plan.geometry_fingerprint)
        assert inspection.condition is cache_metadata.CacheCondition.READY
        solver_test._attach_playback(plan, header)
        assert physics_ui._cache_state(bpy.context) == ("MATCHING",
                                                        "Cache ready")
        print(json.dumps({
            "result": "PASS",
            "cache": str(plan.pc2_path),
            "condition": inspection.condition.value,
            "frames": header.frame_count,
            "vertices": header.vertex_count,
            "cache_sha256": inspection.metadata["cache_sha256"],
            "metadata_digest": inspection.metadata["metadata_digest"],
        }, indent=2))
    finally:
        solver_test.shutdown()
        registration.unregister()


if __name__ == "__main__":
    main()
