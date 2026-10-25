# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-bake only the TETHYS wetness of a saved scene and render frames (no cloth re-bake).

    blender -b <scene.blend> --python tools/blender_tethys_rebake.py -- --repo <repo> --output <dir>
        [--engine <exe>] [--account <dir>] [--object WetSheet] [--set immersion_film=3.0 ...]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import addon_utils
import bpy
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--engine", type=Path)
parser.add_argument("--account", type=Path)
parser.add_argument("--object", default="WetSheet")
parser.add_argument("--frames", default="5,15,30,45,60,90")
parser.add_argument("--set", action="append", default=[], help="tethys property=value")
ARGS = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=True)
if ARGS.account:
    os.environ["CLOTH_NEXT_ACCOUNT_ROOT"] = str(ARGS.account.resolve())
if ARGS.engine:
    os.environ["CLOTH_NEXT_TETHYS_ENGINE"] = str(ARGS.engine.resolve())
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)
from cloth_next.blender import tethys_bake  # noqa: E402
from cloth_next.tethys import engine, protocol  # noqa: E402

tethys_bake.availability = lambda refresh=False: engine.Availability(True)
obj = bpy.data.objects[ARGS.object]
settings = obj.cloth_next.tethys
for item in ARGS.set:
    key, value = item.split("=", 1)
    current = getattr(settings, key)
    setattr(settings, key, type(current)(value) if not isinstance(current, str) else value)
started = time.monotonic()
message, job = tethys_bake.bake_blocking(bpy.context, obj)
report = {"message": message, "seconds": time.monotonic() - started, "engine": job.result}
print(message, flush=True)
scene = bpy.context.scene
frames = [int(f) for f in ARGS.frames.split(",")]
for frame in frames:
    scene.frame_set(frame)
    scene.render.filepath = str(ARGS.output / f"render_{frame:03d}.png")
    bpy.ops.render.render(write_still=True)
with protocol.CacheReader(tethys_bake.final_paths(obj).cache) as reader:
    for frame in frames:
        data = reader.read(frame).channels.get("texture")
        if data is None:
            continue
        image = bpy.data.images.new(f"t{frame}", data.shape[1], data.shape[0])
        rgba = np.zeros(data.shape[:2] + (4,), np.float32)
        rgba[..., 0] = np.clip(data[..., 0] / 1.0, 0, 1)
        rgba[..., 1] = np.clip(data[..., 1], 0, 1) * 0.7
        rgba[..., 2] = 0.15
        rgba[..., 3] = 1
        image.pixels.foreach_set(rgba.reshape(-1))
        image.filepath_raw = str(ARGS.output / f"texture_{frame:03d}.png")
        image.file_format = "PNG"
        image.save()
(ARGS.output / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
