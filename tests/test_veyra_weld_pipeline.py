from types import SimpleNamespace

import pytest


class Transaction:
    def __init__(self):
        self.accepted = 0
        self.rolled_back = 0

    def accept(self):
        self.accepted += 1

    def rollback(self):
        self.rolled_back += 1


def pending(solver_test, before=10):
    session = solver_test._VeyraRegionSession(
        initial_count=before, baseline_count=before,
        baseline_result=SimpleNamespace())
    transaction = Transaction()
    plan = SimpleNamespace(
        object_uuid="cloth", tolerance=1e-9, local_scale=1.0,
        clusters=((1, 2),), merged_vertices=1, skip_reasons={})
    obj = SimpleNamespace(name="Shorts")
    session.pending_weld = (obj, plan, transaction)
    return session, transaction


@pytest.mark.parametrize(("after", "accepted"), [(9, True), (10, False), (11, False)])
def test_authoritative_weld_acceptance_is_strict(
        blender_env, monkeypatch, after, accepted):
    module = blender_env.solver_test
    session, transaction = pending(module)
    scheduled = []
    cleared = []
    monkeypatch.setattr(module, "_veyra_region_session", session)
    monkeypatch.setattr(module, "_schedule_veyra_region_pass",
                        lambda: scheduled.append(True))
    monkeypatch.setattr(module, "clear_topology_cache", cleared.append)
    monkeypatch.setattr(module, "_restore_veyra_region_baseline", lambda _s: None)
    monkeypatch.setattr(module.validation_state, "forget", lambda _obj: None)

    module._handle_veyra_contact_result(SimpleNamespace(detected_count=after))

    assert transaction.accepted == int(accepted)
    assert transaction.rolled_back == int(not accepted)
    assert session.baseline_count == (after if accepted else 10)
    assert session.pending_weld is None
    assert scheduled == [True]
    assert cleared == [""]


def test_accepted_weld_zero_early_exits_without_region_plan(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    session, transaction = pending(module)
    scheduled = []
    monkeypatch.setattr(module, "_veyra_region_session", session)
    monkeypatch.setattr(module, "_schedule_veyra_region_pass",
                        lambda: scheduled.append(True))
    monkeypatch.setattr(module, "clear_topology_cache", lambda _job: None)

    module._handle_veyra_contact_result(SimpleNamespace(detected_count=0))

    assert transaction.accepted == 1
    assert session.termination_reason == "SUCCESS"
    assert not session.active
    assert scheduled == []


def test_cancel_rolls_back_topology_and_invalidates_caches(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    session, transaction = pending(module)
    forgotten = []
    cleared = []
    monkeypatch.setattr(module.validation_state, "forget", forgotten.append)
    monkeypatch.setattr(module, "clear_topology_cache", cleared.append)
    monkeypatch.setattr(module, "_restore_veyra_region_baseline", lambda _s: None)

    module._rollback_veyra_topology_candidate(session)

    assert transaction.rolled_back == 1
    assert session.pending_weld is None
    assert len(forgotten) == 1
    assert cleared == [""]


def test_no_safe_weld_keeps_existing_region_pipeline(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    session = module._VeyraRegionSession()
    monkeypatch.setattr(module, "_veyra_safe_weld_transaction",
                        lambda _context, _result: None)
    result = SimpleNamespace(has_intersections=True)
    assert not module._try_start_veyra_safe_weld(session, result)
    assert session.weld_attempted
    assert session.weld_metrics == {"planned": False}
