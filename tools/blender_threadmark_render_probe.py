# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender 5.x to measure render lifecycle semantics for ThreadMark."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(values) != 2:
        raise SystemExit("expected -- OUTPUT_DIR REPORT_JSON")
    return Path(values[0]), Path(values[1])


OUTPUT, REPORT = _args()
OUTPUT.mkdir(parents=True, exist_ok=True)
events = []


def _sha256(path):
    path = Path(path)
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frame_path(scene):
    try:
        return Path(scene.render.frame_path(frame=scene.frame_current))
    except Exception:
        return Path(scene.render.filepath)


def _output_files():
    return [
        {"name": path.name, "sha256": _sha256(path)}
        for path in sorted(OUTPUT.iterdir())
        if path.is_file()
    ]


def _image_details(image):
    if image is None:
        return {}
    try:
        pixel_count = len(image.pixels)
    except Exception:
        pixel_count = None
    slots = getattr(image, "render_slots", ())
    views = getattr(image, "views", ())
    return {
        "render_result_size": list(getattr(image, "size", ())),
        "render_result_type": getattr(image, "type", None),
        "render_result_has_data": bool(getattr(image, "has_data", False)),
        "render_result_pixel_count": pixel_count,
        "render_result_has_layers_attribute": hasattr(image, "layers"),
        "render_slots": [getattr(slot, "name", "") for slot in slots],
        "views": [getattr(view, "name", "") for view in views],
    }


def _record(name, scene):
    path = _frame_path(scene)
    image = bpy.data.images.get("Render Result")
    event = {
        "event": name,
        "frame": scene.frame_current,
        "engine": scene.render.engine,
        "file": path.name,
        "file_exists": path.is_file(),
        "file_sha256": _sha256(path),
        "output_files": _output_files(),
        "render_result": image is not None,
        "scene_view_layers": [layer.name for layer in scene.view_layers],
        "time": time.monotonic(),
    }
    try:
        event.update(_image_details(image))
    except Exception as exc:
        event["metadata_error"] = f"{type(exc).__name__}: {exc}"
    events.append(event)


def render_pre(scene, *_args):
    _record("render_pre", scene)


def render_post(scene, *_args):
    _record("render_post", scene)


def render_write(scene, *_args):
    _record("render_write", scene)


def render_complete(scene, *_args):
    _record("render_complete", scene)


def render_cancel(scene, *_args):
    _record("render_cancel", scene)


HANDLERS = {
    "render_pre": render_pre,
    "render_post": render_post,
    "render_write": render_write,
    "render_complete": render_complete,
    "render_cancel": render_cancel,
}


def install_handlers():
    for slot, callback in HANDLERS.items():
        getattr(bpy.app.handlers, slot).append(callback)


def remove_handlers():
    for slot, callback in HANDLERS.items():
        handlers = getattr(bpy.app.handlers, slot)
        while callback in handlers:
            handlers.remove(callback)


def configure_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.resolution_x = 160
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.filepath = str(OUTPUT / "still.png")
    scene.render.use_file_extension = True
    scene.view_settings.look = "AgX - Medium High Contrast"
    bpy.ops.object.camera_add(location=(0, 0, 5))
    camera = bpy.context.object
    scene.camera = camera
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    cube = bpy.context.object
    material = bpy.data.materials.new("Probe material")
    material.diffuse_color = (0.8, 0.15, 0.04, 1.0)
    cube.data.materials.append(material)
    world = bpy.data.worlds.new("Probe world")
    world.color = (0.04, 0.08, 0.16)
    scene.world = world
    return scene


def render_result_probe(scene):
    result = {"pixels_readable": False, "same_value_write": False}
    image = bpy.data.images.get("Render Result")
    if image is None:
        return result
    try:
        result["pixel_count"] = len(image.pixels)
        sample = tuple(image.pixels[:4])
        result["pixels_readable"] = len(sample) == 4
        result["first_pixel"] = list(sample)
        if sample:
            image.pixels[0] = sample[0]
            result["same_value_write"] = True
    except Exception as exc:
        result["pixel_error"] = f"{type(exc).__name__}: {exc}"
    before = OUTPUT / "manual-save-before.png"
    try:
        image.save_render(str(before), scene=scene)
        result["save_render"] = {"path": before.name, "sha256": _sha256(before)}
    except Exception as exc:
        result["save_render_error"] = f"{type(exc).__name__}: {exc}"
    return result


def run_case(name, operation):
    start = len(events)
    record = {"name": name, "ok": False}
    try:
        operation()
        record["ok"] = True
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["events"] = events[start:]
    return record


def main():
    scene = configure_scene()
    install_handlers()
    cases = []
    try:
        cases.append(run_case("eevee_render_no_write", lambda: bpy.ops.render.render()))
        render_result = render_result_probe(scene)
        scene.render.filepath = str(OUTPUT / "eevee-write.png")
        cases.append(
            run_case(
                "eevee_write_still", lambda: bpy.ops.render.render(write_still=True)
            )
        )
        eevee_write = OUTPUT / "eevee-write.png"
        saved_after = OUTPUT / "manual-save-after.png"
        bpy.data.images["Render Result"].save_render(str(saved_after), scene=scene)

        scene.render.filepath = str(OUTPUT / "sequence-")
        scene.frame_start = 1
        scene.frame_end = 2
        cases.append(
            run_case("eevee_animation", lambda: bpy.ops.render.render(animation=True))
        )

        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.render.filepath = str(OUTPUT / "cycles.png")
        cases.append(
            run_case("cycles_write_still", lambda: bpy.ops.render.render(write_still=True))
        )

        scene.render.engine = "BLENDER_EEVEE"
        color_management = []
        for transform in ("AgX", "Filmic", "Standard", "Khronos PBR Neutral"):
            slug = transform.lower().replace(" ", "-")
            automatic = OUTPUT / f"color-{slug}.png"
            render_result_save = OUTPUT / f"color-{slug}-render-result.png"
            try:
                scene.view_settings.view_transform = transform
                scene.view_settings.look = "None"
                scene.render.filepath = str(automatic)
                bpy.ops.render.render(write_still=True)
                bpy.data.images["Render Result"].save_render(
                    str(render_result_save), scene=scene
                )
                color_management.append(
                    {
                        "view_transform": transform,
                        "automatic_sha256": _sha256(automatic),
                        "render_result_save_sha256": _sha256(render_result_save),
                        "byte_identical": _sha256(automatic)
                        == _sha256(render_result_save),
                    }
                )
            except Exception as exc:
                color_management.append(
                    {"view_transform": transform, "error": f"{type(exc).__name__}: {exc}"}
                )

        camera = scene.camera
        scene.camera = None
        cases.append(run_case("failure_no_camera", lambda: bpy.ops.render.render()))
        scene.camera = camera

        scene.render.engine = "BLENDER_EEVEE"
        cases.append(run_case("repeat_1", lambda: bpy.ops.render.render()))
        cases.append(run_case("repeat_2", lambda: bpy.ops.render.render()))
        image = bpy.data.images.get("Render Result")
        report = {
            "blender_version": bpy.app.version_string,
            "background": bpy.app.background,
            "cases": cases,
            "render_result_probe": render_result,
            "eevee_write_sha256": _sha256(eevee_write),
            "manual_save_after_sha256": _sha256(saved_after),
            "manual_save_matches_write": _sha256(eevee_write) == _sha256(saved_after),
            "color_management": color_management,
            "final_result_size": list(image.size) if image else None,
            "final_result_alpha_mode": image.alpha_mode if image else None,
        }
    except Exception:
        report = {"fatal_error": traceback.format_exc(), "events": events}
        raise
    finally:
        remove_handlers()
        report["handler_counts_after_remove"] = {
            slot: sum(item is callback for item in getattr(bpy.app.handlers, slot))
            for slot, callback in HANDLERS.items()
        }
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"THREADMARK_RENDER_PROBE={REPORT}")


if __name__ == "__main__":
    main()
