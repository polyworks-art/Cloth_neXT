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
    assert module._queue.get_nowait()[0] == "finished"

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
