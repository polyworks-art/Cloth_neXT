# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloud Bakes Phase 0 against the deterministic test backend.

These tests prove the add-on side only: packaging, the immutable store, the
Import Snapshot, idempotent upload/submit with lost responses, resumable
transfers and the shared import path. They do not prove real Cloud execution;
tools/cloud_phase0.py runs the real solver.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import numpy as np
import pytest

from cloth_next.bake import cache_metadata, pc2, result_import
from cloth_next.cloud import privacy, transport as wire
from cloth_next.cloud.importer import import_result, load_archived_snapshot
from cloth_next.cloud.jobs import CloudJobs, InsufficientDownloadSpace, UploadPaused
from cloth_next.cloud.package import PackageError, build_package
from cloth_next.cloud.package_store import (BlobCorrupt, InsufficientSpace,
                                            PackageStore)
from cloth_next.cloud.registry import JobRegistry, LocalState
from cloth_next.cloud.results import ResultArchive, ResultError
from cloth_next.cloud.snapshot import SnapshotError, build_snapshot, load_snapshot
from cloth_next.ppf import results as solver_results
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.schema import cbor_codec, envelope
from cloth_next.ppf_run.session import PreparedSolverInputs, SessionDeformable, SessionScene
from cloth_next.topology import reference_topology_signature

from tests.cloud_backend import CloudTestBackend, RawRunOutput

GAIA = {"engine": "GAIA", "build_id": "gaia:test", "version": "0.26.0-test",
        "package_version": "0.26.0", "protocol_version": "0.26",
        "schema_version": "2"}
IDENTITY = ((1.0, 0, 0, 0), (0, 1.0, 0, 0), (0, 0, 1.0, 0), (0, 0, 0, 1.0))
MOVED = ((1.0, 0, 0, 2.0), (0, 1.0, 0, -1.0), (0, 0, 1.0, 0.5), (0, 0, 0, 1.0))


@dataclass(frozen=True)
class Deformable:  # DeformablePlan-shaped
    initial_local: tuple
    world_matrix: tuple
    object_name: str
    uuid: str
    pc2_path: Path
    topology_signature: str
    material_meta: dict
    role: str
    stitch_pairs: tuple = ()
    stitch_snap_distance: float = 0.0


def material_meta(vertex_count, frame_count, *, name="Shirt"):
    return {
        "fingerprints": {"settings": "s" * 8, "geometry": "g" * 8,
                         "combined": "c" * 8, "topology": "t" * 8,
                         "object": "o" * 8, "scene": "x" * 8},
        "identities": {"cloth_next_version": "2.9.39", "blender_version": "5.2.2",
                       "object": {"object_key": name},
                       "solver": {"mode": "MANAGED_INSTALLATION",
                                  "source_metadata": {"root": "C:/Users/x/solver"}}},
        "expected": {"vertex_count": vertex_count, "frame_count": frame_count,
                     "start_frame": 0.0, "sample_rate": 1.0},
        "details": {"preset": "cotton", "blender_start_frame": 1,
                    "blender_end_frame": frame_count},
    }


def scene_payloads(tmp_path, *, param_marker=b"param"):
    data = envelope.dumps_envelope(envelope.KIND_SCENE, {"marker": "data"},
                                   schema_version=2)
    param = envelope.dumps_envelope(envelope.KIND_PARAM,
                                    {"marker": param_marker.decode()},
                                    schema_version=2)
    return data, param


def make_plan(tmp_path, *, frames=5, world=IDENTITY, param_marker=b"param",
              solid=False, stitch_pairs=(), snap=0.0, name="Shirt"):
    initial = tuple((float(i), float(i % 2), 0.0) for i in range(4))
    data, param = scene_payloads(tmp_path, param_marker=param_marker)
    kind = "SOLID" if solid else "SHELL"
    session_object = SessionDeformable("uuid-shirt", "uuid-shirt", 4, kind,
                                       solver_world_matrix(world))
    scene = SessionScene(
        "clothnext_test", "uuid-shirt", "uuid-shirt", 4, "", "", frames,
        data, param, envelope.payload_sha256(data), envelope.payload_sha256(param),
        deformables=(session_object,), official_bridge_json='{"stitches":[]}')
    deformable = Deformable(initial, world, name, "uuid-shirt",
                            tmp_path / "local" / "cn_test_cloth_shirt.pc2",
                            "topology-1", material_meta(4, frames, name=name),
                            "CLOTH", tuple(stitch_pairs), snap)
    plan = SimpleNamespace(
        scene=scene, resolved=SimpleNamespace(solver_environment=()),
        frame_start=1, frame_end=frames, frame_count=frames, fps=24.0,
        settings_fingerprint="s" * 8, geometry_fingerprint="g" * 8)
    return plan, (deformable,)


def no_water(scene, resolved, work, *, check_cancel, event):
    return PreparedSolverInputs(scene)


def synthetic_frames(frames, *, total=6, solid=False):
    """Solver-world frames: 4 dynamic vertices + 2 collider vertices."""
    result = []
    for frame in range(1, frames):
        positions = np.zeros((total, 3), dtype="<f4")
        for vertex in range(total):
            positions[vertex] = (vertex * 0.5, 1.0 - 0.01 * frame * vertex, 0.25 * frame)
        result.append(positions)
    return result


def vertex_map_blob():
    return cbor_codec.dumps({"version": 2, "kind": "VertexMap",
                             "payload": {"uuid-shirt": [0, 1, 2, 3],
                                         "uuid-collider": [4, 5]}})


def synthetic_executor(*, partial_after=None, failed=""):
    def run(job, files):
        manifest = json.loads(files["manifest.json"])
        count = manifest["frames"]["count"]
        frames = [frame.tobytes() for frame in synthetic_frames(count)]
        if partial_after is not None:
            frames = frames[:partial_after]
        return RawRunOutput(map_blob=vertex_map_blob(), frames=frames,
                            build_id=manifest["solver"]["build_id"],
                            partial=partial_after is not None, failed_code=failed)
    return run


@pytest.fixture
def cloud(tmp_path):
    backend = CloudTestBackend(executor=synthetic_executor())
    root = tmp_path / "account"
    store = PackageStore(root / "cloud-packages")
    jobs = CloudJobs(backend, JobRegistry(root), store,
                     ResultArchive(root / "cloud-results"), sleep=lambda s: None,
                     upload_block=7, free_bytes=lambda: 1 << 40)
    return SimpleNamespace(backend=backend, root=root, store=store, jobs=jobs)


def package_for(cloud, tmp_path, **plan_kwargs):
    plan, deformables = make_plan(tmp_path, **plan_kwargs)
    package = build_package(plan, deformables, store=cloud.store,
                            work_directory=tmp_path, addon_version="2.9.39",
                            blender_version="5.2.2", job_name="Test shot",
                            prepare_inputs=no_water, solver=GAIA)
    cloud.jobs.register(package)
    return plan, deformables, package


def local_reference(tmp_path, deformables, frames, *, schema=2):
    """What the local Bake writes for the same raw output (shared path)."""
    targets = tuple(result_import.ImportTarget(
        d.uuid, d.object_name, tmp_path / "reference" / "ref.pc2",
        d.initial_local, d.world_matrix, d.stitch_pairs, d.stitch_snap_distance,
        d.material_meta) for d in deformables)
    _map, extraction = solver_results.prepare_output_extraction(
        vertex_map_blob(), None,
        (solver_results.ExtractionTarget("uuid-shirt", 4, "SHELL"),),
        schema_version=schema)
    caches = result_import.PlaybackCacheImport(targets, frame_count=frames)
    caches.begin()
    caches.ensure_writers()
    for index, positions in enumerate(synthetic_frames(frames), start=1):
        caches.write_frame(solver_results.extract_dynamic_frame(
            positions, extraction,
            (solver_results.ExtractionTarget("uuid-shirt", 4, "SHELL"),),
            frame=index))
    caches.finalize(solver_identity={"package_version": "0.26.0",
                                     "protocol_version": "0.26",
                                     "schema_version": "2"})
    return targets[0].pc2_path


def run_to_stored(cloud, package):
    record = cloud.jobs.upload_and_submit(package.client_job_id)
    assert record.state is LocalState.QUEUED
    cloud.backend.run_pending()
    record = cloud.jobs.refresh(package.client_job_id)
    assert record.state is LocalState.SUCCEEDED
    return cloud.jobs.download(package.client_job_id)


# Snapshot ---------------------------------------------------------------------

def test_snapshot_is_deterministic_and_verifies(tmp_path):
    plan, deformables = make_plan(tmp_path)
    kwargs = dict(deformables=deformables, session_objects=plan.scene.dynamic_objects,
                  frame_start=1, frame_end=5, frame_count=5, fps=24.0, solver=GAIA,
                  addon_version="2.9.39", payload_hashes={"data": "a", "param": "b"})
    first = build_snapshot(**kwargs)
    second = build_snapshot(**kwargs)
    assert first.json_bytes == second.json_bytes
    assert first.digest == second.digest
    restored = load_snapshot(first.json_bytes, first.arrays,
                             expected_digest=first.digest)
    np.testing.assert_array_equal(restored.initial_local("uuid-shirt"),
                                  np.asarray(deformables[0].initial_local, "<f4"))
    # Local install provenance is dropped; nothing else is invented.
    meta = restored.objects[0]["material_meta"]
    assert "source_metadata" not in meta["identities"]["solver"]
    assert restored.objects[0]["solver_world_matrix"] == [
        list(row) for row in solver_world_matrix(IDENTITY)]
    damaged = bytearray(first.arrays)
    damaged[0] ^= 1
    with pytest.raises(SnapshotError):
        load_snapshot(first.json_bytes, bytes(damaged))
    with pytest.raises(SnapshotError, match="digest"):
        load_snapshot(first.json_bytes, first.arrays, expected_digest="0" * 64)


def test_snapshot_rejects_private_paths(tmp_path):
    plan, (deformable,) = make_plan(tmp_path)
    meta = material_meta(4, 5)
    meta["details"]["cache"] = "C:\\Users\\artist\\shots\\cache.pc2"
    leaky = Deformable(**{**deformable.__dict__, "material_meta": meta})
    with pytest.raises(privacy.PrivacyViolation, match="local path"):
        build_snapshot(deformables=(leaky,),
                       session_objects=plan.scene.dynamic_objects,
                       frame_start=1, frame_end=5, frame_count=5, fps=24.0,
                       solver=GAIA, addon_version="2.9.39",
                       payload_hashes={})


def test_privacy_scan_finds_tokens_and_signed_urls():
    assert privacy.findings({"a": "https://x/y?t=abc"}, user_names=())
    assert privacy.findings({"a": "Bearer abcdefghijkl"}, user_names=())
    assert privacy.findings({"token": "x"}, user_names=())
    assert privacy.findings({"a": "/home/someone/file"}, user_names=())
    assert not privacy.findings({"a": "gaia:0.26 Shirt", "n": 3}, user_names=())


def test_topology_signature_detects_vertex_reordering():
    mesh = SimpleNamespace(
        vertices=[0, 1, 2, 3],
        edges=[SimpleNamespace(vertices=(0, 1)), SimpleNamespace(vertices=(1, 2)),
               SimpleNamespace(vertices=(2, 0)), SimpleNamespace(vertices=(2, 3))],
        polygons=[SimpleNamespace(loop_start=0, loop_total=3)],
        loops=[SimpleNamespace(vertex_index=i) for i in (0, 1, 2)])
    swapped = SimpleNamespace(
        vertices=mesh.vertices,
        edges=[SimpleNamespace(vertices=(1, 0)), SimpleNamespace(vertices=(0, 2)),
               SimpleNamespace(vertices=(2, 1)), SimpleNamespace(vertices=(2, 3))],
        polygons=mesh.polygons,
        loops=[SimpleNamespace(vertex_index=i) for i in (1, 0, 2)])
    assert reference_topology_signature(mesh) != reference_topology_signature(swapped)


# Package store ----------------------------------------------------------------

def test_package_store_is_immutable_and_verified(tmp_path):
    store = PackageStore(tmp_path / "store")
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc" * 1000)
    blob = store.put_file(source)
    path = store.verify(blob.sha256)
    assert not os.stat(path).st_mode & stat.S_IWRITE
    # Identical content is stored once and shared.
    assert store.put_bytes(b"abc" * 1000) == blob
    # Changing the source afterwards cannot touch the stored copy.
    source.write_bytes(b"changed")
    assert store.verify(blob.sha256) == path
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    path.write_bytes(b"tampered")
    with pytest.raises(BlobCorrupt):
        store.verify(blob.sha256)
    with pytest.raises(BlobCorrupt):
        store.put_file(source, expected_sha256="0" * 64)


def test_package_store_gc_keeps_referenced_blobs(tmp_path):
    store = PackageStore(tmp_path / "store")
    kept = store.put_bytes(b"kept")
    dropped = store.put_bytes(b"dropped")
    store.add_reference("job-00000001", [kept.sha256])
    removed = store.collect_garbage(grace_seconds=0, now=10 ** 12)
    assert removed == [dropped.sha256]
    assert store.has(kept.sha256)
    store.release_reference("job-00000001")
    assert store.collect_garbage(grace_seconds=0, now=10 ** 12) == [kept.sha256]


def test_package_store_checks_free_space(tmp_path, monkeypatch):
    store = PackageStore(tmp_path / "store")
    monkeypatch.setattr(store, "free_bytes", lambda: 100)
    with pytest.raises(InsufficientSpace) as error:
        store.require_space(80, reserve_bytes=50)
    assert error.value.required == 130


# Package ----------------------------------------------------------------------

def test_package_manifest_has_hashes_and_no_private_data(cloud, tmp_path):
    plan, _deformables, package = package_for(cloud, tmp_path)
    manifest = package.manifest
    roles = {item["role"]: item for item in manifest["files"]}
    assert roles["data"]["sha256"] == plan.scene.data_hash
    assert roles["param"]["sha256"] == plan.scene.param_hash
    assert {"official-bindings", "snapshot", "snapshot-arrays"} <= set(roles)
    assert manifest["snapshot_digest"] == package.snapshot.digest
    assert manifest["objects"] == [{
        "uuid": "uuid-shirt", "deformable_type": "SHELL", "vertex_count": 4,
        "solver_world_matrix": [list(row) for row in solver_world_matrix(IDENTITY)]}]
    assert privacy.findings(manifest) == []
    raw = cloud.store.verify(package.manifest_sha256).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == package.manifest_sha256
    assert b"Shirt" not in raw  # object names stay in the snapshot (P4)


def test_package_rejects_unconverted_water_stream(cloud, tmp_path):
    plan, deformables = make_plan(tmp_path)
    from dataclasses import replace

    def legacy(scene, *_args, **_kwargs):
        return PreparedSolverInputs(replace(scene, water_stream_json='{"version":1}'))
    with pytest.raises(PackageError) as error:
        build_package(plan, deformables, store=cloud.store, work_directory=tmp_path,
                      addon_version="2.9.39", blender_version="5.2.2",
                      job_name="x", prepare_inputs=legacy, solver=GAIA)
    assert error.value.code == "water_mode_unsupported"


def test_package_includes_hydro_volumes_without_local_paths(cloud, tmp_path):
    plan, deformables = make_plan(tmp_path)
    volume = tmp_path / "hydro" / "volumes"
    (volume / "field-0-fluid").mkdir(parents=True)
    (volume / "field-0-fluid" / "bricks-00000.bin").write_bytes(b"\0" * 64)
    (volume / "force_field.toml").write_text("[[field]]\n", encoding="utf-8")
    (volume / "hydrodynamics.json").write_text(json.dumps({
        "version": 1, "frames": 2, "provenance": {
            "source_cache": "C:/Users/artist/flip", "source_fingerprint": "f" * 8,
            "method": "m"}}), encoding="utf-8")
    (volume / "preparation-report.json").write_text('{"x":"C:/flip"}', encoding="utf-8")
    model = tmp_path / "hydro" / "model.json"
    model.write_text('{"density_kg_m3":1000}', encoding="utf-8")

    def hydro(scene, *_args, **_kwargs):
        return PreparedSolverInputs(scene, {
            "PPF_EXPERIMENTAL_HYDRO_TEST_JSON": str(model),
            "PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR": str(volume)})
    package = build_package(plan, deformables, store=cloud.store,
                            work_directory=tmp_path / "not" / "yet" / "there",
                            addon_version="2.9.39",
                            blender_version="5.2.2", job_name="x",
                            prepare_inputs=hydro, solver=GAIA)
    names = {item.name for item in package.files}
    assert {"hydro-model.json", "hydro/force_field.toml",
            "hydro/field-0-fluid/bricks-00000.bin", "hydro/hydrodynamics.json"} <= names
    assert "hydro/preparation-report.json" not in names
    assert package.manifest["water"] == "hydrodynamic"
    stored = next(item for item in package.files
                  if item.name == "hydro/hydrodynamics.json")
    document = json.loads(cloud.store.verify(stored.sha256).read_bytes())
    assert document["provenance"] == {"source_fingerprint": "f" * 8, "method": "m"}


# Upload, submit, idempotency ------------------------------------------------------

def test_full_cycle_matches_local_import_bytes(cloud, tmp_path):
    _plan, deformables, package = package_for(cloud, tmp_path)
    record = run_to_stored(cloud, package)
    assert record.state is LocalState.STORED
    archive = cloud.jobs.archive
    snapshot = load_archived_snapshot(archive, package.client_job_id,
                                      record.snapshot_digest)
    target = tmp_path / "cloud" / "cn_test_cloth_shirt.pc2"
    import_result(archive, package.client_job_id, snapshot=snapshot,
                  job_id=record.job_id, pc2_paths={"uuid-shirt": target})
    reference = local_reference(tmp_path, deformables, 5)
    assert target.read_bytes() == reference.read_bytes()
    cloud_meta = json.loads(cache_metadata.sidecar_path(target).read_text())
    local_meta = json.loads(cache_metadata.sidecar_path(reference).read_text())
    assert cloud_meta.pop("execution") == {
        "kind": "cloud", "job_id": record.job_id,
        "client_job_id": package.client_job_id, "build_id": "gaia:test",
        "snapshot_digest": snapshot.digest, "solver_frames": 4,
        "partial_of_job": False}
    for meta in (cloud_meta, local_meta):
        meta.pop("metadata_digest")
        meta.pop("cache_file")
        meta.pop("timings", None)
        meta["identities"]["solver"].pop("source_metadata", None)
    assert cloud_meta == local_meta
    inspection = cache_metadata.inspect_cache(target, settings_fingerprint="s" * 8,
                                              geometry_fingerprint="g" * 8)
    assert inspection.condition is cache_metadata.CacheCondition.READY


def test_import_uses_snapshot_matrix_not_current_scene(cloud, tmp_path):
    _plan, deformables, package = package_for(cloud, tmp_path, world=MOVED)
    record = run_to_stored(cloud, package)
    snapshot = load_archived_snapshot(cloud.jobs.archive, package.client_job_id,
                                      record.snapshot_digest)
    target = tmp_path / "cloud.pc2"
    import_result(cloud.jobs.archive, package.client_job_id, snapshot=snapshot,
                  job_id=record.job_id, pc2_paths={"uuid-shirt": target})
    frames = list(pc2.iter_frames(target))
    expected = synthetic_frames(5)[0][:4].astype(np.float64)
    from cloth_next.ppf.coordinates import solver_world_to_object_local, transform_points_numpy
    local = transform_points_numpy(solver_world_to_object_local(MOVED), expected)
    np.testing.assert_allclose(np.asarray(frames[1]), local.astype("<f4"))


def test_lost_submit_response_never_creates_a_second_job(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.backend.lose_response.add("submit")
    record = cloud.jobs.upload_and_submit(package.client_job_id)
    # The server accepted; the add-on reconciled instead of resubmitting.
    assert record.state is LocalState.QUEUED
    assert record.submitted_confirmed
    jobs = cloud.backend.list_jobs()
    assert len(jobs) == 1 and cloud.backend.jobs[jobs[0].job_id]["submits"] == 1


def test_unconfirmed_submit_survives_restart_and_reconciles(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.backend.lose_response.add("submit")
    cloud.backend.offline = False
    original_find = cloud.backend.find_job
    cloud.backend.find_job = lambda _id: (_ for _ in ()).throw(
        wire.CloudError("network_unavailable"))
    record = cloud.jobs.upload_and_submit(package.client_job_id)
    assert record.state is LocalState.UNCONFIRMED   # "Possibly started – checking"
    # Restart: fresh objects over the same registry root.
    cloud.backend.find_job = original_find
    restarted = CloudJobs(cloud.backend, JobRegistry(cloud.root), cloud.store,
                          ResultArchive(cloud.root / "cloud-results"),
                          sleep=lambda s: None)
    (settled,) = restarted.recover()
    assert settled.state is LocalState.QUEUED
    assert len(cloud.backend.jobs) == 1


def test_submit_that_never_arrived_is_resent_with_the_same_key(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.backend.fail_before.add("submit")
    record = cloud.jobs.upload_and_submit(package.client_job_id)
    assert record.state is LocalState.UPLOAD_PAUSED   # created, not submitted
    record = cloud.jobs.upload_and_submit(package.client_job_id)
    assert record.state is LocalState.QUEUED
    keys = [key for (_account, key) in cloud.backend.idempotency]
    assert keys.count(f"{package.client_job_id}:submit") == 1
    assert len(cloud.backend.jobs) == 1


def test_lost_create_response_reuses_the_created_job(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.backend.lose_response.add("create_job")
    record = cloud.jobs.upload_and_submit(package.client_job_id)
    assert record.state is LocalState.UPLOAD_PAUSED and record.job_id
    record = cloud.jobs.upload_and_submit(package.client_job_id)
    assert record.state is LocalState.QUEUED
    assert len(cloud.backend.jobs) == 1


def test_idempotency_conflict_is_reported_not_replaced(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.backend.idempotency[(cloud.backend.account, package.client_job_id)] = (
        "different-manifest", "job-9999")
    with pytest.raises(wire.CloudError) as error:
        cloud.jobs.upload_and_submit(package.client_job_id)
    assert error.value.code == "idempotency_conflict"
    record = cloud.jobs.registry.get(package.client_job_id)
    assert record.create_key == package.client_job_id   # same id, never a new one


def test_interrupted_upload_resumes_after_restart(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.backend.cut_upload_after = 20
    cloud.jobs._backoff = ()          # first drop pauses immediately
    with pytest.raises(UploadPaused):
        cloud.jobs.upload_and_submit(package.client_job_id)
    assert cloud.jobs.registry.get(package.client_job_id).state is LocalState.UPLOAD_PAUSED
    sent_before = sum(1 for _a, name in cloud.backend.calls if name == "upload_block")
    restarted = CloudJobs(cloud.backend, JobRegistry(cloud.root), cloud.store,
                          ResultArchive(cloud.root / "cloud-results"),
                          sleep=lambda s: None, upload_block=7)
    record = restarted.upload_and_submit(package.client_job_id)
    assert record.state is LocalState.QUEUED
    sent_total = sum(1 for _a, name in cloud.backend.calls if name == "upload_block")
    full = sum(-(-item.size // 7) for item in package.files)
    # Resumed from the server offset: no file was sent twice from zero.
    assert sent_total <= full + 2
    assert sent_before < full


def test_upload_reverifies_store_before_sending(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    param = next(item for item in package.files if item.role == "param")
    path = cloud.store.blob_path(param.sha256)
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    path.write_bytes(b"x" * param.size)
    with pytest.raises(BlobCorrupt):
        cloud.jobs.upload_and_submit(package.client_job_id)
    assert not any(name == "submit" for _a, name in cloud.backend.calls)


def test_deduplication_only_sends_changed_files(cloud, tmp_path):
    _plan, _deformables, first = package_for(cloud, tmp_path)
    cloud.jobs.upload_and_submit(first.client_job_id)
    before = sum(1 for _a, name in cloud.backend.calls if name == "upload_block")
    _plan, _deformables, second = package_for(cloud, tmp_path / "b",
                                              param_marker=b"other")
    record = cloud.jobs.upload_and_submit(second.client_job_id)
    assert record.state is LocalState.QUEUED
    sent = sum(1 for _a, name in cloud.backend.calls if name == "upload_block") - before
    changed = {item.sha256 for item in second.files} - {item.sha256 for item in first.files}
    expected = sum(-(-item.size // 7) for item in second.files if item.sha256 in changed)
    assert sent == expected
    assert next(item for item in second.files if item.role == "data").sha256 not in changed


def test_deduplication_is_isolated_per_account(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.jobs.upload_and_submit(package.client_job_id)
    cloud.backend.account = "account-b"
    other = CloudJobs(cloud.backend, JobRegistry(tmp_path / "b-root"),
                      PackageStore(tmp_path / "b-root" / "cloud-packages"),
                      ResultArchive(tmp_path / "b-root" / "cloud-results"),
                      sleep=lambda s: None, upload_block=7)
    plan, deformables = make_plan(tmp_path / "b")
    package_b = build_package(plan, deformables, store=other.store,
                              work_directory=tmp_path, addon_version="2.9.39",
                              blender_version="5.2.2", job_name="x",
                              prepare_inputs=no_water, solver=GAIA)
    other.register(package_b)
    before = sum(1 for _a, name in cloud.backend.calls if name == "upload_block")
    other.upload_and_submit(package_b.client_job_id)
    sent = sum(1 for _a, name in cloud.backend.calls if name == "upload_block") - before
    assert sent == sum(-(-item.size // 7) for item in package_b.files)
    assert cloud.backend.find_job(package.client_job_id) is None


def test_offline_before_upload_is_not_sent(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.backend.offline = True
    with pytest.raises(wire.CloudError):
        cloud.jobs.upload_and_submit(package.client_job_id)
    record = cloud.jobs.registry.get(package.client_job_id)
    assert record.state is LocalState.UPLOAD_PAUSED and not record.job_id
    assert not record.submitted_confirmed


# Download, import -----------------------------------------------------------------

def test_corrupt_download_block_is_detected_and_refetched(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.jobs.upload_and_submit(package.client_job_id)
    cloud.backend.run_pending()
    cloud.jobs.refresh(package.client_job_id)
    cloud.backend.corrupt_download = True
    record = cloud.jobs.download(package.client_job_id)
    assert record.state is LocalState.STORED
    assert cloud.jobs.archive.is_complete(package.client_job_id)


def test_download_requires_free_space(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.jobs.upload_and_submit(package.client_job_id)
    cloud.backend.run_pending()
    cloud.jobs.refresh(package.client_job_id)
    cloud.jobs._free_bytes = lambda: 10
    with pytest.raises(InsufficientDownloadSpace):
        cloud.jobs.download(package.client_job_id)
    assert cloud.jobs.registry.get(package.client_job_id).state is LocalState.SUCCEEDED


def test_snapshot_is_recovered_from_server_when_local_package_is_gone(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.jobs.upload_and_submit(package.client_job_id)
    cloud.backend.run_pending()
    cloud.jobs.refresh(package.client_job_id)
    # Submission released the package; garbage collection may remove it.
    assert cloud.store.collect_garbage(grace_seconds=0, now=10 ** 12)
    record = cloud.jobs.download(package.client_job_id)
    snapshot = load_archived_snapshot(cloud.jobs.archive, package.client_job_id,
                                      record.snapshot_digest)
    assert snapshot.digest == package.snapshot.digest


def test_partial_result_writes_shorter_complete_cache(cloud, tmp_path):
    cloud.backend.executor = synthetic_executor(partial_after=2)
    _plan, _deformables, package = package_for(cloud, tmp_path, frames=6)
    cloud.jobs.upload_and_submit(package.client_job_id)
    cloud.backend.run_pending()
    record = cloud.jobs.refresh(package.client_job_id)
    assert record.state is LocalState.STOPPED_AT_LIMIT
    record = cloud.jobs.download(package.client_job_id)
    snapshot = load_archived_snapshot(cloud.jobs.archive, package.client_job_id,
                                      record.snapshot_digest)
    target = tmp_path / "partial.pc2"
    import_result(cloud.jobs.archive, package.client_job_id, snapshot=snapshot,
                  job_id=record.job_id, pc2_paths={"uuid-shirt": target})
    assert pc2.read_header(target).frame_count == 3
    meta = json.loads(cache_metadata.sidecar_path(target).read_text())
    assert meta["details"]["partial_of_job"] is True
    assert meta["execution"]["partial_of_job"] is True
    assert cache_metadata.inspect_cache(target).condition is cache_metadata.CacheCondition.READY


def test_sewing_snap_runs_in_cloud_import(cloud, tmp_path):
    _plan, deformables, package = package_for(cloud, tmp_path, stitch_pairs=((0, 1),),
                                              snap=10.0)
    record = run_to_stored(cloud, package)
    snapshot = load_archived_snapshot(cloud.jobs.archive, package.client_job_id,
                                      record.snapshot_digest)
    target = tmp_path / "sewn.pc2"
    import_result(cloud.jobs.archive, package.client_job_id, snapshot=snapshot,
                  job_id=record.job_id, pc2_paths={"uuid-shirt": target})
    frames = list(pc2.iter_frames(target))
    np.testing.assert_array_equal(frames[1][0], frames[1][1])
    assert target.read_bytes() == local_reference(tmp_path, deformables, 5).read_bytes()


def test_solid_reconstruction_uses_shared_surface_map(tmp_path):
    world = solver_world_matrix(IDENTITY)
    target = solver_results.ExtractionTarget("soft", 2, "SOLID", world)
    surface = cbor_codec.dumps({"kind": "SurfaceMap", "payload": {"version": 2,
                                "maps": {"soft": [[0, 0], [[0.5, 0.5, 0.0],
                                                           [0.0, 0.0, 0.1]],
                                                  [[0, 1, 2]]]}}})
    map_blob = cbor_codec.dumps({"version": 1, "kind": "VertexMap",
                                 "payload": {"soft": [0, 1, 2]}})
    _map, extraction = solver_results.prepare_output_extraction(
        map_blob, surface, (target,), schema_version=1)
    frame = np.asarray(((0, 0, 0), (1, 0, 0), (0, 1, 0)), dtype="<f4")
    positions = solver_results.extract_dynamic_frame(frame, extraction, (target,),
                                                     frame=1)["soft"]
    assert positions.shape == (2, 3)
    np.testing.assert_allclose(positions[0], (0.5, 0.5, 0.0), atol=1e-6)


def test_import_rejects_result_from_another_build(cloud, tmp_path):
    def wrong_build(job, files):
        output = synthetic_executor()(job, files)
        output.build_id = "gaia:other"
        return output
    cloud.backend.executor = wrong_build
    _plan, _deformables, package = package_for(cloud, tmp_path)
    cloud.jobs.upload_and_submit(package.client_job_id)
    cloud.backend.run_pending()
    cloud.jobs.refresh(package.client_job_id)
    with pytest.raises(ResultError, match="gaia:other"):
        cloud.jobs.download(package.client_job_id)


def test_cancel_before_submit_discards_without_server_charge(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    record = cloud.jobs.cancel(package.client_job_id)
    assert record.state is LocalState.DISCARDED
    assert not any(name == "submit" for _a, name in cloud.backend.calls)


def test_result_archive_delete_needs_confirmation(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    run_to_stored(cloud, package)
    with pytest.raises(ResultError):
        cloud.jobs.archive.delete_job(package.client_job_id, confirmed=False)
    assert cloud.jobs.archive.is_complete(package.client_job_id)
    cloud.jobs.archive.delete_job(package.client_job_id, confirmed=True)
    assert not cloud.jobs.archive.folder(package.client_job_id).exists()


def test_registry_holds_no_secrets_or_paths(cloud, tmp_path):
    _plan, _deformables, package = package_for(cloud, tmp_path)
    run_to_stored(cloud, package)
    text = cloud.jobs.registry.path.read_text(encoding="utf-8")
    assert privacy.findings(json.loads(text)) == []
    assert str(tmp_path) not in text


# Error mapping -------------------------------------------------------------------------

def test_every_cloud_failure_maps_to_a_trusted_cnx_code():
    from cloth_next.cloud import errors
    from cloth_next.core.error_codes import ERROR_CODES
    for code in wire.KNOWN_CODES | {"unconfirmed", "upload_paused", "result_corrupt",
                                    "local_package_missing", "build_unverifiable",
                                    "water_mode_unsupported", "insufficient_disk_space"}:
        public, info, text = errors.describe(code)
        assert public in ERROR_CODES and public.startswith("CNX-E2")
        assert info.cause in text
    assert errors.cnx_code("something the server made up") == "CNX-E200"
    # Raw server text never reaches the artist; only a bounded request id.
    _code, _info, text = errors.describe("quote_expired", request_id="req_123")
    assert text.endswith("(Request req_123)")
    _code, _info, text = errors.describe("quote_expired", request_id="x" * 200)
    assert "(Request" not in text
    assert errors.code_of(wire.CloudUnconfirmed("submit")) == "unconfirmed"
    assert errors.code_of(UploadPaused("network")) == "upload_paused"
    assert errors.code_of(wire.CloudError("not-a-known-code")) == "unknown"


# Graded activation -----------------------------------------------------------------

def _snapshot_objects():
    return [{"uuid": "u1", "object_name": "Shirt", "vertex_count": 4,
             "topology_signature": "topo",
             "material_meta": {"fingerprints": {"settings": "S", "geometry": "G"}}}]


@pytest.mark.parametrize("current, settings, geometry, expected", [
    ({"u1": ("present", 4, "topo", None)}, "S", "G", "READY"),
    ({"u1": ("present", 4, "topo", None)}, "S2", "G", "STALE_SETTINGS"),
    ({"u1": ("present", 4, "topo", None)}, "S2", "G2", "STALE_GEOMETRY"),
    ({"u1": ("present", 4, "topo2", None)}, "S", "G", "INCOMPATIBLE"),
    ({"u1": ("present", 5, "topo", None)}, "S", "G", "INCOMPATIBLE"),
    ({}, "S", "G", "INCOMPATIBLE"),
    ({"u1": ("present", 4, "topo", 200.0)}, "S", "G", "NEWER_LOCAL"),
    ({"u1": ("present", 4, "topo", 50.0)}, "S", "G", "READY"),
])
def test_activation_grades_changes(current, settings, geometry, expected):
    from cloth_next.cloud.activation import CurrentObject, Verdict, classify
    now = {uuid: CurrentObject(True, count, topo, local)
           for uuid, (_p, count, topo, local) in current.items()}
    decision = classify(_snapshot_objects(), now, settings_fingerprint=settings,
                        geometry_fingerprint=geometry, submitted_at=100.0)
    assert decision.verdict is Verdict(expected)
    assert decision.may_auto_activate == (expected == "READY")
    assert decision.may_activate == (expected != "INCOMPATIBLE")
