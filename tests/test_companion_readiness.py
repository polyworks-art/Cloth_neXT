from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.bake.transport import (BakeWindowReady, EnterBakeMode,
                                        decode_message, encode_message)
from cloth_next.bake.status import BakeState


def request(job="job"):
    return EnterBakeMode(job, 100, 20, 30, "Cotton")


def ready(job="job", **changes):
    values=dict(job_id=job, companion_process_id=200,
                window_created=True, window_visible=True,
                topmost_applied=True, transport_ready=True)
    values.update(changes)
    return values


def reset(manager):
    manager._pending_request=None; manager._pending_deadline=None
    manager._ready=None; manager._startup_error=""
    manager._owned_for_attempt=False


def test_typed_readiness_protocol_round_trip():
    payload=ready()
    decoded=decode_message(encode_message("bake_window_ready","token",
                                          payload=payload).rstrip(b"\n"),"token")
    assert BakeWindowReady(**decoded["payload"]).ready


def test_popen_or_process_alive_is_not_readiness(blender_env, monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    reset(manager)
    fake_server=SimpleNamespace(connected=lambda:False)
    monkeypatch.setattr(manager,"_launch",lambda:(True,"created",True))
    monkeypatch.setattr(manager,"_server",fake_server)
    assert manager.begin_bake_mode(request())[0]
    assert manager.startup_status("job")[0] == "WAITING"


def test_matching_ready_only_and_hidden_or_non_topmost_rejected(blender_env,
                                                                 monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    reset(manager); manager._pending_request=request()
    manager._handle_ready(ready("stale"))
    assert manager.startup_status("job")[0] == "WAITING"
    monkeypatch.setattr(manager,"_request_close_now",lambda:None)
    manager._owned_for_attempt=False
    manager._handle_ready(ready(window_visible=False))
    assert manager.startup_status("job")[0] == "ERROR"
    reset(manager); manager._pending_request=request()
    manager._handle_ready(ready(topmost_applied=False))
    assert manager.startup_status("job")[0] == "ERROR"


def test_matching_ready_permits_gate_and_stale_after_cancel_does_nothing(
        blender_env, monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    reset(manager); manager._pending_request=request()
    manager._handle_ready(ready())
    assert manager.startup_status("job")[0] == "READY"
    assert manager.consume_ready("job")
    reset(manager); manager._pending_request=request()
    monkeypatch.setattr(manager,"_request_close_now",lambda:None)
    manager.cancel_startup("job")
    manager._handle_ready(ready())
    assert manager._ready is None


def test_modal_lock_requires_matching_ready_token_and_release_is_idempotent(
        blender_env):
    lock=__import__("cloth_next.blender.modal_lock",fromlist=["x"])
    lock.release()
    assert not lock.acquire("job",companion_ready_job_id="stale")
    assert lock.acquire("job",companion_ready_job_id="job")
    assert lock.active("job")
    lock.release("job"); lock.release("job")
    assert not lock.active()


def test_cache_replacement_rejects_external_path_and_preserves_result(
        blender_env, tmp_path):
    env=blender_env; env.registration.register(); module=env.solver_test
    obj=env.bpy.types.Object(name="Cloth",type="MESH")
    env.bpy.data.objects[obj.name]=obj
    mod=obj.modifiers.new(module.import_result.MODIFIER_NAME,"MESH_CACHE")
    external=tmp_path.parent/"user.pc2"; external.write_bytes(b"user")
    mod.filepath=str(external)
    plan=SimpleNamespace(cloth_object_name="Cloth",
                         pc2_path=tmp_path/"cn_test_cloth_new.pc2")
    with pytest.raises(module.SceneValidationError,match="could not be removed"):
        module.prepare_cache_for_new_run(plan)
    assert external.exists() and mod in obj.modifiers
    env.registration.unregister()


def test_cache_replacement_is_object_scoped_and_idempotent(blender_env,
                                                            tmp_path):
    env=blender_env; env.registration.register(); module=env.solver_test
    cloth=env.bpy.types.Object(name="Cloth",type="MESH")
    other=env.bpy.types.Object(name="Other",type="MESH")
    env.bpy.data.objects[cloth.name]=cloth; env.bpy.data.objects[other.name]=other
    owned=tmp_path/"cn_test_cloth_old.pc2"; owned.write_bytes(b"old")
    unrelated=tmp_path/"user.pc2"; unrelated.write_bytes(b"user")
    cloth.modifiers.new(module.import_result.MODIFIER_NAME,"MESH_CACHE").filepath=str(owned)
    cloth.modifiers.new("User Cache","MESH_CACHE").filepath=str(unrelated)
    other.modifiers.new(module.import_result.MODIFIER_NAME,"MESH_CACHE").filepath=str(owned)
    plan=SimpleNamespace(cloth_object_name="Cloth",
                         pc2_path=tmp_path/"cn_test_cloth_new.pc2")
    module.prepare_cache_for_new_run(plan); module.prepare_cache_for_new_run(plan)
    assert not owned.exists() and unrelated.exists()
    assert any(m.name=="User Cache" for m in cloth.modifiers)
    assert any(m.name==module.import_result.MODIFIER_NAME for m in other.modifiers)
    env.registration.unregister()


def test_structural_ready_gate_precedes_lock_and_worker():
    source=Path("cloth_next/blender/solver_test.py").read_text(encoding="utf-8")
    gate=source.index('state != "READY"')
    acquire=source.index("modal_lock.acquire")
    worker=source.index("_worker = threading.Thread")
    # Worker helper is defined earlier, but its only production call occurs
    # inside the modal operator after acquisition.
    modal=source.index("class CLOTHNEXT_OT_bake_modal")
    call=source.index("_start_prepared_run(plan)",modal)
    assert gate < modal < acquire < call


def test_startup_reservation_never_replaces_cache_or_starts_worker(
        blender_env, monkeypatch):
    module=blender_env.solver_test
    plan=SimpleNamespace(frame_start=20,frame_end=30,
                         preset_identifier="COTTON")
    context=SimpleNamespace(preferences=SimpleNamespace(addons={
        "cloth_next":SimpleNamespace(preferences=SimpleNamespace(
            auto_launch_bake_window=True,telemetry_refresh_seconds=1.0))}))
    monkeypatch.setattr(module,"build_run_plan",lambda _context:plan)
    calls=[]
    monkeypatch.setattr(module,"prepare_cache_for_new_run",
                        lambda _plan:calls.append("cache"))
    monkeypatch.setattr(module,"_start_prepared_run",
                        lambda _plan:calls.append("worker"))
    monkeypatch.setattr(module.companion_manager,"begin_bake_mode",
                        lambda _request:(True,"created"))
    _job,waiting=module.begin_production_bake(context)
    assert waiting and calls == [] and not module.modal_lock.active()
    module.cancel_pending_startup(); module.shared_controller.reset()


def test_cache_deletion_failure_after_ready_prevents_worker_and_releases_lock(
        blender_env, monkeypatch):
    module=blender_env.solver_test
    plan=SimpleNamespace()
    module._pending_plan=plan; module._pending_job_id="job"
    module.shared_controller.transition(BakeState.PREPARING,job_id="job")
    module.shared_controller.transition(BakeState.STARTING_COMPANION)
    module.shared_controller.transition(BakeState.WAITING_FOR_COMPANION)
    module.shared_controller.transition(BakeState.COMPANION_READY)
    monkeypatch.setattr(module,"prepare_cache_for_new_run",lambda _plan:
        (_ for _ in ()).throw(module.SceneValidationError(
            "The previous Cloth NeXt cache could not be removed. Rebake was not started.")))
    monkeypatch.setattr(module,"_start_prepared_run",lambda _plan:
                        (_ for _ in ()).throw(AssertionError("worker started")))
    manager=SimpleNamespace(event_timer_add=lambda *_a,**_k:object(),
                            event_timer_remove=lambda _t:None,
                            modal_handler_add=lambda _o:None)
    operator=module.CLOTHNEXT_OT_bake_modal(); operator.job_id="job"
    result=operator.invoke(SimpleNamespace(window_manager=manager,window=None),None)
    assert result == {"CANCELLED"} and not module.modal_lock.active()
    assert module._worker is None
    module.shared_controller.reset()
