"""Real Blender + packaged Companion terminal-shutdown smoke."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import bpy


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    package_root, result_path = Path(argv[0]), Path(argv[1])
    sys.path.insert(0, str(package_root))
    import cloth_next
    cloth_next.register()
    from cloth_next.bake.controller import shared_controller
    from cloth_next.bake.status import BakeJobKind, BakeState
    from cloth_next.bake.transport import EnterBakeMode
    from cloth_next.blender import companion_manager

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
        "process_running_after_finished": companion_manager.running(),
        "terminal_state": shared_controller.snapshot().state.value,
        "launch_ok": ok,
        "launch_message": message,
    }
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    companion_manager.shutdown()
    cloth_next.unregister()
    if payload["result"] != "PASS":
        raise RuntimeError(json.dumps(payload, sort_keys=True))


main()
