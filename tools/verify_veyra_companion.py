# SPDX-License-Identifier: GPL-3.0-or-later
"""Real one-process Bake/VEYRA/Bake IPC and worker smoke test."""

from __future__ import annotations

import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.bake.transport import EnterBakeMode, LocalSocketServer
from cloth_next.veyra.artifacts import SessionArtifacts
from cloth_next.veyra.model import CompanionMode, RepairArtifact


def wait(server, wanted, timeout=20.0):
    end = time.monotonic() + timeout
    seen = []
    while time.monotonic() < end:
        item = server.poll_request()
        if item is None:
            time.sleep(.02); continue
        kind = item["type"] if isinstance(item, dict) else item
        seen.append(kind)
        if kind == wanted:
            return item, seen
    raise RuntimeError(f"timed out waiting for {wanted}; saw {seen}")


def window_title(pid):
    titles = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                      ctypes.c_void_p)
    def visit(hwnd, _lparam):
        owner = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value: titles.append(buffer.value)
        return True
    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return next((title for title in titles if title.startswith("Cloth NeXt")), "")


def basic_input(job_id):
    face = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))
    return {"schema": "cnx.veyra.input.v1", "job_id": job_id,
            "source_snapshot_identity": "a" * 64,
            "desired_separation": .01, "pairs": [],
            "degenerate_faces": [{"object_uuid": "cloth",
                "vertex_indices": [0, 1, 2], "vertices": face,
                "source_polygon_index": 0}],
            "validation_triangles": [{"keys": [["cloth", 0], ["cloth", 1],
                                                  ["cloth", 2]],
                                      "vertices": face}]}


def cancellation_input(job_id, count=25_000):
    value = basic_input(job_id)
    template = value["degenerate_faces"][0]
    value["degenerate_faces"] = [dict(template, source_polygon_index=index)
                                 for index in range(count)]
    return value


def main():
    executable = ROOT / "companion/dist/Cloth NeXt Bake.exe"
    if not executable.is_file():
        raise SystemExit("development Companion is missing")
    with tempfile.TemporaryDirectory(prefix="cnx-veyra-smoke-") as temporary:
        store = SessionArtifacts(temporary)
        server = LocalSocketServer()
        process = subprocess.Popen([
            str(executable), "--port", str(server.port), "--token", server.token,
            "--session-root", temporary])
        try:
            wait(server, "ready")
            launcher_pid = process.pid
            server.enter_bake_mode(EnterBakeMode("bake-before", 1, 1, 2, "Bake"))
            ready, _ = wait(server, "bake_window_ready")
            pid = int(ready["payload"]["companion_process_id"])
            time.sleep(.1)
            bake_title = window_title(pid)
            job_id = "veyra-smoke"
            artifact = store.write_json(
                schema="cnx.veyra.input.v1", job_id=job_id,
                name=f"{job_id}.input.json", value=basic_input(job_id))
            started = time.monotonic()
            server.enter_bake_mode(EnterBakeMode(
                job_id, 1, 1, 1, "Veyra", mode=CompanionMode.VEYRA,
                input_artifact=artifact.to_dict()))
            ready, _ = wait(server, "bake_window_ready")
            if int(ready["payload"]["companion_process_id"]) != pid:
                raise RuntimeError("VEYRA started a second Companion process")
            time.sleep(.1)
            veyra_title = window_title(pid)
            result, steps = wait(server, "veyra_result")
            planning_seconds = time.monotonic() - started
            output = RepairArtifact.from_dict(result["payload"]["artifact"])
            store.read_json(output, schema="cnx.veyra.plan.v1", job_id=job_id)
            cancel_job = "veyra-cancel"
            cancel_artifact = store.write_json(
                schema="cnx.veyra.input.v1", job_id=cancel_job,
                name=f"{cancel_job}.input.json",
                value=cancellation_input(cancel_job))
            server.enter_bake_mode(EnterBakeMode(
                cancel_job, 1, 1, 1, "Veyra", mode=CompanionMode.VEYRA,
                input_artifact=cancel_artifact.to_dict()))
            wait(server, "bake_window_ready")
            cancel_started = time.monotonic()
            server.cancel_veyra(cancel_job)
            wait(server, "veyra_cancelled")
            cancel_seconds = time.monotonic() - cancel_started
            server.enter_bake_mode(EnterBakeMode("bake-after", 1, 1, 2, "Bake"))
            ready, _ = wait(server, "bake_window_ready")
            if int(ready["payload"]["companion_process_id"]) != pid:
                raise RuntimeError("Bake restart changed Companion process")
            time.sleep(.1)
            bake_after_title = window_title(pid)
            print({"launcher_pid": launcher_pid, "pid_before": pid,
                   "pid_after": int(ready["payload"]["companion_process_id"]),
                   "reused": int(ready["payload"]["companion_process_id"]) == pid,
                   "bake_title": bake_title, "veyra_title": veyra_title,
                   "bake_after_title": bake_after_title,
                   "planning_seconds": planning_seconds,
                   "cancel_seconds": cancel_seconds,
                   "events": steps})
            if (bake_title != "Cloth NeXt Bake"
                    or veyra_title != "Cloth NeXt Veyra"
                    or bake_after_title != "Cloth NeXt Bake"):
                raise RuntimeError("Companion title switch failed")
            server.shutdown_companion(); process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=3)
            server.close()


if __name__ == "__main__":
    main()
