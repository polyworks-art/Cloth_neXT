# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender to exercise automatic ThreadMark render integration."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import bpy


def _args() -> tuple[Path, Path]:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(values) != 2:
        raise SystemExit("expected -- OUTPUT_DIR REPORT_JSON")
    return Path(values[0]), Path(values[1])


OUTPUT, REPORT = _args()
OUTPUT.mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.blender import threadmark_render  # noqa: E402


worker_events: list[dict] = []
case_name = "startup"


class TrackingWorker(threadmark_render.OwnedThreadMarkWorker):
    def __init__(self):
        super().__init__()
        worker_events.append({"event": "created", "case": case_name})

    def encode(self, path):
        result = super().encode(path)
        worker_events.append(
            {
                "event": "encode",
                "case": case_name,
                "path": Path(path).name,
                "pid": self.process.pid,
                "ok": result[0],
                "reason": result[1],
            }
        )
        return result

    def shutdown(self):
        process = self.process
        super().shutdown()
        if process is not None:
            worker_events.append(
                {
                    "event": "shutdown",
                    "case": case_name,
                    "pid": process.pid,
                    "returncode": process.returncode,
                }
            )


def _configure_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.use_file_extension = True
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.object.camera_add(location=(0, 0, 5))
    scene.camera = bpy.context.object
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    material = bpy.data.materials.new("ThreadMark integration material")
    material.diffuse_color = (0.76, 0.14, 0.04, 1.0)
    bpy.context.object.data.materials.append(material)
    world = bpy.data.worlds.new("ThreadMark integration world")
    world.color = (0.03, 0.08, 0.18)
    scene.world = world
    return scene


def _render(scene, name, image_format, path, **operator_args):
    global case_name
    case_name = name
    scene.render.image_settings.file_format = image_format
    scene.render.filepath = str(path)
    result = bpy.ops.render.render(**operator_args)
    return {"name": name, "operator": sorted(result), "path": path.name}


def main() -> None:
    scene = _configure_scene()
    threadmark_render._worker_factory = TrackingWorker
    threadmark_render.should_threadmark_render = lambda _scene: True
    threadmark_render.register()
    cases = []
    try:
        formats = {
            "png": "PNG",
            "jpg": "JPEG",
            "jpeg": "JPEG",
            "webp": "WEBP",
            "tif": "TIFF",
            "tiff": "TIFF",
        }
        for suffix, image_format in formats.items():
            path = OUTPUT / f"eligible-{suffix}.{suffix}"
            cases.append(
                _render(scene, f"eligible-{suffix}", image_format, path, write_still=True)
            )

        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = str(OUTPUT / "animation-")
        scene.frame_start = 1
        scene.frame_end = 2
        cases.append(
            _render(
                scene,
                "animation",
                "PNG",
                OUTPUT / "animation-",
                animation=True,
            )
        )

        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        cases.append(
            _render(
                scene,
                "cycles",
                "PNG",
                OUTPUT / "cycles.png",
                write_still=True,
            )
        )
        scene.render.engine = "BLENDER_EEVEE"

        cases.append(
            _render(
                scene,
                "unsupported-exr",
                "OPEN_EXR",
                OUTPUT / "unsupported.exr",
                write_still=True,
            )
        )

        threadmark_render.should_threadmark_render = lambda _scene: False
        cases.append(
            _render(
                scene,
                "ineligible",
                "PNG",
                OUTPUT / "ineligible.png",
                write_still=True,
            )
        )
    finally:
        threadmark_render.unregister()

    files = sorted(path.name for path in OUTPUT.iterdir() if path.is_file())
    REPORT.write_text(
        json.dumps(
            {
                "blender_version": bpy.app.version_string,
                "cases": cases,
                "worker_events": worker_events,
                "files": files,
                "handler_count_after_unregister": threadmark_render.handler_count(),
                "session_worker_after_unregister": threadmark_render._session.worker is None,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"THREADMARK_INTEGRATION_REPORT={REPORT}")


if __name__ == "__main__":
    main()
