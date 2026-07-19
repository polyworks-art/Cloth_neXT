# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create the clean English-named Superhive Quick Start scene in Blender."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import bpy


def _arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return parser.parse_args(values)


def _material(name, color, metallic=0.0):
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1.0)
    material.metallic = metallic
    material.roughness = 0.38
    return material


def main():
    args = _arguments()
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # The generator needs only the persistent PropertyGroups. Registering the
    # full UI would start telemetry and Blender handlers in a background build
    # process, which is unnecessary and can keep the process alive at shutdown.
    from cloth_next.blender import object_properties
    for cls in object_properties.CLASSES:
        bpy.utils.register_class(cls)
    object_properties.attach_to_object()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials,
                       bpy.data.cameras, bpy.data.lights):
        for datablock in tuple(datablocks):
            datablocks.remove(datablock)
    for collection in tuple(bpy.data.collections):
        if collection.name != "Collection":
            bpy.data.collections.remove(collection)
    collection = bpy.data.collections.get("Collection")
    collection.name = "Cloth_NeXt_Quick_Start"

    bpy.ops.mesh.primitive_grid_add(x_subdivisions=31, y_subdivisions=31,
                                    size=4.5, location=(0.0, 0.0, 2.8))
    cloth = bpy.context.object
    cloth.name = "Demo_Cloth"
    cloth.data.name = "Demo_Cloth_Mesh"
    cloth.data.materials.append(_material("Demo_Cloth_Fabric", (0.08, 0.3, 0.8)))
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 48

    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                        location=(0.4, 0.0, 1.35), scale=(1.1, 1.1, 1.1))
    collider = bpy.context.object
    collider.name = "Demo_Animated_Collider"
    collider.data.name = "Demo_Animated_Collider_Mesh"
    collider.data.materials.append(_material("Demo_Collider_Material", (0.8, 0.16, 0.08), 0.25))
    collider.cloth_next.enabled = True
    collider.cloth_next.role = "COLLIDER"
    collider.cloth_next.collider_motion = "ANIMATED"
    collider.cloth_next.collider_samples_per_frame = 12
    collider.keyframe_insert("location", frame=1)
    collider.location.x = -0.7
    collider.location.z = 1.8
    collider.keyframe_insert("location", frame=48)

    bpy.ops.mesh.primitive_plane_add(size=12.0, location=(0.0, 0.0, 0.0))
    floor = bpy.context.object
    floor.name = "Demo_Floor_Collider"
    floor.data.name = "Demo_Floor_Mesh"
    floor.data.materials.append(_material("Demo_Floor_Material", (0.07, 0.07, 0.08)))
    floor.cloth_next.enabled = True
    floor.cloth_next.role = "COLLIDER"

    bpy.ops.object.empty_add(type="SINGLE_ARROW", location=(-2.8, 0.0, 2.0))
    wind = bpy.context.object
    wind.name = "Demo_Wind_Force"
    wind.cloth_next.enabled = True
    wind.cloth_next.role = "FORCE"
    wind.cloth_next.force.force_type = "WIND"
    wind.cloth_next.force.strength = 2.0

    scene = bpy.context.scene
    scene.name = "Cloth_NeXt_Quick_Start"
    scene.frame_start = 1
    scene.frame_end = 48
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 600
    scene.render.resolution_percentage = 100
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    print(f"Created {output}")
    object_properties.detach_from_object()
    for cls in reversed(object_properties.CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    main()
