"""Interactive Blender smoke for the packaged companion readiness gate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import bpy


def main() -> None:
    argv=sys.argv[sys.argv.index("--")+1:]
    package_root=Path(argv[0]); result_path=Path(argv[1])
    mode=argv[2] if len(argv)>2 else "success"
    sys.path.insert(0,str(package_root))
    import cloth_next
    cloth_next.register()
    from cloth_next.bake.controller import shared_controller
    from cloth_next.bake.status import BakeState, BakeJobKind
    from cloth_next.bake.transport import EnterBakeMode
    from cloth_next.blender import companion_manager, modal_lock

    if mode == "hidden":
        os.environ["CLOTH_NEXT_COMPANION_TEST_MODE"]="hidden"
    elif mode == "missing":
        def missing(*_args,**_kwargs):
            raise OSError("controlled missing companion fixture")
        companion_manager.validate_bundle=missing

    job=shared_controller.transition(BakeState.PREPARING,
        job_kind=BakeJobKind.BAKE,status_message="Packaged startup smoke").job_id
    request=EnterBakeMode(job,os.getpid(),1,30,"Cotton")
    ok,message=companion_manager.begin_bake_mode(request)
    started=time.monotonic()

    deadline=time.monotonic()+10
    payload=None
    while ok and time.monotonic()<deadline:
        companion_manager._pulse()
        state,detail=companion_manager.startup_status(job)
        if state=="READY":
            payload={"result":"PASS",
                "job_id":job,"status":state,"detail":detail,
                "elapsed":time.monotonic()-started,
                "modal_lock_active":modal_lock.active()}
            result_path.write_text(json.dumps(payload,indent=2),encoding="utf-8")
            time.sleep(3.0)  # external harness observes the real HWND
            break
        if state in {"ERROR","CANCELLED"} or time.monotonic()-started>10:
            payload={"result":"FAIL","job_id":job,"status":state,
                     "detail":detail,"launch_ok":ok,"launch_message":message}
            break
        time.sleep(.05)
    if payload is None:
        state,detail=companion_manager.startup_status(job)
        payload={"result":"FAIL","job_id":job,"status":state,
                 "detail":detail,"launch_ok":ok,
                 "launch_message":message}
    payload["modal_lock_active"]=modal_lock.active()
    result_path.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    companion_manager.shutdown(); modal_lock.release(); cloth_next.unregister()


main()
