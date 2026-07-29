# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender regression for deforming Collider Scene cache identity.

The same topology is captured twice with a changed deformation curve. Both the
sampled frame and the persistent motion digest must change, proving that a
Frame-1 Scene payload cannot be reused for a different Collider animation.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.bake.frame_range import BakeFrameRange  # noqa: E402
from cloth_next.blender import registration, solver_test  # noqa: E402


def _capture(source, bake_range):
    capture = solver_test._capture_collider_motion(
        bpy.context, source, bake_range)
    try:
        assert capture.motion_type == "DEFORMING_ANIMATED"
        digest = capture.content_digest
        assert digest
        last = np.asarray(capture.animation["vert_frames"][-1]).copy()
        return digest, last
    finally:
        capture.cleanup()


def main():
    print("[animated-collider-cache] register", flush=True)
    registration.register()
    try:
        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = 3
        scene.frame_set(1)

        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=8, y_subdivisions=8,
            location=(-4.0, 0.0, 0.0))
        source = bpy.context.object
        source.name = "AnimatedColliderDigestSmoke"
        source.cloth_next.enabled = True
        source.cloth_next.role = "COLLIDER"
        source.cloth_next.collider_motion = "ANIMATED"
        source.cloth_next.collider_capture_mode = "DEFORMING"
        source.cloth_next.collider_samples_per_frame = 2

        source.shape_key_add(name="Basis")
        deform = source.shape_key_add(name="Deform")
        # Guaranteed local-space deformation with unchanged topology and world
        # transform, matching an Armature/shape-key character Collider.
        for point in deform.data:
            point.co.z += 0.25 * (1.0 + float(point.co.x))
        deform.value = 0.0
        deform.keyframe_insert("value", frame=1)
        deform.value = 1.0
        deform.keyframe_insert("value", frame=3)

        bake_range = BakeFrameRange(1, 3)
        print("[animated-collider-cache] capture A", flush=True)
        first_digest, first_last = _capture(source, bake_range)

        deform.value = 0.45
        deform.keyframe_insert("value", frame=3)
        print("[animated-collider-cache] capture B", flush=True)
        second_digest, second_last = _capture(source, bake_range)

        assert second_digest != first_digest
        assert not np.allclose(second_last, first_last)
        print(json.dumps({
            "motion_type": "DEFORMING_ANIMATED",
            "digest_changed": True,
            "frame_changed": True,
        }), flush=True)
    finally:
        print("[animated-collider-cache] unregister", flush=True)
        registration.unregister()


if __name__ == "__main__":
    main()
