# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Blender file-load/unregister crash reproducer.

Run from Blender with ``--`` followed by ``--repo``, ``--variant``, ``--mode``
and ``--report``.  Each lifecycle event is flushed to JSONL immediately so the
last completed operation survives a native Blender crash.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import bpy
from bpy.app.handlers import persistent


def _args() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--variant", choices=tuple("ABCDEFGH"), required=True)
    parser.add_argument(
        "--mode",
        choices=("cloth_next", "minimal", "minimal_many", "none"),
        required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--trace-unregister", action="store_true")
    return parser.parse_args(values)


ARGS = _args()
ARGS.report.parent.mkdir(parents=True, exist_ok=True)


def _emit(step: str, **fields: object) -> None:
    event = {
        "time": time.time(),
        "step": step,
        "variant": ARGS.variant,
        "mode": ARGS.mode,
        **fields,
    }
    with ARGS.report.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
    print("CLOTH_NEXT_CRASH_REPRO", json.dumps(event, ensure_ascii=False),
          flush=True)


class _MinimalSettings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(default=False)
    role: bpy.props.StringProperty(default="")


class _MinimalOperator(bpy.types.Operator):
    bl_idname = "wm.cloth_next_minimal_reproducer"
    bl_label = "Cloth NeXt Minimal Reproducer"

    def execute(self, _context):
        return {"FINISHED"}


def _minimal_execute(self, _context):
    return {"FINISHED"}


_MINIMAL_MANY_OPERATORS = tuple(
    type(
        f"CNX_OT_minimal_reproducer_{index:03d}",
        (bpy.types.Operator,),
        {
            "__module__": __name__,
            "bl_idname": f"wm.cnx_minimal_reproducer_{index:03d}",
            "bl_label": f"CNX Minimal Reproducer {index:03d}",
            "execute": _minimal_execute,
        },
    )
    for index in range(143)
)


@persistent
def _minimal_load_post(_unused) -> None:
    return None


def _minimal_timer() -> None:
    return None


def _register_minimal(*, many: bool = False) -> None:
    bpy.utils.register_class(_MinimalSettings)
    classes = _MINIMAL_MANY_OPERATORS if many else (_MinimalOperator,)
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.cloth_next_minimal = bpy.props.PointerProperty(
        type=_MinimalSettings)
    bpy.app.handlers.load_post.append(_minimal_load_post)
    bpy.app.timers.register(_minimal_timer, first_interval=3600.0)


def _unregister_minimal(*, many: bool = False) -> None:
    if bpy.app.timers.is_registered(_minimal_timer):
        bpy.app.timers.unregister(_minimal_timer)
    while _minimal_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_minimal_load_post)
    if hasattr(bpy.types.Object, "cloth_next_minimal"):
        del bpy.types.Object.cloth_next_minimal
    classes = _MINIMAL_MANY_OPERATORS if many else (_MinimalOperator,)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.utils.unregister_class(_MinimalSettings)


def _register() -> object | None:
    if ARGS.mode == "cloth_next":
        sys.path.insert(0, str(ARGS.repo))
        from cloth_next.blender import registration

        registration.register()
        return registration
    if ARGS.mode in {"minimal", "minimal_many"}:
        _register_minimal(many=ARGS.mode == "minimal_many")
    return None


def _unregister(registration: object | None) -> None:
    if ARGS.mode == "cloth_next":
        original = bpy.utils.unregister_class
        if ARGS.trace_unregister:
            def traced(cls):
                _emit("unregister_class_start", class_name=cls.__name__)
                original(cls)
                _emit("unregister_class_finished", class_name=cls.__name__)

            bpy.utils.unregister_class = traced
        try:
            registration.unregister()
        finally:
            bpy.utils.unregister_class = original
    elif ARGS.mode in {"minimal", "minimal_many"}:
        _unregister_minimal(many=ARGS.mode == "minimal_many")


def _set_state(obj: bpy.types.Object, role: str) -> None:
    if ARGS.mode == "cloth_next":
        obj.cloth_next.enabled = True
        obj.cloth_next.role = role
    elif ARGS.mode in {"minimal", "minimal_many"}:
        obj.cloth_next_minimal.enabled = True
        obj.cloth_next_minimal.role = role


def _save(path: Path, label: str) -> None:
    _emit(f"{label}_start")
    result = bpy.ops.wm.save_as_mainfile(
        filepath=str(path), check_existing=False)
    assert result == {"FINISHED"}
    _emit(f"{label}_finished")


def _open(path: Path, label: str) -> None:
    _emit(f"{label}_start")
    result = bpy.ops.wm.open_mainfile(filepath=str(path))
    assert result == {"FINISHED"}
    _emit(f"{label}_finished")


def _delete_collider() -> None:
    collider = bpy.data.objects["Lifecycle Collider"]
    bpy.ops.object.select_all(action="DESELECT")
    collider.select_set(True)
    bpy.context.view_layer.objects.active = collider
    _emit("delete_collider_start")
    assert bpy.ops.object.delete(use_global=False) == {"FINISHED"}
    del collider
    _emit("delete_collider_finished")


def main() -> None:
    _emit("process_started", blender=bpy.app.version_string)
    registration = _register()
    _emit("register_finished")
    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_plane_add()
        cloth = bpy.context.object
        cloth.name = "Lifecycle Cloth"
        _set_state(cloth, "CLOTH")
        bpy.ops.mesh.primitive_cube_add()
        collider = bpy.context.object
        collider.name = "Lifecycle Collider"
        _set_state(collider, "COLLIDER")
        del cloth, collider
        _emit("scene_created")

        with tempfile.TemporaryDirectory(
                prefix="cloth next unregister reproducer ") as raw:
            blend = Path(raw) / "lifecycle.blend"
            variant = ARGS.variant

            if variant == "A":
                _delete_collider()
            elif variant == "B":
                _save(blend, "save_1")
                _open(blend, "open_1")
            elif variant == "C":
                _delete_collider()
                _save(blend, "save_1")
            elif variant == "D":
                _save(blend, "save_1")
                _delete_collider()
                _open(blend, "open_1")
            elif variant == "E":
                _save(blend, "save_1")
                _open(blend, "open_1")
                _delete_collider()
                _save(blend, "save_2")
                _open(blend, "open_2")
            elif variant == "F":
                _save(blend, "save_1")
                _open(blend, "open_1")
                _save(blend, "save_2")
                _open(blend, "open_2")
            elif variant == "G":
                _save(blend, "save_1")
                _open(blend, "open_1")
                _delete_collider()
                _save(blend, "save_2")
                _open(blend, "open_2")
            elif variant == "H":
                _save(blend, "save_1")
                _open(blend, "open_1")

        if ARGS.variant == "G":
            _emit("exit_without_unregister")
            registration = None
            return
        _emit("unregister_start")
        _unregister(registration)
        registration = None
        _emit("unregister_finished")
    finally:
        if registration is not None:
            _emit("unregister_cleanup_start")
            _unregister(registration)
            _emit("unregister_cleanup_finished")


if __name__ == "__main__":
    main()
