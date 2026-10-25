# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS end to end in real Blender: real GAIA cloth bake, native wetness bake.

    blender -b --factory-startup --python tools/blender_tethys_e2e.py -- \
        --repo <repo> --solver-root <copied GAIA root> --output <new dir> [--frames 72]

A cotton sheet with a waterproof Material Zone drapes over a box. A synthetic
GAIA water field (built with the add-on's own .gaia writer) rises over the
box and recedes again. The cloth is baked with the real GAIA solver; the
wetness is baked with the bundled native TETHYS Engine. Checks cover cache
ownership, zones, materials, renders, save/reload, stale detection, cancel,
determinism and Clear.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import addon_utils
import bpy
import numpy as np


def parse():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--solver-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=72)
    parser.add_argument("--resolution", type=int, default=24)
    parser.add_argument("--engine", type=Path, help="engine binary when none is staged in the add-on")
    parser.add_argument("--keep-blend", type=Path,
                        help="save the baked scene here after the material checks and stop (manual viewing)")
    parser.add_argument("--account", type=Path,
                        help="PolyWorks account folder holding a GAIA entitlement the engine trusts")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


ARGS = parse()
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=False)
os.environ["CLOTH_NEXT_ACCOUNT_ROOT"] = str((ARGS.account or ARGS.output / "account").resolve())
if ARGS.engine:
    os.environ["CLOTH_NEXT_TETHYS_ENGINE"] = str(ARGS.engine.resolve())
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)

from cloth_next.bake import cache_metadata  # noqa: E402
from cloth_next.blender import material_zones, solver_test, tethys_bake, tethys_shader, validation_state  # noqa: E402
from cloth_next.gaia.container import write_water_container  # noqa: E402
from cloth_next.gaia.water_field import WaterVelocityField  # noqa: E402
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver  # noqa: E402
from cloth_next.tethys import cache as tcache  # noqa: E402
from cloth_next.tethys import engine, protocol  # noqa: E402

REPORT = {"blender": bpy.app.version_string, "checks": {}, "measurements": {}}


def save():
    (ARGS.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")


def check(name, ok, detail=""):
    REPORT["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:600]}
    save()
    print(("PASS " if ok else "FAIL ") + name, detail, flush=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


ROOT = ARGS.solver_root.resolve()
RESOLVED = SolverResolver(solver_test._version_probe).resolve(SolverResolutionContext(
    development_executable=ROOT / "target/cuda/release/ppf-cts-server.exe"))
solver_test.resolve_solver = lambda _context: RESOLVED
GATE = {"open": True}
tethys_bake.availability = lambda refresh=False: (
    engine.Availability(True) if GATE["open"] else engine.Availability(False, "Select GAIA as the solver engine in Preferences"))

prefs = bpy.context.preferences.addons["cloth_next"].preferences
prefs.auto_launch_bake_window = False
scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 24, 1.0
scene.frame_start, scene.frame_end = 1, ARGS.frames

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=ARGS.resolution, y_subdivisions=ARGS.resolution, size=1.2,
                                location=(0.0, 0.0, 0.75))
sheet = bpy.context.object
sheet.name = "WetSheet"
bpy.ops.clothnext.add_physics()
sheet.cloth_next.bake_start, sheet.cloth_next.bake_end = 1, ARGS.frames
sheet.cloth_next.cache_directory = str(ARGS.output / "cache")
sheet.cloth_next.material.preset = "COTTON"
bpy.ops.mesh.primitive_cube_add(size=0.6, location=(0.0, 0.0, 0.3))
box = bpy.context.object
box.name = "Box"
bpy.ops.clothnext.add_physics()
bpy.ops.clothnext.set_object_type(role="COLLIDER")
bpy.context.view_layer.objects.active = sheet
sheet.select_set(True)

# Material with linked textures: TETHYS must keep these sources.
material = bpy.data.materials.new("Fabric")
material.use_nodes = True
tree = material.node_tree
principled = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
noise = tree.nodes.new("ShaderNodeTexNoise")
ramp = tree.nodes.new("ShaderNodeValToRGB")
tree.links.new(noise.outputs["Fac"], principled.inputs["Roughness"])
checker = tree.nodes.new("ShaderNodeTexChecker")
checker.inputs["Color1"].default_value = (0.75, 0.2, 0.15, 1)
checker.inputs["Color2"].default_value = (0.8, 0.7, 0.5, 1)
tree.links.new(checker.outputs["Color"], principled.inputs["Base Color"])
sheet.data.materials.append(material)
second = bpy.data.materials.new("Plain")
second.use_nodes = True
sheet.data.materials.append(second)
for polygon in sheet.data.polygons:
    polygon.material_index = 1 if polygon.center.x > 0.3 else 0

# Material Zone: waterproof stripe on the -X side (mechanically identical).
bpy.ops.clothnext.add_material_zone()
zone = sheet.cloth_next.material_zones[0]
zone.name = "Waterproof Panel"
owners = tuple(zone.token if p.center.x < -0.15 else 0 for p in sheet.data.polygons)
material_zones.write_owners(sheet, owners)
zone.wetness_profile = "WATERPROOF"
ppf_zones_before = material_zones.zones_of(sheet)
owners_before = material_zones.read_owners(sheet.data)

# Synthetic GAIA water: rises from below the box over the top, then recedes.
lo, hi, dims = (-1.5, -1.5, -0.5), (1.5, 1.5, 1.5), (31, 31, 21)
z = np.linspace(lo[2], hi[2], dims[2])


def level(frame):
    if frame < 24:
        return -1.0
    if frame < 44:
        return -0.2 + (frame - 24) / 20 * 1.2
    if frame < 52:
        return 1.0
    return -1.0


def fields():
    for frame in range(1, ARGS.frames + 1):
        influence = np.broadcast_to((z < level(frame))[:, None, None], (dims[2], dims[1], dims[0]))
        yield frame, (frame - 1) / 24.0, WaterVelocityField(
            lo, hi, np.zeros((dims[2], dims[1], dims[0], 3), np.float32), influence.astype(np.float32))


container = ARGS.output / "water.gaia"
write_water_container(container, {"fps": 24.0, "fingerprint": "synthetic-rising-water",
                                  "provider": {"sampling": "all-liquid-particles"}, "frames": [1, ARGS.frames]},
                      fields())
sheet.cloth_next.water_flow_container = str(container)

# 1. Real GAIA cloth bake (Water Flow off: the water only feeds TETHYS).
started = time.monotonic()
result = bpy.ops.clothnext.bake("EXEC_DEFAULT")
while solver_test.shared_controller.snapshot().active:
    solver_test._startup_pump()
    solver_test._pump()
    time.sleep(0.02)
snapshot = solver_test.shared_controller.snapshot()
REPORT["measurements"]["cloth_bake_seconds"] = time.monotonic() - started
inspection = solver_test.inspect_attached_cache(sheet)
check("cloth_bake_ready", getattr(inspection, "condition", None) is not None
      and inspection.condition.value == "READY", f"{result} {snapshot.state.value} {snapshot.error_details}")
modifier = next(m for m in sheet.modifiers if m.type == "MESH_CACHE")
pc2_path = Path(bpy.path.abspath(modifier.filepath))
pc2_hash = sha(pc2_path)
pc2_sidecar_hash = sha(cache_metadata.sidecar_path(pc2_path))
# Pretend a full validation just succeeded, to prove TETHYS never demotes it.
validation_before = validation_state.store_valid(
    sheet, pin_count=0, pin_group="", topology_signature="t", geometry_fingerprint="g",
    settings_fingerprint="s")

# 2. Gate: no GAIA -> refused, nothing launched.
sheet.cloth_next.tethys.enabled = True
sheet.cloth_next.tethys.debug_channels = True
sheet.cloth_next.tethys.evaporation = 2000.0  # visible drying within the short shot
bpy.context.view_layer.update()  # the UI flushes property edits at once; a script must ask
GATE["open"] = False
try:
    tethys_bake.prepare(bpy.context, sheet)
    refused = False
except tethys_bake.TethysBakeError as exc:
    refused = "unavailable" in str(exc)
check("gaia_gate_refuses", refused and not tethys_bake.final_paths(sheet).cache.exists())
GATE["open"] = True

# 3. Wetness bake.
started = time.monotonic()
message, job = tethys_bake.bake_blocking(bpy.context, sheet)
REPORT["measurements"]["wetness_bake_seconds"] = time.monotonic() - started
REPORT["measurements"]["engine"] = job.result
check("wetness_bake_completes", job.result is not None and "Wetness baked" in message, message)
paths = tethys_bake.final_paths(sheet)
state, record = tcache.inspect(paths, None, full=True)
check("wetness_cache_ready", state is tcache.CacheState.READY, state)
check("cloth_cache_untouched", sha(pc2_path) == pc2_hash and sha(cache_metadata.sidecar_path(pc2_path)) == pc2_sidecar_hash)
check("zone_ownership_unchanged", material_zones.read_owners(sheet.data) == owners_before)
check("ppf_zone_parameters_unchanged", material_zones.zones_of(sheet) == ppf_zones_before)
# Let the deferred validation observer run.
bpy.context.view_layer.update()
check("cloth_validation_kept", validation_state.record_for(sheet).state.value == "VALID",
      f"{validation_before.state} -> {validation_state.record_for(sheet).state}")
check("cloth_still_ready", solver_test.inspect_attached_cache(sheet).condition.value == "READY")

# 4. Numerical behavior from the cache: zones differ, water enters, stays, dries.
with protocol.CacheReader(paths.cache, verify_all=True) as reader:
    frames = {f: reader.read(f) for f in (1, 23, 40, 51, 52, ARGS.frames)}
owner = np.asarray(owners_before)
sat_wet = frames[51].channels["saturation"]
surface_wet = frames[51].channels["surface"]
cotton_sat = float(sat_wet[owner == 0].mean())
nylon_sat = float(sat_wet[owner != 0].mean())
REPORT["measurements"]["mean_saturation_frame51"] = {"cotton": cotton_sat, "waterproof": nylon_sat}
check("dry_before_contact", float(frames[23].channels["saturation"].max()) == 0.0
      and float(frames[23].channels["surface"].max()) == 0.0)
check("wet_while_submerged", cotton_sat > 0.5, cotton_sat)
check("zones_absorb_differently", nylon_sat < 0.5 * cotton_sat or float(
    frames[51].ledger["absorbed"]) > 0, f"cotton={cotton_sat} waterproof={nylon_sat}")
end = frames[ARGS.frames]
check("moisture_retained_after_exit", float(end.channels["saturation"][owner == 0].mean()) > 0.2,
      float(end.channels["saturation"][owner == 0].mean()))
# Same exposure, different materials: absorption, surface water, runoff, drying.
with protocol.CacheReader(paths.cache) as reader:
    drained = {0: 0.0, 1: 0.0}
    for frame in range(1, ARGS.frames + 1):
        values = reader.read(frame).channels["drained"]
        drained[0] += float(values[owner == 0].mean())
        drained[1] += float(values[owner != 0].mean())
capacity = {0: 0.35, 1: 0.002}   # mm, Cotton and Waterproof profiles


def zone_mean(frame, channel, which):
    values = frames[frame].channels[channel]
    return float(values[(owner == 0) if which == 0 else (owner != 0)].mean())


behavior = {name: {
    "absorbed_mm_frame51": zone_mean(51, "saturation", k) * capacity[k],
    "surface_mm_frame51": zone_mean(51, "surface", k),
    "drained_mm_total": drained[k],
    "absorbed_mm_end": zone_mean(ARGS.frames, "saturation", k) * capacity[k],
    "water_mm_exit": zone_mean(52, "saturation", k) * capacity[k] + zone_mean(52, "surface", k),
    "water_mm_end": zone_mean(ARGS.frames, "saturation", k) * capacity[k] + zone_mean(ARGS.frames, "surface", k),
} for k, name in ((0, "cotton"), (1, "waterproof"))}
REPORT["measurements"]["zone_behavior"] = behavior
check("adjacent_zones_behave_differently",
      behavior["cotton"]["absorbed_mm_frame51"] > 20 * behavior["waterproof"]["absorbed_mm_frame51"]
      and behavior["waterproof"]["drained_mm_total"] > behavior["cotton"]["drained_mm_total"]
      and behavior["cotton"]["water_mm_end"] < behavior["cotton"]["water_mm_exit"]
      and behavior["cotton"]["water_mm_end"] > behavior["waterproof"]["water_mm_end"], behavior)
ledger = end.ledger
balance = ledger["initial"] + ledger["injected"] - (ledger["surface"] + ledger["absorbed"] + ledger["evaporated"]
                                                    + ledger["drained"] + ledger["roundoff"])
check("mass_ledger_balances", abs(balance) <= 1e-9 * max(ledger["injected"], 1e-12), ledger)
REPORT["measurements"]["ledger_last"] = ledger

# 5. Material integration keeps the original sources.
group = next(n for n in tree.nodes if n.get(tethys_shader.OWNED) and n.bl_idname == "ShaderNodeGroup"
             and n.node_tree.name == tethys_shader.GROUP)
check("roughness_texture_preserved", group.inputs["Roughness"].links[0].from_node == noise
      and principled.inputs["Roughness"].links[0].from_node == group)
check("base_color_texture_preserved", group.inputs["Base Color"].links[0].from_node == checker)
check("second_material_connected", any(n.get(tethys_shader.OWNED) for n in second.node_tree.nodes))
image = bpy.data.images.get(tethys_shader.image_name(sheet))
check("image_sequence", image is not None and image.source == "SEQUENCE", getattr(image, "filepath", None))

# 6. Animated values in the EXR frames match the cache at the right frames.
mismatch = 0.0
details = []
for frame in (1, 40, 51, ARGS.frames):
    loaded = bpy.data.images.load(str(paths.frame_image(frame)), check_existing=False)
    loaded.colorspace_settings.name = "Non-Color"   # data, exactly as the material reads it
    loaded.reload()
    pixels = np.empty(loaded.size[0] * loaded.size[1] * 4, np.float32)
    loaded.pixels.foreach_get(pixels)
    pixels = pixels.reshape(-1, 4)
    with protocol.CacheReader(paths.cache) as reader:
        values = reader.read(frame)
    if "texture" in values.channels:
        parts = values.channels["texture"].shape[-1]
        texture = values.channels["texture"].reshape(-1, parts)
        errors = tuple(float(np.abs(pixels[:, k] - texture[:, k]).max()) for k in range(2))
        normal = bpy.data.images.load(str(paths.normal_image(frame)), check_existing=False)
        normal.colorspace_settings.name = "Non-Color"
        normal.reload()
        encoded = np.empty(normal.size[0] * normal.size[1] * 4, np.float32)
        normal.pixels.foreach_get(encoded)
        encoded = encoded.reshape(-1, 4)
        errors += tuple(float(np.abs(2.0 * encoded[:, k] - 1.0 - texture[:, 2 + k]).max()) for k in range(2))
        bpy.data.images.remove(normal)
    else:
        count = len(sheet.data.polygons)
        errors = (float(np.abs(pixels[:count, 0] - values.channels["surface"]).max()),
                  float(np.abs(pixels[:count, 1] - values.channels["saturation"]).max()))
    details.append((frame, loaded.size[:], errors))
    mismatch = max(mismatch, *errors)
    bpy.data.images.remove(loaded)
check("exr_frames_match_cache", mismatch < 1e-3, details)

if ARGS.keep_blend:
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(camera)
    camera.location, camera.rotation_euler = (0.0, -2.2, 1.6), (1.05, 0.0, 0.0)
    scene.camera = camera
    bpy.ops.wm.save_as_mainfile(filepath=str(ARGS.keep_blend.resolve()))
    REPORT["result"] = "PASS" if all(item["ok"] for item in REPORT["checks"].values()) else "FAIL"
    save()
    print(json.dumps({"result": REPORT["result"], "kept": str(ARGS.keep_blend)}), flush=True)
    sys.exit(0)

# 7. Renders: wet frames render darker than dry ones (Cycles and EEVEE).
camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(camera)
camera.location = (0.0, -2.2, 1.6)
camera.rotation_euler = (1.05, 0.0, 0.0)
scene.camera = camera
light = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
scene.collection.objects.link(light)
light.rotation_euler = (0.6, 0.2, 0.0)
scene.render.resolution_x = scene.render.resolution_y = 96
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"


def render(engine_name, frame):
    scene.render.engine = engine_name
    if engine_name == "CYCLES":
        scene.cycles.samples = 8
        scene.cycles.device = "CPU"
    scene.frame_set(frame)
    target = ARGS.output / f"render_{engine_name}_{frame}.png"
    scene.render.filepath = str(target)
    bpy.ops.render.render(write_still=True)
    shot = bpy.data.images.load(str(target), check_existing=False)
    pixels = np.empty(shot.size[0] * shot.size[1] * 4, np.float32)
    shot.pixels.foreach_get(pixels)
    bpy.data.images.remove(shot)
    return float(pixels.reshape(-1, 4)[:, :3].mean())


for engine_name in ("CYCLES", "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {
        e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} else "BLENDER_EEVEE"):
    try:
        dry, wet = render(engine_name, 1), render(engine_name, 51)
        REPORT["measurements"][f"render_mean_{engine_name}"] = {"dry": dry, "wet": wet}
        check(f"render_{engine_name.lower()}_wet_differs", abs(dry - wet) > 0.002, f"dry={dry} wet={wet}")
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        check(f"render_{engine_name.lower()}_wet_differs", False, exc)

# 8. Stale detection, determinism, cancel keeps the previous result.
fingerprint = record["fingerprints"]["combined"]
cache_hash = sha(paths.cache)
sheet.cloth_next.tethys.absorption = 2.0
check("settings_change_is_stale", tethys_bake.cache_status(sheet)[0] == "STALE")
check("stale_wetness_keeps_cloth_ready", solver_test.inspect_attached_cache(sheet).condition.value == "READY")
sheet.cloth_next.tethys.absorption = 1.0
check("restored_settings_ready", tethys_bake.cache_status(sheet)[0] == "READY")
message, _job = tethys_bake.bake_blocking(bpy.context, sheet)
check("rebake_is_deterministic", sha(paths.cache) == cache_hash, message)
message, cancel_job = tethys_bake.bake_blocking(bpy.context, sheet, cancel_after=0.0)
staging = tethys_bake.staging_paths(sheet)
leftovers = [p.name for p in staging.root.glob(staging.stem + "*")]
check("cancel_keeps_previous_and_cleans_up", cancel_job.cancelled and not leftovers
      and tcache.inspect(paths, None, full=True)[0] is tcache.CacheState.READY, f"{message} {leftovers}")

# 9. Save and reload.
blend = ARGS.output / "tethys.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
bpy.ops.wm.open_mainfile(filepath=str(blend))
sheet = bpy.data.objects["WetSheet"]
material = bpy.data.materials["Fabric"]
check("reload_keeps_settings", sheet.cloth_next.tethys.enabled and
      sheet.cloth_next.material_zones[0].wetness_profile == "WATERPROOF")
check("reload_keeps_cache_ready", tethys_bake.cache_status(sheet)[0] == "READY", tethys_bake.cache_status(sheet))
check("reload_keeps_material_nodes", any(n.get(tethys_shader.OWNED) for n in material.node_tree.nodes))

# 10. Clear removes only TETHYS data and restores materials.
pc2_files = sorted(p.name for p in (ARGS.output / "cache").glob("*.pc2"))
bpy.context.view_layer.objects.active = sheet
bpy.ops.clothnext.tethys_clear()
tree = material.node_tree
principled = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
check("clear_restores_material", not any(n.get(tethys_shader.OWNED) for n in tree.nodes)
      and principled.inputs["Roughness"].links[0].from_node.bl_idname == "ShaderNodeTexNoise"
      and principled.inputs["Base Color"].links[0].from_node.bl_idname == "ShaderNodeTexChecker")
check("clear_removes_only_tethys_files", not paths.cache.exists() and not paths.frames.exists()
      and sorted(p.name for p in (ARGS.output / "cache").glob("*.pc2")) == pc2_files
      and sha(pc2_path) == pc2_hash)
check("cell_attribute_removed", sheet.data.attributes.get(tethys_shader.ATTRIBUTE) is None)

REPORT["result"] = "PASS" if all(item["ok"] for item in REPORT["checks"].values()) else "FAIL"
save()
print(json.dumps({"result": REPORT["result"]}), flush=True)
