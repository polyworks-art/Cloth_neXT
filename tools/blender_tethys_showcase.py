# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS showcase: a hanging cloth (half cotton, half waterproof) dipped into water, then rain and trickles.

    blender -b --factory-startup --python tools/blender_tethys_showcase.py -- \
        --repo <repo> --solver-root <copied GAIA root> --output <new dir> --engine <exe> --account <dir>

Real GAIA cloth bake, native wetness bake with fine surface detail and
droplet geometry, Cycles renders (wide and close-up) plus false-colour strips
of the wetness texture.
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
parser.add_argument("--drops", type=int, default=320)
parser.add_argument("--samples", type=int, default=96)
parser.add_argument("--eevee", action="store_true", help="fast EEVEE renders instead of Cycles")
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

# Synthetic GAIA water in a thin slab around the hanging sheet (5 mm cells, so
# a raindrop touches the cloth on a spot about its own size).
lo, hi = (-0.45, -0.03, 0.15), (0.45, 0.03, 1.05)
dims = tuple(int(round((b - a) / 0.005)) + 1 for a, b in zip(lo, hi))
x = np.linspace(lo[0], hi[0], dims[0])
y = np.linspace(lo[1], hi[1], dims[1])
z = np.linspace(lo[2], hi[2], dims[2])
Z, Y, X = np.meshgrid(z, y, x, indexing="ij")
rng = np.random.default_rng(7)
# Rain: individual drops hit the front of the sheet at random places and
# times (each touches it for two frames), denser on the upper half.
DROPS = [(int(rng.integers(40, 80)), float(rng.uniform(-0.36, 0.36)), float(0.55 + 0.4 * rng.random() ** 0.7))
         for _ in range(ARGS.drops)]
# Trickles: three small sources at the top seam keep feeding water that has
# to run down the face. Before that the lower half is dipped into water and
# pulled out again (dewetting on the waterproof half).
TRICKLES = [(-0.22, 0.95), (0.05, 0.97), (0.24, 0.94)]


def fields():
    for frame in range(1, ARGS.frames + 1):
        level = float(np.interp(frame, (6, 18, 26, 36), (0.1, 0.6, 0.6, 0.1)))
        inside = (Z < level) if 6 <= frame <= 36 else np.zeros(X.shape, bool)
        for start, cx, cz in DROPS:
            if start <= frame <= start + 1:
                inside |= (X - cx) ** 2 + Y ** 2 + (Z - cz) ** 2 < 0.0045 ** 2
        if 50 <= frame <= 75:
            for cx, cz in TRICKLES:
                inside |= (X - cx) ** 2 + Y ** 2 + (Z - cz) ** 2 < 0.006 ** 2
        velocity = np.zeros(X.shape + (3,), np.float32)
        velocity[inside] = (0.0, 1.2, -2.0)
        yield frame, (frame - 1) / 24.0, WaterVelocityField(lo, hi, velocity, inside.astype(np.float32))


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


def add_camera(name, location, target, lens):
    camera = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    scene.collection.objects.link(camera)
    camera.location = location
    camera.data.lens = lens
    direction = (np.array(target) - np.array(location))
    camera.rotation_euler = (math.atan2(math.hypot(direction[0], direction[1]), -direction[2]), 0.0,
                             math.atan2(-direction[0], direction[1]))
    return camera


wide = add_camera("Wide", (0.35, -1.45, 0.75), (0.0, 0.0, 0.6), 42)
close = add_camera("Close", (0.12, -0.42, 0.72), (0.02, 0.0, 0.66), 60)


def area(name, location, rotation, size, energy, color=(1.0, 1.0, 1.0)):
    light = bpy.data.objects.new(name, bpy.data.lights.new(name, "AREA"))
    light.data.size, light.data.energy, light.data.color = size, energy, color
    light.location, light.rotation_euler = location, rotation
    scene.collection.objects.link(light)


# Soft key from the front left, cool fill, warm rim from behind for the droplet highlights.
area("Key", (-1.0, -1.3, 1.5), (math.radians(55), 0.0, math.radians(-38)), 1.0, 70.0)
area("Fill", (1.3, -1.0, 0.7), (math.radians(80), 0.0, math.radians(52)), 1.6, 14.0, (0.85, 0.92, 1.0))
area("Rim", (0.4, 1.0, 1.4), (math.radians(-50), 0.0, math.radians(160)), 0.8, 40.0, (1.0, 0.93, 0.85))
# Small, bright source: crisp glints on drops and wet film.
area("Glint", (0.6, -0.9, 1.3), (math.radians(50), 0.0, math.radians(35)), 0.12, 25.0)
backdrop = bpy.data.materials.new("Backdrop")
backdrop.use_nodes = True
next(n for n in backdrop.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled").inputs[
    "Base Color"].default_value = (0.09, 0.1, 0.11, 1.0)
bpy.ops.mesh.primitive_plane_add(size=6, location=(0.0, 1.2, 0.5), rotation=(math.radians(90), 0.0, 0.0))
bpy.context.object.data.materials.append(backdrop)
bpy.ops.mesh.primitive_plane_add(size=6, location=(0.0, 0.0, 0.0))
bpy.context.object.data.materials.append(backdrop)
world = bpy.data.worlds.new("World")
world.use_nodes = True
background = world.node_tree.nodes["Background"]
background.inputs["Color"].default_value = (0.55, 0.6, 0.68, 1.0)
background.inputs["Strength"].default_value = 0.15
scene.world = world
engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
if ARGS.eevee:
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
else:
    scene.render.engine = "CYCLES"
    scene.cycles.samples = ARGS.samples
    scene.cycles.use_denoising = True
    try:
        cycles_prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA"):
            try:
                cycles_prefs.compute_device_type = backend
            except TypeError:
                continue
            cycles_prefs.get_devices()
            if any(d.type == backend for d in cycles_prefs.devices):
                for device in cycles_prefs.devices:
                    device.use = device.type == backend
                scene.cycles.device = "GPU"
                break
    except (KeyError, AttributeError):
        pass
    REPORT["cycles_device"] = scene.cycles.device
scene.view_settings.view_transform = "AgX" if "AgX" in {
    i.identifier for i in scene.view_settings.bl_rna.properties["view_transform"].enum_items} else "Filmic"
scene.render.resolution_x, scene.render.resolution_y = 1280, 860
scene.render.image_settings.file_format = "PNG"
renders = []
for camera, frames in ((wide, (22, 40, 62, 90)), (close, (40, 62, 90))):
    scene.camera = camera
    for frame in frames:
        if frame > ARGS.frames:
            continue
        scene.frame_set(frame)
        scene.render.filepath = str(ARGS.output / f"render_{camera.name.lower()}_{frame:03d}.png")
        started = time.monotonic()
        bpy.ops.render.render(write_still=True)
        renders.append((scene.render.filepath, time.monotonic() - started))
        print("rendered", scene.render.filepath, flush=True)
REPORT["renders"] = renders

# False-colour strips of the wetness texture (R: surface film, G: saturation).
paths = tethys_bake.final_paths(sheet)
with protocol.CacheReader(paths.cache) as reader:
    for frame in (22, 40, 62, 90):
        if frame > ARGS.frames:
            continue
        texture = reader.read(frame).channels["texture"][::-1]
        image = bpy.data.images.new(f"strip{frame}", texture.shape[1], texture.shape[0])
        rgba = np.zeros(texture.shape[:2] + (4,), np.float32)
        rgba[..., 0] = np.clip(texture[..., 0] / 0.6, 0, 1)
        rgba[..., 1] = np.clip(texture[..., 1], 0, 1) * 0.7
        rgba[..., 2] = 0.5 + 0.5 * texture[..., 2] if texture.shape[-1] == 4 else 0.15
        rgba[..., 3] = 1
        image.pixels.foreach_set(rgba[::-1].reshape(-1))
        image.filepath_raw = str(ARGS.output / f"texture_{frame:03d}.png")
        image.file_format = "PNG"
        image.save()
bpy.ops.wm.save_as_mainfile(filepath=str(ARGS.output / "showcase.blend"))
(ARGS.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")
print(json.dumps({"done": True}), flush=True)
