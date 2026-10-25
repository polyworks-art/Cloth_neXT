# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS showcase: water poured onto a cloth draped over a ramp, half cotton, half waterproof.

    blender -b --factory-startup --python tools/blender_tethys_showcase.py -- \
        --repo <repo> --solver-root <copied GAIA root> --output <new dir> --engine <exe> --account <dir>

Real GAIA cloth bake, native wetness bake with fine surface detail, EEVEE
renders of selected frames plus false-colour strips of the wetness texture.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

import addon_utils
import bpy
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--solver-root", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--engine", type=Path)
parser.add_argument("--account", type=Path)
parser.add_argument("--frames", type=int, default=96)
parser.add_argument("--detail", default="6")
parser.add_argument("--texture", default="2048")
parser.add_argument("--resolution", type=int, default=40)
ARGS = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=False)
os.environ["CLOTH_NEXT_ACCOUNT_ROOT"] = str((ARGS.account or ARGS.output / "account").resolve())
if ARGS.engine:
    os.environ["CLOTH_NEXT_TETHYS_ENGINE"] = str(ARGS.engine.resolve())
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)

from cloth_next.blender import material_zones, solver_test, tethys_bake  # noqa: E402
from cloth_next.gaia.container import write_water_container  # noqa: E402
from cloth_next.gaia.water_field import WaterVelocityField  # noqa: E402
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver  # noqa: E402
from cloth_next.tethys import engine, protocol  # noqa: E402

REPORT = {}
ROOT = ARGS.solver_root.resolve()
RESOLVED = SolverResolver(solver_test._version_probe).resolve(SolverResolutionContext(
    development_executable=ROOT / "target/cuda/release/ppf-cts-server.exe"))
solver_test.resolve_solver = lambda _context: RESOLVED
tethys_bake.availability = lambda refresh=False: engine.Availability(True)

prefs = bpy.context.preferences.addons["cloth_next"].preferences
prefs.auto_launch_bake_window = False
scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 24, 1.0
scene.frame_start, scene.frame_end = 1, ARGS.frames
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

# A cloth hanging from its top edge (pinned), facing the camera.
bpy.ops.mesh.primitive_grid_add(x_subdivisions=ARGS.resolution, y_subdivisions=ARGS.resolution, size=0.8,
                                location=(0.0, 0.0, 0.6))
sheet = bpy.context.object
sheet.name = "WetSheet"
sheet.rotation_euler = (math.radians(90), 0.0, 0.0)
bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
group = sheet.vertex_groups.new(name="Pin")
top = max(v.co.z for v in sheet.data.vertices)
group.add([v.index for v in sheet.data.vertices if v.co.z > top - 1e-4], 1.0, "REPLACE")
bpy.ops.clothnext.add_physics()
sheet.cloth_next.pinning_enabled = True
sheet.cloth_next.pin_group = "Pin"
sheet.cloth_next.bake_start, sheet.cloth_next.bake_end = 1, ARGS.frames
sheet.cloth_next.cache_directory = str(ARGS.output / "cache")
sheet.cloth_next.material.preset = "COTTON"
material = bpy.data.materials.new("Fabric")
material.use_nodes = True
principled = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
principled.inputs["Base Color"].default_value = (0.62, 0.45, 0.3, 1.0)
principled.inputs["Roughness"].default_value = 0.85
sheet.data.materials.append(material)
bpy.ops.clothnext.add_material_zone()
zone = sheet.cloth_next.material_zones[0]
zone.name = "Waterproof"
material_zones.write_owners(sheet, tuple(zone.token if p.center.x > 0.0 else 0 for p in sheet.data.polygons))
zone.wetness_profile = "WATERPROOF"

# Synthetic GAIA water: a stream hits the upper part of the ramp, frames 6-30.
lo, hi, dims = (-0.6, -0.6, -0.2), (0.6, 0.6, 1.0), (49, 49, 49)
x = np.linspace(lo[0], hi[0], dims[0])
y = np.linspace(lo[1], hi[1], dims[1])
z = np.linspace(lo[2], hi[2], dims[2])
Z, Y, X = np.meshgrid(z, y, x, indexing="ij")


def fields():
    for frame in range(1, ARGS.frames + 1):
        pour = 6 <= frame <= 30
        # Upper band of the ramp, across both materials.
        inside = ((np.abs(X) < 0.3) & (np.abs(Y) < 0.08) & (Z > 0.78) & (Z < 0.92)) if pour else np.zeros(X.shape, bool)
        yield frame, (frame - 1) / 24.0, WaterVelocityField(
            lo, hi, np.zeros(X.shape + (3,), np.float32), inside.astype(np.float32))


container = ARGS.output / "water.gaia"
write_water_container(container, {"fps": 24.0, "fingerprint": "showcase-pour",
                                  "provider": {"sampling": "all-liquid-particles"}}, fields())
sheet.cloth_next.water_flow_container = str(container)

started = time.monotonic()
bpy.ops.clothnext.bake("EXEC_DEFAULT")
while solver_test.shared_controller.snapshot().active:
    solver_test._startup_pump()
    solver_test._pump()
    time.sleep(0.02)
REPORT["cloth_bake_seconds"] = time.monotonic() - started
REPORT["cloth"] = solver_test.inspect_attached_cache(sheet).condition.value

s = sheet.cloth_next.tethys
s.enabled = True
s.surface_detail = ARGS.detail
s.texture_resolution = ARGS.texture
s.immersion_film = 0.6
s.debug_channels = True
started = time.monotonic()
message, job = tethys_bake.bake_blocking(bpy.context, sheet)
REPORT["wetness"] = message
REPORT["wetness_seconds"] = time.monotonic() - started
REPORT["engine"] = job.result
print(message, flush=True)

# Camera close on the ramp, light, EEVEE renders.
camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(camera)
camera.location = (0.0, -1.55, 0.6)
camera.rotation_euler = (math.radians(90), 0.0, 0.0)
scene.camera = camera
light = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
light.data.energy = 3.0
scene.collection.objects.link(light)
light.rotation_euler = (math.radians(40), math.radians(-25), math.radians(20))
world = bpy.data.worlds.new("World")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
scene.world = world
engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 960, 640
scene.render.image_settings.file_format = "PNG"
for frame in (5, 15, 30, 45, 60, 90):
    if frame > ARGS.frames:
        continue
    scene.frame_set(frame)
    scene.render.filepath = str(ARGS.output / f"render_{frame:03d}.png")
    bpy.ops.render.render(write_still=True)

# False-colour strips of the wetness texture (R: surface film, G: saturation).
paths = tethys_bake.final_paths(sheet)
with protocol.CacheReader(paths.cache) as reader:
    for frame in (15, 30, 45, 60, 90):
        if frame > ARGS.frames:
            continue
        texture = reader.read(frame).channels["texture"][::-1]
        image = bpy.data.images.new(f"strip{frame}", texture.shape[1], texture.shape[0])
        rgba = np.zeros(texture.shape[:2] + (4,), np.float32)
        rgba[..., 0] = np.clip(texture[..., 0] / 0.6, 0, 1)
        rgba[..., 1] = np.clip(texture[..., 1], 0, 1) * 0.7
        rgba[..., 2] = 0.15
        rgba[..., 3] = 1
        image.pixels.foreach_set(rgba[::-1].reshape(-1))
        image.filepath_raw = str(ARGS.output / f"texture_{frame:03d}.png")
        image.file_format = "PNG"
        image.save()
bpy.ops.wm.save_as_mainfile(filepath=str(ARGS.output / "showcase.blend"))
(ARGS.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")
print(json.dumps({"done": True}), flush=True)
