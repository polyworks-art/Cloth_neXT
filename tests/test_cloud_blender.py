# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloud Bakes inside the add-on (fake bpy): submit path, runtime, guards."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from cloth_next.cloud import service
from cloth_next.cloud.registry import LocalState
from tests.cloud_backend import CloudTestBackend
from tests.test_cloud_bakes import GAIA, OFFERED, make_plan, no_water


@pytest.fixture
def cloud_env(blender_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(tmp_path / "account"))
    backend = CloudTestBackend(builds=OFFERED)
    service.install_backend(lambda: backend)
    service.record_capabilities(backend.capabilities())
    runtime = __import__("sys").modules.get("cloth_next.blender.cloud_runtime")
    if runtime is None:
        import importlib
        runtime = importlib.import_module("cloth_next.blender.cloud_runtime")
    runtime.reset()
    try:
        yield SimpleNamespace(env=blender_env, backend=backend, runtime=runtime)
    finally:
        runtime.shutdown()
        runtime.reset()
        service.install_backend(None)


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_cloud_unavailable_without_service():
    service.install_backend(None)
    status = service.availability()
    assert not status.available and status.reason == service.PENDING_REASON


def test_runtime_uploads_in_background_and_shuts_down(cloud_env, tmp_path):
    from cloth_next.cloud.package import build_package
    runtime = cloud_env.runtime
    plan, deformables = make_plan(tmp_path)
    package = build_package(plan, deformables, store=runtime.jobs().store,
                            work_directory=tmp_path / "w", addon_version="2.9.39",
                            blender_version="5.2.3", job_name="shot",
                            prepare_inputs=no_water, solver=GAIA)
    runtime.submit(package)
    assert wait_for(lambda: any(job.state is LocalState.AWAITING_APPROVAL
                                for job in runtime.view().jobs))
    pending = runtime.view().jobs[0]
    assert runtime.describe_state(pending).startswith("Waiting for your confirmation")
    assert not any(name == "create_job" for _a, name in cloud_env.backend.calls)
    runtime.approve(pending.client_job_id, pending.quote_id)
    assert wait_for(lambda: any(job.state is LocalState.QUEUED
                                for job in runtime.view().jobs))
    assert runtime.view().message == "Job confirmed. You can close Blender."
    assert runtime.describe_state(runtime.view().jobs[0]).startswith("Waiting")
    assert runtime.shutdown()


def test_runtime_maps_failures_to_trusted_codes(cloud_env, tmp_path):
    from cloth_next.cloud.package import build_package
    runtime = cloud_env.runtime
    plan, deformables = make_plan(tmp_path)
    package = build_package(plan, deformables, store=runtime.jobs().store,
                            work_directory=tmp_path / "w", addon_version="2.9.39",
                            blender_version="5.2.3", job_name="shot",
                            prepare_inputs=no_water, solver=GAIA)
    cloud_env.backend.offline = True
    runtime.submit(package)
    assert wait_for(lambda: runtime.view().error_code == "CNX-E201")
    assert "internet connection" in runtime.view().message


def test_cloud_submit_never_touches_local_caches(cloud_env, monkeypatch):
    solver_test = cloud_env.env.solver_test
    monkeypatch.setattr(solver_test, "_cloud_submit_job_id", "job-1")
    monkeypatch.setattr(solver_test.shared_controller, "snapshot",
                        lambda: SimpleNamespace(job_id="job-1"))
    assert solver_test.is_cloud_submit()
    # Would raise on the fake plan if it inspected any cache.
    solver_test.prepare_cache_for_new_run(object())


def test_cloud_package_worker_queues_package(cloud_env, monkeypatch, tmp_path):
    solver_test = cloud_env.env.solver_test
    runtime = cloud_env.runtime
    captured = {}

    def package_plan(plan, deformables, *, check_cancel, event):
        check_cancel()
        event("EXPORTING", "Packaging for the Cloud", indeterminate=True)
        captured["plan"] = plan
        return "PACKAGE"
    monkeypatch.setattr(runtime, "package_plan", package_plan)
    monkeypatch.setattr(solver_test, "_plan_deformables", lambda plan: ())
    solver_test._cloud_package_worker("PLAN")
    messages = []
    while not solver_test._queue.empty():
        messages.append(solver_test._queue.get_nowait())
    assert messages[-1] == ("cloud_packaged", "PACKAGE")
    assert messages[0][0] == "event"


def test_bake_operator_refuses_cloud_without_fallback(blender_env, monkeypatch):
    solver_test = blender_env.solver_test
    service.install_backend(None)
    monkeypatch.setattr(blender_env.physics_ui, "bake_location", lambda _c: "CLOUD")
    started = []
    monkeypatch.setattr(
        __import__("cloth_next.blender.solver_backends",
                   fromlist=["begin_bake"]), "begin_bake",
        lambda context: started.append(context) or ("job", False))
    operator = solver_test.CLOTHNEXT_OT_bake()
    reports = []
    operator.report = lambda kind, text: reports.append(text)
    assert operator.execute(SimpleNamespace()) == {"CANCELLED"}
    assert not started and reports == [service.PENDING_REASON]


def test_bake_label_names_location(blender_env, monkeypatch):
    ui = blender_env.physics_ui
    model = SimpleNamespace(action="REBAKE")
    monkeypatch.setattr(ui, "bake_location", lambda _c: "CLOUD")
    assert ui.bake_button_text(None, model) == "Rebake · Cloud"
    monkeypatch.setattr(ui, "bake_location", lambda _c: "LOCAL")
    assert ui.bake_button_text(None, model) == "Rebake · This Computer"


class _Layout:
    def __init__(self, log):
        self.log = log
        self.alert = False

    def row(self, **_kwargs):
        return _Layout(self.log)

    box = column = row

    def label(self, text="", **_kwargs):
        self.log.append(("label", text))

    def operator(self, idname, text="", **_kwargs):
        props = SimpleNamespace()
        self.log.append(("operator", text, props))
        return props

    def prop(self, *_args, **_kwargs):
        pass


def test_jobs_list_draws_from_snapshot_only(blender_env, monkeypatch):
    import importlib
    import builtins
    from cloth_next.cloud.registry import new_record
    ui = importlib.import_module("cloth_next.blender.cloud_ui")
    runtime = importlib.import_module("cloth_next.blender.cloud_runtime")
    record = new_record(client_job_id="c" * 32, display_name="Shirt · 1–24",
                        solver=GAIA, manifest_sha256="0" * 64,
                        snapshot_digest="1" * 64, package_hashes=(),
                        upload_bytes_total=100)
    paused = record.__class__(**{**record.to_json(), "state": LocalState.UPLOAD_PAUSED,
                                 "package_hashes": (), "uploaded": (),
                                 "object_uuids": (), "quote_accepted": True})
    monkeypatch.setattr(runtime, "view", lambda: runtime.CloudView(jobs=(paused,)))
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("draw opened a file")))
    try:
        log = []
        ui.draw_jobs(_Layout(log))
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    labels = [entry[1] for entry in log if entry[0] == "label"]
    assert "Shirt · 1–24 · GAIA" in labels
    assert "Upload paused – not sent" in labels
    # Header: Refresh, Clean Up, then the Cloud Bakes dashboard link.
    operators = [entry for entry in log if entry[0] == "operator"]
    assert operators[1][2].action == "PRUNE"
    assert operators[2][2].url == ui.CLOUD_BAKES_PAGE
    assert [entry[1] for entry in operators][3:] == ["Resume Upload", "Cancel"]


def test_file_jobs_found_by_stored_id_or_object_uuid(blender_env):
    import importlib
    from cloth_next import export_identity
    from cloth_next.cloud.registry import new_record
    ui = importlib.import_module("cloth_next.blender.cloud_ui")

    def record(job, uuids=()):
        return new_record(client_job_id=job * 16, display_name=job, solver=GAIA,
                          manifest_sha256="0" * 64, snapshot_digest="1" * 64,
                          package_hashes=(), upload_bytes_total=1, object_uuids=uuids)
    obj = SimpleNamespace(cloth_next=SimpleNamespace(
        enabled=True, persistent_export_id="pid", role="CLOTH"))
    uuid = export_identity.export_uuid(obj)
    scene = SimpleNamespace(objects=[obj], cloth_next_cloud_job_ids="")
    stored, matched, other = record("aa"), record("bb", (uuid,)), record("cc", ("x",))
    ui.remember_job(scene, stored.client_job_id)
    ui.remember_job(scene, stored.client_job_id)
    assert scene.cloth_next_cloud_job_ids == stored.client_job_id
    found = ui.file_jobs(scene, (stored, matched, other))
    assert [job.display_name for job in found] == ["aa", "bb"]


def test_review_lines_show_server_quote_and_test_billing(blender_env):
    import importlib
    from cloth_next.cloud.registry import new_record
    ui = importlib.import_module("cloth_next.blender.cloud_ui")
    record = new_record(client_job_id="d" * 32, display_name="x", solver=GAIA,
                        manifest_sha256="0" * 64, snapshot_digest="1" * 64,
                        package_hashes=(), upload_bytes_total=1)
    record = record.__class__(**{**record.to_json(), "state": LocalState.AWAITING_APPROVAL,
                                 "package_hashes": (), "uploaded": (), "object_uuids": (),
                                 "quote_maximum": 30.0, "quote_unit": "test_second",
                                 "quote_expires_at": "2026-10-23T12:10:00Z",
                                 "quote_billing_mode": "test"})
    manifest = {"engine": "GAIA", "build_id": "gaia-test", "frame_start": 1,
                "frame_end": 24, "fps": 24.0, "water_mode": "hydrodynamic",
                "objects": [{"vertex_count": 625}],
                "files": [{"size_bytes": 2_000_000}]}
    lines = ui.review_lines(record, manifest, SimpleNamespace(retention_days=14))
    assert "Cost limit: 30 test_second · quote valid until 2026-10-23T12:10:00Z" in lines
    assert ("Credits are taken from your PolyWorks account; "
            "no payment provider is charged.") in lines
    assert "1 object(s) · 625 vertices · Water Flow · Hydrodynamic Pressure" in lines
    assert ui.actions_for(record) == ("REVIEW", "CANCEL")


def test_failed_free_job_offers_retry_and_requests_its_review(blender_env, monkeypatch):
    import importlib
    from cloth_next.cloud.jobs import retry_client_id
    from cloth_next.cloud.registry import LocalState
    ui = importlib.import_module("cloth_next.blender.cloud_ui")
    runtime = importlib.import_module("cloth_next.blender.cloud_runtime")
    parent_id = "0f0e0d0c-0b0a-4908-8706-050403020100"
    failed = SimpleNamespace(state=LocalState.FAILED, retryable=True, retried_as="",
                             client_job_id=parent_id)
    assert ui.actions_for(failed) == ("RETRY",)
    assert ui.actions_for(SimpleNamespace(state=LocalState.FAILED, retryable=False,
                                          retried_as="")) == ()
    assert ui.actions_for(SimpleNamespace(state=LocalState.FAILED, retryable=True,
                                          retried_as="x")) == ()
    queued = []
    monkeypatch.setattr(runtime, "enqueue", lambda action, job="": queued.append((action, job)))
    operator = ui.CLOTHNEXT_OT_cloud_job_action()
    operator.action, operator.client_job_id = "RETRY", parent_id
    scene = SimpleNamespace(cloth_next_cloud_job_ids="")
    assert operator.execute(SimpleNamespace(scene=scene)) == {"FINISHED"}
    child = retry_client_id(parent_id)
    assert queued == [("retry", parent_id)]
    assert child in scene.cloth_next_cloud_job_ids and child in ui._review_requested
