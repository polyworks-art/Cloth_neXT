# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed validation for the generated Superhive Quick Start blend."""
from __future__ import annotations

from pathlib import Path
import sys

import bpy


def main():
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from cloth_next.blender import object_properties
    for cls in object_properties.CLASSES:
        bpy.utils.register_class(cls)
    object_properties.attach_to_object()
    required = {
        "Demo_Cloth": "CLOTH",
        "Demo_Animated_Collider": "COLLIDER",
        "Demo_Floor_Collider": "COLLIDER",
        "Demo_Wind_Force": "FORCE",
    }
    for name, role in required.items():
        obj = bpy.data.objects.get(name)
        assert obj is not None, f"missing demo object {name}"
        assert obj.cloth_next.enabled, f"Cloth NeXt disabled on {name}"
        assert obj.cloth_next.role == role, f"wrong role on {name}"
    animated = bpy.data.objects["Demo_Animated_Collider"].cloth_next
    assert animated.collider_motion == "ANIMATED"
    assert animated.collider_samples_per_frame == 12
    names = [item.name for collection in (
        bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.scenes)
        for item in collection]
    assert not any(name.startswith(("Cube", "Sphere", "Plane", "Grid", "Material"))
                   or ".00" in name for name in names), names
    assert not bpy.data.libraries, "demo must not rely on linked libraries"
    assert bpy.context.scene.render.resolution_x == 1200
    assert bpy.context.scene.render.resolution_y == 600
    print("SUPERHIVE_DEMO_VALID")
    object_properties.detach_from_object()
    for cls in reversed(object_properties.CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    main()
