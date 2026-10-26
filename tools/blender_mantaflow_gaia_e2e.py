# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real GAIA cloth bake driven by a real Blender Mantaflow liquid.

    blender -b --factory-startup --python tools/blender_mantaflow_gaia_e2e.py -- \
        --repo <repo> --solver-root <copied GAIA root> --output <new dir> [--frames 36] [--no-pressure]
        [--source particles]

``--source particles`` feeds the same liquid as imported particles (the
Houdini/LiquiGen path): a point mesh with a ``velocity`` attribute rewritten
each frame from the Mantaflow cache, plus the liquid mesh as Surface.

Builds a sheet over a box, pours a Mantaflow liquid onto it, bakes Mantaflow
(OpenVDB data + mesh), then runs the actual Bake operator twice: dry and with
GAIA Water Flow from Mantaflow. Checks the prepared .gaia field, the FLIP-style
surface folder Hydrodynamic Pressure reads, and that the water moves the cloth.
"""
from __future__ import annotations

import argparse
import json
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
    parser.add_argument("--frames", type=int, default=36)
    parser.add_argument("--no-pressure", action="store_true", help="Native Flow only")
    parser.add_argument("--source", choices=("mantaflow", "particles"), default="mantaflow")
    parser.add_argument("--skip-noise", action="store_true", help="one dry bake only")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


ARGS = parse()
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)

from cloth_next.bake import pc2  # noqa: E402
from cloth_next.blender import gaia_account, solver_test, water_preparation  # noqa: E402
from cloth_next.blender import water_mantaflow, water_particles  # noqa: E402
from cloth_next.gaia import mantaflow  # noqa: E402
from cloth_next.gaia.container import GaiaContainer  # noqa: E402
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver  # noqa: E402

REPORT = {"blender": bpy.app.version_string, "checks": {}, "measurements": {}}


def save():
    (ARGS.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")


def check(name, ok, detail=""):
    REPORT["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:800]}
    save()
    print(("PASS " if ok else "FAIL ") + name, detail, flush=True)


ROOT = ARGS.solver_root.resolve()
RESOLVED = SolverResolver(solver_test._version_probe).resolve(SolverResolutionContext(
    development_executable=ROOT / "target/cuda/release/ppf-cts-server.exe"))
solver_test.resolve_solver = lambda _context: RESOLVED
gaia_account.access_granted = lambda *a, **k: True  # Water Flow is gated on GAIA access
prefs = bpy.context.preferences.addons["cloth_next"].preferences
prefs.auto_launch_bake_window = False
scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 24, 1.0
scene.frame_start, scene.frame_end = 1, ARGS.frames

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

# Mantaflow: a 2 m box, liquid poured from above onto the sheet.
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 1.0))
domain = bpy.context.object
domain.name = "MantaDomain"
fluid = domain.modifiers.new("Fluid", "FLUID")
fluid.fluid_type = "DOMAIN"
d = fluid.domain_settings
d.domain_type = "LIQUID"
d.resolution_max = 48
d.use_mesh = True
d.cache_type = "ALL"
d.cache_data_format = "OPENVDB"
d.cache_directory = str(ARGS.output / "manta")
d.cache_frame_start, d.cache_frame_end = 1, ARGS.frames + 1
bpy.ops.mesh.primitive_cylinder_add(radius=0.18, depth=0.2, location=(0.0, 0.0, 1.6))
inflow = bpy.context.object
inflow.name = "Inflow"
flow = inflow.modifiers.new("Fluid", "FLUID")
flow.fluid_type = "FLOW"
flow.flow_settings.flow_type = "LIQUID"
flow.flow_settings.flow_behavior = "INFLOW"
flow.flow_settings.use_initial_velocity = True
flow.flow_settings.velocity_coord = (0.0, 0.0, -2.0)
bpy.ops.mesh.primitive_cube_add(size=0.6, location=(0.0, 0.0, 0.3))
box = bpy.context.object
box.name = "Box"
effector = box.modifiers.new("Fluid", "FLUID")
effector.fluid_type = "EFFECTOR"
started = time.monotonic()
with bpy.context.temp_override(object=domain, active_object=domain):
    bpy.ops.fluid.bake_all()
REPORT["measurements"]["mantaflow_bake_seconds"] = time.monotonic() - started
check("mantaflow_baked", d.has_cache_baked_data and d.has_cache_baked_mesh)
domain.hide_render = True

# Cloth: sheet over the box.
# 1 m keeps the Hydrodynamic Pressure SDF of cloth box + margin under its 24 M cell limit.
bpy.ops.mesh.primitive_grid_add(x_subdivisions=24, y_subdivisions=24, size=1.0, location=(0.0, 0.0, 0.75))
sheet = bpy.context.object
sheet.name = "Sheet"
bpy.ops.clothnext.add_physics()
s = sheet.cloth_next
s.bake_start, s.bake_end = 1, ARGS.frames
s.material.preset = "COTTON"
bpy.context.view_layer.objects.active = box
box.select_set(True)
bpy.ops.clothnext.add_physics()
bpy.ops.clothnext.set_object_type(role="COLLIDER")
bpy.context.view_layer.objects.active = sheet
sheet.select_set(True)


def bake(label):
    s.cache_directory = str(ARGS.output / f"cache_{label}")
    begin = time.monotonic()
    result = bpy.ops.clothnext.bake("EXEC_DEFAULT")
    while solver_test.shared_controller.snapshot().active:
        if time.monotonic() - begin > 3600:
            solver_test.request_cancel()
            raise TimeoutError("Bake exceeded one hour")
        if solver_test._pin_capture is not None:
            solver_test._pin_capture_pump()
        if water_preparation.active():
            water_preparation._pump()
        solver_test._startup_pump()
        solver_test._pump()
        time.sleep(0.02)
    snapshot = solver_test.shared_controller.snapshot()
    REPORT["measurements"][f"{label}_bake_seconds"] = time.monotonic() - begin
    modifier = next((m for m in sheet.modifiers if m.type == "MESH_CACHE"), None)
    path = Path(bpy.path.abspath(modifier.filepath)) if modifier else None
    check(f"{label}_bake_finished", snapshot.state.value == "FINISHED" and path and path.is_file(),
          f"{result} {snapshot.state.value} {snapshot.error_summary} {snapshot.error_details}")
    frames = [np.asarray(frame) for frame in pc2.iter_frames(path)] if path and path.is_file() else []
    return frames


dry = bake("dry")
dry2 = dry if ARGS.skip_noise else bake("dry2")  # GPU run-to-run noise baseline
s.water_flow_enabled = True
if ARGS.source == "particles":
    # Imported-particles path: points + velocity attribute per frame, liquid mesh as Surface.
    points = bpy.data.meshes.new("LiquidParticles")
    particles = bpy.data.objects.new("LiquidParticles", points)
    scene.collection.objects.link(particles)
    grid_min, grid_max = water_mantaflow.domain_box(domain)
    dx = float(max(grid_max - grid_min)) / d.resolution_max

    def feed(scene_, *_args):
        frame = scene_.frame_current
        evaluated = domain.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        vertices = np.array([v.co[:] for v in mesh.vertices]).reshape(-1, 3) + np.asarray(domain.matrix_world)[:3, 3]
        faces = np.array([t.vertices[:] for t in mesh.loop_triangles]).reshape(-1, 3)
        evaluated.to_mesh_clear()
        velocity, origin = mantaflow.read_velocity(mantaflow.data_file(Path(bpy.path.abspath(d.cache_directory)), frame))
        p, v = mantaflow.liquid_samples(velocity, origin, grid_minimum=grid_min, dx=dx,
                                        vertices=vertices, faces=faces)
        points.clear_geometry()
        points.vertices.add(len(p))
        points.vertices.foreach_set("co", p.ravel())
        attribute = points.attributes.get("velocity") or points.attributes.new("velocity", "FLOAT_VECTOR", "POINT")
        attribute.data.foreach_set("vector", v.ravel())
        points.update()
    bpy.app.handlers.frame_change_post.append(feed)
    s.water_flow_provider = "HOUDINI"
    s.water_flow_domain = particles
    s.water_flow_surface = domain
else:
    s.water_flow_provider = "MANTAFLOW"
    s.water_flow_domain = domain
s.water_flow_container = str(ARGS.output / "Mantaflow.gaia")
s.water_hydrodynamic_pressure = not ARGS.no_pressure
wet = bake("wet")

container = ARGS.output / "Mantaflow.gaia"
if container.is_file():
    with GaiaContainer(container) as cache:
        water = cache.manifest["water"]
        provider = water["provider"]
        speeds = []
        for index in range(len(cache.manifest["frames"])):
            field = cache.frame(index)
            occupied = field.influence > 0
            if occupied.any():
                speeds.append(float(np.linalg.norm(field.velocity[occupied], axis=-1).mean()))
    check("gaia_field_from_mantaflow", provider.get("provider") == ("Houdini FX" if ARGS.source == "particles" else "Mantaflow")
          and provider.get("mode") == "FLUID_PARTICLES" and water.get("physical_velocity_units") == "m/s",
          {k: provider.get(k) for k in ("provider", "mode", "dx", "sampling")})
    REPORT["measurements"]["mean_water_speed_m_s"] = speeds
    check("water_speed_plausible", speeds and 0.3 < max(speeds) < 8.0, max(speeds) if speeds else None)
else:
    check("gaia_field_from_mantaflow", False, "no .gaia container")
folder = (water_particles.surface_folder(container) if ARGS.source == "particles"
          else water_mantaflow.surface_folder(container))
surfaces = sorted(folder.glob("*.bobj"))
check("pressure_surfaces_written", len(surfaces) >= ARGS.frames, len(surfaces))
check("water_flow_stayed_on", s.water_flow_enabled
      and s.water_flow_provider == ("HOUDINI" if ARGS.source == "particles" else "MANTAFLOW"))
if dry and dry2 and wet and len(dry) == len(wet) == len(dry2):
    def mean_distance(first, second):
        return float(np.mean([np.linalg.norm(a - b, axis=1).mean() for a, b in zip(first, second)]))
    noise, difference = mean_distance(dry, dry2), mean_distance(dry, wet)
    drop = float(np.mean(wet[-1][:, 2]) - np.mean(dry[-1][:, 2]))
    REPORT["measurements"].update(mean_dry_vs_dry_m=noise, mean_wet_vs_dry_m=difference,
                                  final_mean_height_wet_minus_dry_m=drop)
    check("water_moves_the_cloth", difference > max(0.005, 3 * noise),
          f"wet {difference:.4f} m, noise {noise:.4f} m, final height change {drop:+.4f} m")
REPORT["result"] = "PASS" if all(c["ok"] for c in REPORT["checks"].values()) else "FAIL"
save()
print(json.dumps({"result": REPORT["result"]}))
