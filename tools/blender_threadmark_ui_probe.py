# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Instrument an invoked (F12-equivalent) render and Image > Save As in Blender."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import traceback

import bpy


values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
if len(values) != 2:
    raise SystemExit("expected -- OUTPUT_DIR REPORT_JSON")
OUTPUT, REPORT = map(Path, values)
OUTPUT.mkdir(parents=True, exist_ok=True)
events = []
report = {"blender_version": bpy.app.version_string, "background": bpy.app.background}
MSGBUS_OWNER = object()
manual_save_started = False


def _sha256(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _event(name):
    image = bpy.data.images.get("Render Result")
    try:
        pixel_count = len(image.pixels) if image else None
    except Exception:
        pixel_count = None
    events.append(
        {
            "event": name,
            "frame": bpy.context.scene.frame_current,
            "render_result_size": list(image.size) if image else None,
            "render_result_has_data": bool(image and image.has_data),
            "render_result_pixel_count": pixel_count,
            "render_result_filepath": getattr(image, "filepath", None),
            "render_result_filepath_raw": getattr(image, "filepath_raw", None),
        }
    )


def _pre(*_args):
    _event("render_pre")


def _post(*_args):
    _event("render_post")


def _write(*_args):
    _event("render_write")


def _complete(*_args):
    _event("render_complete")
    if not manual_save_started:
        bpy.app.timers.register(_manual_save, first_interval=0.1)


def _cancel(*_args):
    _event("render_cancel")


def _save_pre(*_args):
    _event("save_pre")


def _save_post(*_args):
    _event("save_post")


def _save_post_fail(*_args):
    _event("save_post_fail")


HANDLERS = {
    "render_pre": _pre,
    "render_post": _post,
    "render_write": _write,
    "render_complete": _complete,
    "render_cancel": _cancel,
    "save_pre": _save_pre,
    "save_post": _save_post,
    "save_post_fail": _save_post_fail,
}


def _operator_snapshot():
    result = []
    for operator in bpy.context.window_manager.operators:
        item = {"idname": operator.bl_idname}
        properties = getattr(operator, "properties", None)
        for name in ("filepath", "copy", "save_as_render"):
            if properties is not None and hasattr(properties, name):
                item[name] = getattr(properties, name)
        result.append(item)
    return result


def _render_result_paths():
    image = bpy.data.images.get("Render Result")
    return {
        "filepath": getattr(image, "filepath", None),
        "filepath_raw": getattr(image, "filepath_raw", None),
    }


def _msgbus_notify(property_name):
    report.setdefault("msgbus_notifications", []).append(
        {
            "property": property_name,
            "render_result": _render_result_paths(),
            "operators": _operator_snapshot(),
        }
    )


def _cleanup_and_quit():
    bpy.msgbus.clear_by_owner(MSGBUS_OWNER)
    for name, callback in HANDLERS.items():
        handlers = getattr(bpy.app.handlers, name)
        while callback in handlers:
            handlers.remove(callback)
    report["events"] = events
    report["handler_counts_after_remove"] = {
        name: sum(item is callback for item in getattr(bpy.app.handlers, name))
        for name, callback in HANDLERS.items()
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    bpy.ops.wm.quit_blender()


def _manual_save():
    global manual_save_started
    manual_save_started = True
    target = OUTPUT / "image-save-as.png"
    image = bpy.data.images.get("Render Result")
    owner_window = None
    area = None
    for window in bpy.context.window_manager.windows:
        for candidate in window.screen.areas:
            if candidate.type == "IMAGE_EDITOR":
                owner_window, area = window, candidate
                if candidate.spaces.active.image == image:
                    break
        if area is not None and area.spaces.active.image == image:
            break
    old_type = None
    try:
        report["before_image_save_as"] = {
            "render_result": _render_result_paths(),
            "operators": _operator_snapshot(),
        }
        for property_name in ("filepath", "filepath_raw"):
            for key_name, key in (
                ("type", (bpy.types.Image, property_name)),
                ("instance", image.path_resolve(property_name, False)),
            ):
                try:
                    bpy.msgbus.subscribe_rna(
                        key=key,
                        owner=MSGBUS_OWNER,
                        args=(f"{key_name}:{property_name}",),
                        notify=_msgbus_notify,
                    )
                except Exception as exc:
                    report.setdefault("msgbus_subscription_errors", []).append(
                        f"{key_name}:{property_name}: {type(exc).__name__}: {exc}"
                    )
        if area is None:
            area = bpy.context.screen.areas[0]
            owner_window = bpy.context.window
            old_type = area.type
            area.type = "IMAGE_EDITOR"
        area.spaces.active.image = image
        region = next(item for item in area.regions if item.type == "WINDOW")
        with bpy.context.temp_override(
            window=owner_window,
            area=area,
            region=region,
            space_data=area.spaces.active,
        ):
            report["image_save_as_context"] = {
                "poll": bpy.ops.image.save_as.poll(),
                "area_type": bpy.context.area.type,
                "space_image": getattr(bpy.context.space_data.image, "name", None),
                "edit_image": getattr(bpy.context.edit_image, "name", None),
            }
            result = bpy.ops.image.save_as(
                "EXEC_DEFAULT",
                filepath=str(target),
                save_as_render=True,
                copy=False,
            )
        report["image_save_as"] = {
            "operator_result": sorted(result),
            "exists": target.is_file(),
            "sha256": _sha256(target),
        }
        report["after_image_save_as"] = {
            "render_result": _render_result_paths(),
            "operators": _operator_snapshot(),
        }
        report["before_second_render"] = _render_result_paths()
        bpy.ops.render.render()
        report["after_second_render"] = _render_result_paths()
    except Exception:
        report["image_save_as_error"] = traceback.format_exc()
    finally:
        if old_type is not None:
            area.type = old_type
        bpy.app.timers.register(_cleanup_and_quit, first_interval=0.2)
    return None


def _start_render():
    try:
        result = bpy.ops.render.render("INVOKE_DEFAULT")
        report["invoke_result"] = sorted(result)
    except Exception:
        report["invoke_error"] = traceback.format_exc()
        _cleanup_and_quit()
    return None


def _configure():
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 160
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.view_settings.look = "AgX - Medium High Contrast"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.object.camera_add(location=(0, 0, 5))
    scene.camera = bpy.context.object
    bpy.ops.mesh.primitive_cube_add()
    material = bpy.data.materials.new("Probe material")
    material.diffuse_color = (0.8, 0.15, 0.04, 1.0)
    bpy.context.object.data.materials.append(material)
    for name, callback in HANDLERS.items():
        getattr(bpy.app.handlers, name).append(callback)
    bpy.app.timers.register(_start_render, first_interval=0.1)


_configure()
