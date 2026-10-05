# SPDX-License-Identifier: GPL-3.0-or-later
from queue import Queue
from types import SimpleNamespace

import pytest

from cloth_next.bake.status import BakeState
from cloth_next.bake.transport import LocalSocketServer


def server_without_socket():
    server = LocalSocketServer.__new__(LocalSocketServer)
    server.requests = Queue(maxsize=256)
    return server


def test_cancel_bypasses_handshake_backlog_without_reordering_other_messages():
    server = server_without_socket()
    messages = ['ready', {'type': 'bake_window_ready', 'payload': {}},
                'cancel_request', 'close_notice', 'cancel_request']
    for message in messages:
        server.requests.put_nowait(message)
    assert server.consume_cancel_request()
    assert not server.consume_cancel_request()
    assert [server.poll_request() for _ in range(3)] == messages[:2] + ['close_notice']


@pytest.mark.parametrize('phase', ['validation', 'plan'])
def test_synchronous_preparation_cancel_never_reaches_startup(
        blender_env, monkeypatch, phase):
    module = blender_env.solver_test
    module.shared_controller.reset()
    module._cancel_event.clear()
    server = server_without_socket()
    monkeypatch.setattr(module.companion_manager, '_server', server)
    monkeypatch.setattr(module.companion_manager, 'ensure_running', lambda: (True, 'ready'))
    context = SimpleNamespace(scene=SimpleNamespace(objects=[object()] if phase == 'validation' else []))

    def cancelled_stage(*args, **kwargs):
        server.requests.put_nowait('cancel_request')
        module._check_preparation_cancel()
        pytest.fail('Preparation continued after Cancel')

    monkeypatch.setattr(module, 'validate_scene' if phase == 'validation' else 'build_run_plan', cancelled_stage)
    monkeypatch.setattr(module, '_continue_production_bake', lambda *args: pytest.fail('Solver startup reached'))
    with pytest.raises(module.SessionCancelled):
        module.begin_production_bake(context)
    assert module.shared_controller.snapshot().state is BakeState.CANCELLED
    assert module._worker is None and module._pending_plan is None
    assert module.modal_lock.reserve('next-attempt')
    module.modal_lock.release('next-attempt')


def test_cancel_during_companion_wait_cleans_capture_and_releases_reservation(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    module.shared_controller.reset()
    job = module._begin_controller(module.BakeJobKind.BAKE)
    module.modal_lock.reserve(job)
    module._cancel_event.clear()
    server = server_without_socket()
    monkeypatch.setattr(module.companion_manager, '_server', server)
    state = {'context': SimpleNamespace(scene=object()), 'job_id': job,
             'wait_for_companion': True, 'collider_states': {'owned': object()}}
    monkeypatch.setattr(module, '_pin_capture', state)
    monkeypatch.setattr(module, '_pending_job_id', job)
    cleaned = []
    monkeypatch.setattr(module, '_cleanup_collider_pump', lambda value: cleaned.append(value))
    monkeypatch.setattr(module, '_restore_pin_capture_state', lambda value: cleaned.append(value))
    server.requests.put_nowait('cancel_request')
    assert module._pin_capture_pump() is None
    assert cleaned == [state['collider_states'], state]
    assert module._pin_capture is None and not module._pending_job_id
    assert module.shared_controller.snapshot().state is BakeState.CANCELLED
    assert module.modal_lock.reserve('next-attempt')
    module.modal_lock.release('next-attempt')


def test_controller_cancel_latches_before_worker_exists(blender_env):
    module = blender_env.solver_test
    module._cancel_event.clear()
    module._on_controller_snapshot(SimpleNamespace(state=BakeState.CANCELLING))
    assert module._cancel_event.is_set()


def test_cancel_at_preparation_startup_handoff_clears_pending_plan(blender_env, monkeypatch):
    module = blender_env.solver_test
    module.shared_controller.reset()
    server = server_without_socket()
    monkeypatch.setattr(module.companion_manager, '_server', server)
    plan = SimpleNamespace(frame_start=1, frame_end=10, preset_identifier='DEFAULT')
    monkeypatch.setattr(module, 'build_run_plan', lambda *args, **kwargs: plan)

    def cancel_in_handshake(*args, **kwargs):
        server.requests.put_nowait('cancel_request')
        return True, 'ready'

    monkeypatch.setattr(module.companion_manager, 'begin_bake_mode', cancel_in_handshake)
    with pytest.raises(module.SessionCancelled):
        module.begin_production_bake(SimpleNamespace(scene=SimpleNamespace(objects=[])))
    assert module.shared_controller.snapshot().state is BakeState.CANCELLED
    assert module._pending_plan is None and not module._pending_job_id
    assert module._worker is None
    assert module.modal_lock.reserve('next-attempt')
    module.modal_lock.release('next-attempt')
