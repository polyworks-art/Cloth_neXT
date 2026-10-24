# SPDX-License-Identifier: GPL-3.0-or-later
"""``cloud.http_v1`` against a loopback server speaking Cloud API v1."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cloth_next.bake import cache_metadata
from cloth_next.cloud import transport as wire
from cloth_next.cloud.http_v1 import HttpCloudBackend, validate_origin
from cloth_next.cloud.importer import import_result, load_archived_snapshot
from cloth_next.cloud.jobs import BuildNotOffered, CloudJobs
from cloth_next.cloud.package import build_package
from cloth_next.cloud.package_store import PackageStore
from cloth_next.cloud.registry import JobRegistry, LocalState
from cloth_next.cloud.results import ResultArchive

from tests.cloud_backend import CloudTestBackend
from tests.cloud_http_server import TOKEN, CloudHttpServer
from tests.test_cloud_bakes import (GAIA, OFFERED, approve, local_reference, make_plan,
                                    no_water, synthetic_executor)


@pytest.fixture
def http(tmp_path):
    backend = CloudTestBackend(executor=synthetic_executor(), builds=OFFERED)
    server = CloudHttpServer(backend)
    client = HttpCloudBackend(lambda: TOKEN, origin=server.origin)
    root = tmp_path / "account"
    jobs = CloudJobs(client, JobRegistry(root), PackageStore(root / "cloud-packages"),
                     ResultArchive(root / "cloud-results"), sleep=lambda s: None,
                     upload_block=4096, free_bytes=lambda: 1 << 40)
    try:
        yield SimpleNamespace(backend=backend, server=server, client=client,
                              jobs=jobs, root=root)
    finally:
        server.close()


def package(http, tmp_path, **kwargs):
    plan, deformables = make_plan(tmp_path, **kwargs)
    built = build_package(plan, deformables, store=http.jobs.store,
                          work_directory=tmp_path, addon_version="2.9.39",
                          blender_version="5.2.3", job_name="http shot",
                          prepare_inputs=no_water, solver=GAIA)
    http.jobs.register(built)
    return deformables, built


def test_full_cycle_over_http_matches_local_bytes(http, tmp_path):
    deformables, built = package(http, tmp_path)
    approve(http.jobs, built)
    record = http.jobs.upload_and_submit(built.client_job_id)
    assert record.state is LocalState.QUEUED
    assert record.server_manifest_sha256 == http.backend.jobs[record.job_id]["manifest_sha256"]
    http.backend.run_pending()
    assert http.jobs.refresh(built.client_job_id).state is LocalState.SUCCEEDED
    record = http.jobs.download(built.client_job_id)
    snapshot = load_archived_snapshot(http.jobs.archive, built.client_job_id,
                                      record.snapshot_digest)
    target = tmp_path / "http.pc2"
    import_result(http.jobs.archive, built.client_job_id, snapshot=snapshot,
                  job_id=record.job_id, pc2_paths={"7d0e2b1c-3f4a-4c5d-8e6f-0a1b2c3d4e5f": target},
                  expected_build_id=record.server_build_id)
    assert target.read_bytes() == local_reference(tmp_path, deformables, 5).read_bytes()
    assert cache_metadata.inspect_cache(target).condition.value == "READY"


def test_patch_uses_one_zstd_frame_and_decoded_chunk_hash(http, tmp_path):
    _deformables, built = package(http, tmp_path)
    approve(http.jobs, built)
    http.jobs.upload_and_submit(built.client_job_id)
    patches = [headers for method, _path, headers in http.server.requests
               if method == "PATCH"]
    try:
        import zstandard  # noqa: F401 - Blender ships it; plain CPython may not
        expected = "zstd"
    except ImportError:
        expected = None   # identity fallback, still a valid v1 upload
    assert patches and all(headers.get("Content-Encoding") == expected for headers in patches)
    assert all(len(headers["Chunk-SHA256"]) == 64 for headers in patches)
    creates = [headers for method, path, headers in http.server.requests
               if method == "POST" and path.endswith("/jobs")]
    assert creates[0]["Idempotency-Key"] == built.client_job_id


def test_lost_submit_answer_over_http_reconciles_to_one_job(http, tmp_path):
    _deformables, built = package(http, tmp_path)
    approve(http.jobs, built)
    http.server.drop_after_handling.add("POST submit")
    record = http.jobs.upload_and_submit(built.client_job_id)
    assert record.state is LocalState.QUEUED and record.submitted_confirmed
    jobs = list(http.backend.jobs.values())
    assert len(jobs) == 1 and jobs[0]["submits"] == 1


def test_error_envelope_maps_code_never_message(http):
    http.backend.desktop_builds = []
    with pytest.raises(wire.CloudError) as error:
        http.client.quote({"engine": "GAIA", "build_id": "x", "solver_sha256": "a",
                           "worker_sha256": "b"})
    assert error.value.code == "engine_build_unavailable"
    assert error.value.request_id == "req_test"
    assert "server text" not in str(error.value)


def test_unmapped_build_stops_before_quote(http, tmp_path):
    http.backend.desktop_builds = []
    _deformables, built = package(http, tmp_path)
    with pytest.raises(BuildNotOffered):
        http.jobs.request_quote(built.client_job_id)
    assert not any(path.endswith("/quotes") for _m, path, _h in http.server.requests)


def test_redirects_and_foreign_download_urls_are_refused(http, tmp_path):
    http.server.redirect_next = True
    with pytest.raises(wire.CloudError) as error:
        http.client.capabilities()
    assert error.value.code == "invalid_response"
    _deformables, built = package(http, tmp_path)
    approve(http.jobs, built)
    record = http.jobs.upload_and_submit(built.client_job_id)
    http.backend.run_pending()
    http.server.foreign_url = True
    with pytest.raises(wire.CloudError) as error:
        http.client.result_manifest(record.job_id)
    assert error.value.code == "invalid_response"


def test_missing_session_sends_nothing(http):
    client = HttpCloudBackend(lambda: None, origin=http.server.origin)
    with pytest.raises(wire.CloudError) as error:
        client.capabilities()
    assert error.value.code == "session_expired"
    assert not http.server.requests


def test_snapshot_comes_back_through_package_session(http, tmp_path):
    _deformables, built = package(http, tmp_path)
    approve(http.jobs, built)
    record = http.jobs.upload_and_submit(built.client_job_id)
    raw = http.client.fetch_package_file(record.job_id, built.snapshot.digest)
    assert raw == built.snapshot.file_bytes


def test_origin_rules():
    assert validate_origin("https://api.cloth-next.de") == "https://api.cloth-next.de"
    assert validate_origin("http://127.0.0.1:8080").startswith("http://127.0.0.1")
    for bad in ("http://api.cloth-next.de", "https://api.cloth-next.de/v1",
                "https://user:pw@api.cloth-next.de", "http://localhost:80"):
        with pytest.raises(ValueError):
            validate_origin(bad)


def _openapi_schema(name):
    spec = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "cloud_v1"
                       / "openapi.json").read_text(encoding="utf-8"))
    return {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}


def test_manifest_validates_against_openapi(http, tmp_path):
    import jsonschema
    _deformables, built = package(http, tmp_path)
    jsonschema.validate({"manifest": built.manifest}, _openapi_schema("Create"),
                        format_checker=jsonschema.FormatChecker())


def test_real_export_manifest_validates_against_openapi():
    import jsonschema
    path = (__import__("pathlib").Path(__file__).parent / "fixtures" / "cloud_v1"
            / "real_aether_manifest.json")
    if not path.is_file():
        pytest.skip("real AETHER export fixture not generated")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate({"manifest": manifest}, _openapi_schema("Create"),
                        format_checker=jsonschema.FormatChecker())


def test_test_server_capabilities_validate_against_openapi(http):
    import jsonschema
    import urllib.request
    # The pilot contract still pins execution.enabled to false; the add-on
    # stays locked until the live server answers true (see below).
    http.backend.execution_enabled = False
    request = urllib.request.Request(http.server.origin + "/v1/cloud/capabilities",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(request) as response:
        value = json.loads(response.read())
    jsonschema.validate(value, _openapi_schema("CapabilitiesOut"))


def test_cloud_stays_locked_until_server_enables_execution(http):
    from cloth_next.cloud import service
    service.install_backend(lambda: http.client)
    try:
        http.backend.execution_enabled = False
        service.record_capabilities(http.client.capabilities())
        status = service.availability()
        assert not status.available and status.reason == service.LOCKED_REASON
        assert not service.unlocked()
        http.backend.execution_enabled = True
        service.record_capabilities(http.client.capabilities())
        assert service.availability().available and service.unlocked()
        service.record_capabilities(None, "not_found")
        assert service.availability().reason == service.LOCKED_REASON
        service.record_capabilities(None, "not_authenticated")
        assert service.availability().reason != service.LOCKED_REASON
    finally:
        service.install_backend(None)


def test_default_origin_is_the_polyworks_cloud_and_can_be_switched_off():
    from cloth_next.cloud import service
    factory = service.default_factory({})
    assert factory is not None and factory().origin == service.DEFAULT_ORIGIN
    assert service.default_factory({"CLOTH_NEXT_CLOUD_API": "off"}) is None


def test_account_without_submit_right_stops_before_quote(http, tmp_path):
    from cloth_next.cloud.jobs import AccessDenied
    from cloth_next.cloud.registry import LocalState
    http.backend.can_submit = False
    _deformables, built = package(http, tmp_path)
    with pytest.raises(AccessDenied):
        http.jobs.request_quote(built.client_job_id)
    assert any(path.endswith("/account") for _m, path, _h in http.server.requests)
    assert not any(path.endswith("/quotes") for _m, path, _h in http.server.requests)
    record = http.jobs.registry.get(built.client_job_id)
    assert record.state is LocalState.UPLOAD_PAUSED
    assert record.error_code == "cloud_access_required"


def test_package_over_server_limit_stops_before_quote(http, tmp_path, monkeypatch):
    from dataclasses import replace
    from cloth_next.cloud.jobs import PackageTooLarge
    original = http.client.capabilities
    monkeypatch.setattr(http.client, "capabilities",
                        lambda: replace(original(), max_file_bytes=16))
    _deformables, built = package(http, tmp_path)
    with pytest.raises(PackageTooLarge):
        http.jobs.request_quote(built.client_job_id)
    assert not any(path.endswith("/quotes") for _m, path, _h in http.server.requests)


def test_quote_keeps_maximum_runtime_for_review(http, tmp_path, blender_env):
    from cloth_next.blender.cloud_ui import review_lines
    from cloth_next.cloud.jobs import load_manifest
    _deformables, built = package(http, tmp_path)
    record = http.jobs.request_quote(built.client_job_id)
    assert record.quote_max_seconds
    if record.quote_max_seconds:
        lines = review_lines(record, load_manifest(http.jobs.store, record.manifest_sha256))
        assert any(line.startswith("Maximum server runtime") for line in lines)


def test_quote_and_job_responses_follow_the_current_contract(http, tmp_path):
    import jsonschema
    _deformables, built = package(http, tmp_path)
    record = http.jobs.request_quote(built.client_job_id)
    assert record.quote_gpu == "NVIDIA RTX A5000" and record.quote_usage_percent == 290
    approve(http.jobs, built)
    record = http.jobs.upload_and_submit(built.client_job_id)
    http.backend.run_pending()
    record = http.jobs.refresh(built.client_job_id)
    assert record.billing_finalized and record.final_cost == "7 credits"
    assert record.elapsed_ms == 21000 and record.gpu_type_id == "NVIDIA RTX A5000"
    import urllib.request
    request = urllib.request.Request(
        f"{http.server.origin}/v1/cloud/jobs/{record.job_id}",
        headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(request) as response:
        jsonschema.validate(json.loads(response.read()), _openapi_schema("JobOut"))
    quote = [entry for entry in http.server.requests if entry[1].endswith("/quotes")]
    assert quote


def test_unfinalized_billing_is_not_a_final_cost():
    from cloth_next.cloud.http_v1 import _job
    base = {"job_id": "00000000-0000-4000-8000-000000000002",
            "client_job_id": "00000000-0000-4000-8000-000000000001",
            "state": "succeeded", "progress": {}, "statistics": {"elapsed_ms": None}}
    pending = _job({**base, "billing": {"unit": "credit", "reserved": 311,
                                        "charged": None, "finalized": False}})
    assert pending.final_cost is None and pending.billing_reserved == 311
    zero = _job({**base, "billing": {"unit": "credit", "reserved": 0,
                                     "charged": 0, "finalized": True}})
    assert zero.final_cost == "0 credits" and zero.billing_finalized
    settled = _job({**base, "billing": {"unit": "credit", "reserved": 0,
                                        "charged": 1, "finalized": True},
                    "statistics": {"elapsed_ms": 282000}})
    assert settled.final_cost == "1 credit" and settled.elapsed_ms == 282000
