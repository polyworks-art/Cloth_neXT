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


def test_attach_reuses_owned_modifier(blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    old = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    old.filepath = str(tmp_path / "cn_test_cloth_old.pc2")
    module.mark_owned_playback(obj, old, old.filepath)
    path = tmp_path / "cn_test_cloth_new.pc2"
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
                          ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
                          obj.name, tmp_path, path, 1)

    module._attach_playback(plan, header)

    assert len(obj.modifiers) == 1
    assert obj.modifiers[0] is old
    assert old.filepath == str(path)


def test_pump_exception_becomes_terminal_error(blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    module.shared_controller.transition(BakeState.PREPARING)
    module._active_plan = SimpleNamespace()
    module._worker = SimpleNamespace(is_alive=lambda: False)
    monkeypatch.setattr(module, "_pump_once",
                        lambda: (_ for _ in ()).throw(TypeError("attach boom")))

    assert module._pump() is None
    assert module._active_plan is None
    assert module._worker is None
    snapshot = module.shared_controller.snapshot()
    assert snapshot.state is BakeState.ERROR
    assert "attach boom" in snapshot.error_details

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
