# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine backend (pure parts and engine round trips).

Engine round trips run against a real ``AETHER Engine.exe`` named by
``CLOTH_NEXT_AETHER_ENGINE`` and are skipped without it.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import pytest

from cloth_next.aether_engine import bake, client, engine, materials
from cloth_next.bake import cache_metadata, pc2
from cloth_next.core.error_codes import ERROR_CODES
from cloth_next.materials.models import ShellMaterialSettings
from cloth_next.materials.presets import builtin_presets

ENGINE = os.environ.get(engine.DEVELOPER_OVERRIDE, "")
needs_engine = pytest.mark.skipif(not ENGINE or not Path(ENGINE).is_file(),
                                  reason="set CLOTH_NEXT_AETHER_ENGINE to a built AETHER Engine.exe")


# ---------------------------------------------------------------- materials
def test_membrane_and_bending_are_derived_not_copied():
    m = ShellMaterialSettings(surface_weight=0.25, stretch_resistance=4000.0, bend_resistance=2.0,
                              sideways_response=0.3, surface_grip=0.6, collision_gap=0.004)
    a = materials.map_material(m)
    assert a.youngs == pytest.approx(4000.0 * 0.25)          # undo PPF density normalization
    assert a.bend == pytest.approx(2.0 * 0.25 / 14.3491 * 4.4482216152605 * 0.0254 ** 2 * 0.6)
    assert a.poisson == 0.3 and a.density == 0.25
    assert a.friction == pytest.approx(0.3) and a.thickness == pytest.approx(0.002)


def test_measured_bending_presets_map_to_their_measurement():
    """MIT presets: AETHER bending = the measured bending stiffness in SI."""
    checked = 0
    for preset in builtin_presets():
        measured = getattr(preset, "measured_bending_stiffness_lbf_in2", None)
        if not measured or preset.settings.model != "FABRIC":
            continue
        # only the bending mapping is checked here: switch off controls AETHER refuses
        settings = replace(preset.settings, stretch_limit_enabled=False)
        a = materials.map_material(settings)
        assert materials.physical_bending(settings) == pytest.approx(measured * materials.LBF_IN2_TO_NM2,
                                                                     rel=0.02), preset.identifier
        assert a.bend == pytest.approx(materials.physical_bending(settings) * 0.6)
        checked += 1
    assert checked >= 5


def test_stretch_limit_switched_off_is_reported_not_enforced(monkeypatch):
    monkeypatch.setenv("CLOTH_NEXT_AETHER_STRAIN_LIMIT", "0")
    m = ShellMaterialSettings(stretch_limit_enabled=True, maximum_stretch_percent=4.0)
    assert materials.map_material(m).strain_limit == 0.0          # baked without it
    assert materials.not_enforced(m) and "not enforced" in materials.not_enforced(m)[0]


def test_stretch_limit_enforced_by_default(monkeypatch):
    monkeypatch.delenv("CLOTH_NEXT_AETHER_STRAIN_LIMIT", raising=False)
    m = ShellMaterialSettings(stretch_limit_enabled=True, maximum_stretch_percent=4.0)
    mapped = materials.map_material(m)
    assert mapped.strain_limit == pytest.approx(0.04)
    assert mapped.as_protocol()["strain_limit"] == pytest.approx(0.04)
    assert materials.not_enforced(m) == []
    off = materials.map_material(ShellMaterialSettings(stretch_limit_enabled=False, maximum_stretch_percent=4.0))
    assert off.strain_limit == 0.0 and "strain_limit" not in off.as_protocol()


def test_gaia_bending_factor_follows_mesh_resolution(monkeypatch):
    monkeypatch.delenv("CLOTH_NEXT_AETHER_GAIA_BENDING", raising=False)
    for edge, factor in materials.GAIA_BENDING_POINTS:
        assert materials.gaia_bending_factor(edge) == pytest.approx(factor, rel=1e-6)
    assert materials.gaia_bending_factor(0.0104) > materials.gaia_bending_factor(0.0208) > materials.gaia_bending_factor(0.0417)
    assert materials.gaia_bending_factor(0.001) == 1.0          # very fine meshes keep the mapped bending
    assert materials.gaia_bending_factor(1.0) == pytest.approx(0.005)
    assert materials.gaia_bending_factor(0.0) == 1.0
    monkeypatch.setenv("CLOTH_NEXT_AETHER_GAIA_BENDING", "0")
    assert materials.gaia_bending_factor(0.0417) == 1.0


@pytest.mark.parametrize("change,label", [
    ({"stretch_plasticity_enabled": True}, "Permanent Stretch"),
    ({"shrink_percent": 3.0}, "Shrink"),
    ({"surface_offset": 0.001}, "Surface Offset"),
    ({"model": "SHAPE_PRESERVING"}, "Shape-Preserving"),
])
def test_unsupported_controls_are_refused_not_approximated(change, label):
    values = {"stretch_limit_enabled": False, "maximum_stretch_percent": 5.0}
    values.update(change)
    if change.get("stretch_limit_enabled"):
        values["maximum_stretch_percent"] = 5.0
    m = ShellMaterialSettings(**values)
    with pytest.raises(materials.UnsupportedMaterial, match=label):
        materials.map_material(m)


# ---------------------------------------------------------------- discovery and errors
def _staged(tmp_path: Path, data: bytes, **override) -> Path:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "AETHER Engine.exe").write_bytes(data)
    manifest = {"schema_version": 1, "filename": "AETHER Engine.exe", "platform": "windows-x64",
                "file_size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "protocol": 1,
                "api": list(engine.API_VERSION), "engine_version": "0.4.6-dev", "build_id": "0" * 16}
    manifest.update(override)
    (tmp_path / engine.MANIFEST).write_text(json.dumps(manifest))
    return tmp_path


def test_locate_authenticates_the_staged_engine(tmp_path, monkeypatch):
    monkeypatch.delenv(engine.DEVELOPER_OVERRIDE, raising=False)
    root = _staged(tmp_path, b"engine")
    path, override = engine.locate(root, platform="windows-x64")
    assert path.name == "AETHER Engine.exe" and not override
    (root / "bin" / "AETHER Engine.exe").write_bytes(b"tampered")
    with pytest.raises(engine.EngineUnavailable, match="damaged") as exc:
        engine.locate(root, platform="windows-x64")
    assert exc.value.code == "CNX-E131"


def test_locate_rejects_other_api_and_missing_engine(tmp_path, monkeypatch):
    monkeypatch.delenv(engine.DEVELOPER_OVERRIDE, raising=False)
    with pytest.raises(engine.EngineUnavailable, match="not included") as exc:
        engine.locate(tmp_path, platform="windows-x64")
    assert exc.value.code == "CNX-E131"
    root = _staged(tmp_path, b"engine", api=[2, 0])
    with pytest.raises(engine.EngineUnavailable) as exc:
        engine.locate(root, platform="windows-x64")
    assert exc.value.code == "CNX-E132"


def test_every_engine_status_maps_to_a_registered_cnx_code():
    for name in engine.STATUS_CODES:
        code, message = engine.map_error(name, "detail")
        assert code in ERROR_CODES and message.endswith(": detail")
    for exit_code in (2, 3, 5, 9):
        assert engine.exit_reason(exit_code)[0] in ERROR_CODES


# ---------------------------------------------------------------- engine round trips
def _grid(n, size, origin, u, v):
    o, u, v = (np.asarray(a, float) for a in (origin, u, v))
    pts = np.array([o + u * size * i / n + v * size * j / n for j in range(n + 1) for i in range(n + 1)],
                   np.float32)
    tris = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            tris += [a, a + 1, a + n + 2, a, a + n + 2, a + n + 1]
    return pts, np.asarray(tris, np.int32)


def _box(center, half):
    c = np.asarray(center, np.float32)
    v = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], np.float32) * half + c
    f = [0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1, 2, 3, 7, 2, 7, 6, 0, 2, 6, 0, 6, 4, 1, 5, 7, 1, 7, 3]
    return v, np.asarray(f, np.int32)


def _request(tmp: Path, *, quality="normal", start=10, end=46, shift=0.0) -> bake.BakeRequest:
    n = 10
    mat = materials.map_material(ShellMaterialSettings(surface_weight=0.2, stretch_resistance=10000.0,
                                                       bend_resistance=1.0, collision_gap=0.004))
    cloths = []
    for k, x0 in enumerate((-0.44, 0.04)):
        world, tris = _grid(n, 0.4, (x0, 0.0, 0.25 + shift), (1, 0, 0), (0, 0, 1))
        matrix = np.eye(4)
        matrix[:3, 3] = (0.1 * k, 0.0, 0.05)   # object transform: PC2 stays object-local
        local = (world - matrix[:3, 3]).astype(np.float32)
        top = n * (n + 1)
        meta = bake.cache_meta(settings_fp="s" * 64, geometry_fp="g" * 64, combined_fp="c" * 64,
                               topology="t" * 64, object_identity={"k": k}, scene_identity={"s": 1},
                               vertex_count=len(world), frame_count=end - start + 1,
                               details={"aether": {"mapping_version": materials.MAPPING_VERSION}},
                               cloth_next_version="test", blender_version="test")
        cloths.append(bake.ClothInput(f"uuid-{k}", f"Panel{k}", world, local, tris, matrix, mat,
                                      np.asarray([top] if k == 0 else [top + n], np.int32),
                                      tmp / f"Panel{k}.pc2", meta))
    steps = end - start
    box_v, box_t = _box((-0.2, 0.12, 0.35), 0.06)
    frames = np.stack([box_v + np.asarray((0.012 * (f + 1), 0.0, 0.0), np.float32) for f in range(steps)])
    pin_targets = np.stack([np.asarray([[0.22 + 0.002 * (f + 1), 0.0, 0.65 + shift]], np.float32)
                            for f in range(steps)])
    pins = (bake.PinTrack(1, np.asarray([n * (n + 1) + n // 2], np.int32), 0.0, pin_targets),)
    seams = (bake.SeamInput(0, 1, tuple(j * (n + 1) + n for j in range(n + 1)),
                            tuple(j * (n + 1) for j in range(n + 1)),
                            materials.seam_stiffness(100.0, mat, mat), 24),)
    return bake.BakeRequest(tuple(cloths), (bake.ColliderInput("Box", box_t, box_v, frames),), pins, seams,
                            quality, 24.0, (0.0, 0.0, -9.81), True, start, end, tmp / "aether_ckpt",
                            tmp / "aether_recovery.json", checkpoint_interval=12)


def _run(request, **kw) -> bake.BakeRunner:
    r = bake.BakeRunner(request, Path(ENGINE), **kw)
    r.join(300)
    assert r.done
    return r


@needs_engine
def test_bake_writes_complete_verified_caches(tmp_path):
    req = _request(tmp_path)
    r = _run(req)
    assert r.error is None, r.error and (r.error.code, str(r.error), r.error.details)
    for c in req.cloths:
        header = pc2.read_header(c.pc2_path)
        assert header.frame_count == req.output_count == 37 and header.start_frame == 0.0
        first = np.frombuffer(c.pc2_path.read_bytes()[32:32 + len(c.rest_local) * 12], "<f4").reshape(-1, 3)
        assert np.array_equal(first, c.rest_local)        # sample 0 = rest (Bake Start)
        inspection = cache_metadata.inspect_cache(c.pc2_path, settings_fingerprint="s" * 64)
        assert inspection.usable, inspection.message
        assert inspection.metadata["identities"]["solver"]["engine"] == "AETHER Engine"
    assert not req.recovery_path.exists() and not req.checkpoint_dir.exists()


@needs_engine
def test_killed_engine_resumes_bit_identically(tmp_path):
    ref = _request(tmp_path / "ref")
    assert _run(ref).error is None
    req = _request(tmp_path / "run")
    procs = []
    r = bake.BakeRunner(req, Path(ENGINE), on_process=procs.append)
    deadline = time.monotonic() + 120
    while r.progress.frames_done < 28 and time.monotonic() < deadline and not r.done:
        time.sleep(0.01)
    procs[0].proc.kill()                                  # forced termination mid-bake
    r.join(60)
    assert r.error is not None and r.error.code == "CNX-E164"
    record = bake.load_recovery(req.recovery_path)
    assert bake.check_recovery(record, req) == ""
    resumed = _run(req, resume=True)
    assert resumed.error is None, (resumed.error.code, str(resumed.error))
    assert resumed.progress.resumed_from == 24 and resumed.progress.verified >= 1
    for a, b in zip(ref.cloths, req.cloths):
        assert a.pc2_path.read_bytes() == b.pc2_path.read_bytes()


@needs_engine
def test_device_loss_is_reported_and_resumable(tmp_path):
    req = _request(tmp_path)
    r = _run(req, testing=True, env={"AETHER_ENGINE_TEST_FAULT_FRAME": "15"})
    assert r.error is not None and r.error.code == "CNX-E164" and "GPU was lost" in str(r.error)
    on_disk = pc2.StreamingPc2Writer(req.cloths[0].pc2_path, vertex_count=len(req.cloths[0].rest_local),
                                     frame_count=req.output_count,
                                     resume_path=bake.partial_path(req.cloths[0].pc2_path))
    cached = on_disk.frames_written - 1
    on_disk.preserve()
    resumed = _run(req, resume=True)
    assert resumed.error is None, (resumed.error.code, str(resumed.error))
    # the engine ran ahead of the cache: only a checkpoint the cache covers is used
    assert resumed.progress.resumed_from == (12 if cached >= 12 else 0)
    assert resumed.progress.verified == cached - resumed.progress.resumed_from


@needs_engine
def test_cancel_preserves_a_resumable_partial(tmp_path):
    req = _request(tmp_path, end=200)
    r = bake.BakeRunner(req, Path(ENGINE))
    while r.progress.frames_done < 5 and not r.done:
        time.sleep(0.01)
    r.cancel()
    r.join(60)
    assert r.cancelled and r.error is None
    assert bake.partial_path(req.cloths[0].pc2_path).exists() and req.recovery_path.exists()
    assert not req.cloths[0].pc2_path.exists()           # nothing published


@needs_engine
def test_incompatible_recovery_is_rejected(tmp_path):
    req = _request(tmp_path)
    procs = []
    r = bake.BakeRunner(req, Path(ENGINE), on_process=procs.append)
    while r.progress.frames_done < 14 and not r.done:
        time.sleep(0.01)
    procs[0].proc.kill()
    r.join(60)
    record = bake.load_recovery(req.recovery_path)
    assert "quality preset changed" in bake.check_recovery(record, _request(tmp_path, quality="fitting"))
    assert "scene changed" in bake.check_recovery(record, _request(tmp_path, shift=0.01))
    assert "Bake End changed" in bake.check_recovery(record, _request(tmp_path, end=50))
