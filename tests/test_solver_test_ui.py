# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Threading, cancellation, and cleanup contracts for the Blender bridge."""
from __future__ import annotations
import threading
from types import SimpleNamespace
from cloth_next.bake.status import BakeState

def test_worker_never_accesses_bpy(blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    main_ident = threading.get_ident()
    class Guard:
        def __getattr__(self, name):
            assert threading.get_ident() == main_ident, f"worker touched bpy.{name}"
            return getattr(blender_env.bpy, name)
    module.bpy = Guard()
    class StubSession:
        def __init__(self, **kwargs): self.frame_sink = kwargs["frame_sink"]
        def run(self): return SimpleNamespace()
    monkeypatch.setattr(module, "SolverSession", StubSession)
    monkeypatch.setattr(module.import_result, "build_playback_frames",
                        lambda *args, **kwargs: (((0.0, 0.0, 0.0),),))
    monkeypatch.setattr(module.import_result, "write_playback_cache",
                        lambda *args: SimpleNamespace(vertex_count=1, frame_count=1))
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), ((0.0, 0.0, 0.0),),
                          ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
                          "cloth", tmp_path, tmp_path / "x.pc2", 1)
    thread = threading.Thread(target=module._worker_main, args=(plan,))
    thread.start(); thread.join(2)
    assert not thread.is_alive()
    messages = []
    while not module._queue.empty():
        messages.append(module._queue.get_nowait()[0])
    assert messages[-1] == "finished"

def test_companion_cancelling_snapshot_sets_worker_event(blender_env):
    module = blender_env.solver_test
    module._cancel_event.clear()
    module._worker = SimpleNamespace(is_alive=lambda: True)
    module._on_controller_snapshot(SimpleNamespace(state=BakeState.CANCELLING))
    assert module._cancel_event.is_set()
    module._worker = None

def test_unregister_clears_solver_worker_timer_and_subscription(blender_env):
    module = blender_env.solver_test
    blender_env.registration.register()
    module._unsubscribe = lambda: None
    blender_env.bpy.app.timers.register(module._pump)
    blender_env.registration.unregister()
    assert module._worker is None
    assert module._unsubscribe is None
    assert not blender_env.bpy.app.timers.is_registered(module._pump)

def test_pump_exception_becomes_visible_terminal_error(blender_env, monkeypatch,
                                                       tmp_path):
    module = blender_env.solver_test
    controller = module.shared_controller
    if controller.snapshot().state is not BakeState.IDLE:
        controller.reset()
    controller.transition(BakeState.PREPARING)
    controller.transition(BakeState.EXPORTING)
    controller.transition(BakeState.STARTING_SOLVER)
    controller.transition(BakeState.SIMULATING)
    controller.transition(BakeState.IMPORTING)
    module._active_plan = SimpleNamespace(pc2_path=tmp_path / "result.pc2")
    module._worker = SimpleNamespace(is_alive=lambda: False)
    monkeypatch.setattr(module, "_pump_once",
                        lambda: (_ for _ in ()).throw(TypeError("attach boom")))

    assert module._pump() is None
    snapshot = controller.snapshot()
    assert snapshot.state is BakeState.ERROR
    assert "TypeError: attach boom" in snapshot.error_details
    assert module._active_plan is None
    assert module._worker is None

def test_pump_watchdog_restores_missing_timer(blender_env):
    module = blender_env.solver_test
    module._active_plan = SimpleNamespace()
    timers = blender_env.bpy.app.timers
    if timers.is_registered(module._pump):
        timers.unregister(module._pump)

    assert module._pump_watchdog() == 0.5
    assert timers.is_registered(module._pump)
    module._active_plan = None
    timers.unregister(module._pump)

def test_run_operator_reports_optional_companion_warning(blender_env, monkeypatch):
    module=blender_env.solver_test
    monkeypatch.setattr(module,"start_run",lambda _context, **_kw:"bundle unavailable")
    op=module.CLOTHNEXT_OT_solver_test_run()
    assert op.execute(blender_env.bpy.context)=={"FINISHED"}
    assert op.reports[-1][0]=={"WARNING"}
    assert "bundle unavailable" in op.reports[-1][1]

def test_companion_ensure_running_reuses_existing(blender_env, monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    monkeypatch.setattr(manager,"running",lambda:True)
    monkeypatch.setattr(manager,"launch",lambda: (_ for _ in ()).throw(AssertionError("duplicate")))
    assert manager.ensure_running()==(True,"Bake window reused")

def test_companion_replaces_exited_session_without_leaking(blender_env,
                                                            monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    manager._process=SimpleNamespace(poll=lambda:1)
    manager._server=SimpleNamespace()
    manager._unsubscribe=lambda:None
    calls=[]
    monkeypatch.setattr(manager,"shutdown",lambda:calls.append("shutdown"))
    monkeypatch.setattr(manager,"launch",lambda:calls.append("launch") or
                        (True,"Bake window launched"))
    assert manager.ensure_running()==(True,"Bake window launched")
    assert calls==["shutdown","launch"]
