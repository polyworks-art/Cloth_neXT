"""Real Blender + packaged Companion terminal-shutdown smoke."""
from __future__ import annotations

import json
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time

import bpy


def _command(*args: str, timeout: float | None = None) -> str:
    return subprocess.run(
        args, check=True, text=True, capture_output=True,
        timeout=timeout).stdout.strip()


def _window_id(title: str, *, wait: bool = False) -> str:
    search = ["xdotool", "search"]
    if wait:
        search.append("--sync")
    values = _command(
        *search, "--onlyvisible", "--name", title,
        timeout=5.0 if wait else None).splitlines()
    if not values:
        raise RuntimeError(f"visible X11 window not found: {title}")
    return values[-1]


def _geometry(window_id: str) -> dict[str, int]:
    values = {}
    for line in _command("xdotool", "getwindowgeometry", "--shell", window_id).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key in {"X", "Y", "WIDTH", "HEIGHT"}:
                values[key.lower()] = int(value)
    return values


def _real_wm_exercise(blender_window: str) -> dict:
    companion = _window_id("^Cloth NeXt Bake$")
    compact = _geometry(companion)
    info = _command("xwininfo", "-id", companion)
    if "Map State: IsViewable" not in info:
        raise RuntimeError(f"Companion is not viewable:\n{info}")
    if compact.get("width", 0) < 300 or compact.get("height", 0) < 80:
        raise RuntimeError(f"invalid compact Companion geometry: {compact}")

    probe = subprocess.Popen(
        ["xmessage", "-geometry", "+20+500", "-title",
         "Cloth NeXt WM Probe", "WM focus probe"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        probe_window = _window_id("^Cloth NeXt WM Probe$", wait=True)
        _command("xdotool", "mousemove", "--window", probe_window,
                 "20", "20", "click", "1", timeout=5.0)
        probe_received_focus = _command("xdotool", "getactivewindow") == probe_window
        _command("xdotool", "mousemove", "--window", blender_window,
                 "20", "20", "click", "1", timeout=5.0)
        blender_regained_focus = _command("xdotool", "getactivewindow") == blender_window
        if not probe_received_focus or not blender_regained_focus:
            raise RuntimeError(
                "Openbox focus cycle failed: "
                f"probe={probe_received_focus}, blender={blender_regained_focus}")

        # Exercise the actual installed Details control after both focus changes.
        _command("xdotool", "mousemove", "--window", companion, "45",
                 str(compact["height"] - 16), "click", "1")
        deadline = time.monotonic() + 3.0
        expanded = _geometry(companion)
        while expanded["height"] <= compact["height"] and time.monotonic() < deadline:
            time.sleep(0.05)
            expanded = _geometry(companion)
        if expanded["height"] <= compact["height"]:
            raise RuntimeError(
                f"Details did not expand: compact={compact}, expanded={expanded}")
        if "Map State: IsViewable" not in _command("xwininfo", "-id", companion):
            raise RuntimeError("Companion stopped being viewable after focus exercise")
        return {
            "window_id": companion,
            "compact_geometry": compact,
            "expanded_geometry": expanded,
            "mapped_viewable": True,
            "other_window_received_focus": probe_received_focus,
            "blender_regained_focus": blender_regained_focus,
            "companion_remained_usable": True,
        }
    finally:
        probe.terminate()
        try:
            probe.wait(timeout=3)
        except subprocess.TimeoutExpired:
            probe.kill()


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    package_root, result_path = Path(argv[0]), Path(argv[1])
    installed = "--installed" in argv[2:]
    real_wm = "--real-wm" in argv[2:]
    blender_window = _command("xdotool", "getactivewindow") if real_wm else ""
    if installed:
        module_name = "bl_ext.user_default.cloth_next"
        try:
            cloth_next = importlib.import_module(module_name)
        except ModuleNotFoundError:
            import addon_utils
            cloth_next = addon_utils.enable(module_name, default_set=False)
            if cloth_next is None:
                raise RuntimeError(f"could not enable installed {module_name}")
    else:
        sys.path.insert(0, str(package_root.parent))
        cloth_next = importlib.import_module(package_root.name)
    cloth_next.register()
    shared_controller = importlib.import_module(
        cloth_next.__name__ + ".bake.controller").shared_controller
    status = importlib.import_module(cloth_next.__name__ + ".bake.status")
    BakeJobKind, BakeState = status.BakeJobKind, status.BakeState
    EnterBakeMode = importlib.import_module(
        cloth_next.__name__ + ".bake.transport").EnterBakeMode
    companion_manager = importlib.import_module(
        cloth_next.__name__ + ".blender.companion_manager")

    job = shared_controller.transition(
        BakeState.PREPARING, job_kind=BakeJobKind.BAKE,
        status_message="Companion shutdown smoke").job_id
    ok, message = companion_manager.begin_bake_mode(
        EnterBakeMode(job, os.getpid(), 1, 3, "Shutdown smoke"))
    deadline = time.monotonic() + 15.0
    ready = False
    while ok and time.monotonic() < deadline:
        companion_manager._pulse()
        state, _detail = companion_manager.startup_status(job)
        if state == "READY":
            ready = True
            companion_manager.consume_ready(job)
            break
        if state in {"ERROR", "CANCELLED"}:
            break
        time.sleep(0.05)

    if ready:
        wm_result = _real_wm_exercise(blender_window) if real_wm else None
        for state in (BakeState.EXPORTING, BakeState.STARTING_SOLVER,
                      BakeState.SIMULATING, BakeState.IMPORTING,
                      BakeState.FINISHED):
            shared_controller.transition(state)
        while companion_manager.running() and time.monotonic() < deadline:
            companion_manager._pulse()
            time.sleep(0.05)

    payload = {
        "result": "PASS" if ready and not companion_manager.running() else "FAIL",
        "ready": ready,
        "startup_state": companion_manager.startup_status(job)[0],
        "startup_detail": companion_manager.startup_status(job)[1],
        "process_running_after_finished": companion_manager.running(),
        "terminal_state": shared_controller.snapshot().state.value,
        "launch_ok": ok,
        "launch_message": message,
        "real_wm": wm_result if ready else None,
    }
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    companion_manager.shutdown()
    cloth_next.unregister()
    if payload["result"] != "PASS":
        raise RuntimeError(json.dumps(payload, sort_keys=True))
    if real_wm:
        bpy.ops.wm.quit_blender()


main()
