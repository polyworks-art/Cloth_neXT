from pathlib import Path
from types import SimpleNamespace

from cloth_next.ppf.schema import envelope
from cloth_next.ppf_run.session import SessionScene


def _plan(solver_test):
    payload = [{"type": "SHELL", "object": [{
        "name": "Shorts", "uuid": "cloth", "vert": [[0.0, 0.0, 0.0]],
        "face": [[0, 0, 0]], "transform": [[1, 0, 0, 0], [0, 1, 0, 0],
                                                   [0, 0, 1, 0], [0, 0, 0, 1]],
    }]}]
    scene_blob = envelope.dumps_envelope(envelope.KIND_SCENE, payload)
    scene = SessionScene(
        "old-project", "Shorts", "cloth", 1, "", "", 2,
        scene_blob, b"params", envelope.payload_sha256(scene_blob), "param-hash")
    target = solver_test.DeformablePlan(
        ((0.0, 0.0, 0.0),),
        ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
         (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        "Shorts", "cloth", Path("cache.pc2"), "topology", {}, "CLOTH")
    return solver_test.RunPlan(
        scene, SimpleNamespace(schema_version="1"), target.initial_local,
        target.world_matrix, target.object_name, Path("work"), target.pc2_path,
        2, deformables=(target,))


def test_persistent_validation_plan_reuses_immutable_payload_state(
        blender_env, monkeypatch):
    solver_test = blender_env.solver_test
    monkeypatch.setattr(solver_test.bpy.app, "tempdir", "C:/Temp/")
    plan = _plan(solver_test)
    template = envelope.loads_envelope(
        plan.scene.data_payload, envelope.KIND_SCENE)
    refreshed = solver_test._refresh_cached_veyra_plan(
        plan, template, {"cloth": ((.25, .5, .75),)})
    payload = envelope.loads_envelope(
        refreshed.scene.data_payload, envelope.KIND_SCENE)
    assert payload[0]["object"][0]["vert"] == [[.25, .5, .75]]
    assert payload[0]["object"][0]["face"] == [[0, 0, 0]]
    assert refreshed.scene.param_payload is plan.scene.param_payload
    assert refreshed.scene.project_name != plan.scene.project_name
    assert refreshed.deformables[0].initial_local == ((.25, .5, .75),)
    assert refreshed.export_cache_events == {"veyra_validation_plan": "HIT"}


def test_veyra_cleanup_releases_cached_state_even_after_failure(
        blender_env, monkeypatch):
    solver_test = blender_env.solver_test
    cleared = []
    session = SimpleNamespace(
        job_id="job", validation_plan=object(),
        validation_payload_template=object())
    monkeypatch.setattr(solver_test, "_veyra_region_session", session)
    monkeypatch.setattr(solver_test, "clear_topology_cache", cleared.append)

    solver_test.CLOTHNEXT_OT_intersection_auto_fix()._veyra_cleanup(
        SimpleNamespace())

    assert cleared == ["job"]
    assert session.validation_plan is None
    assert session.validation_payload_template is None
