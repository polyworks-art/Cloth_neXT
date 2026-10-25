# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS add-on side: profiles, protocol, cache, availability, engine round trips.

Engine round trips run against the staged native engine and are skipped
when this checkout has none (the engine is built in its own repository).
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from cloth_next.tethys import cache as tcache
from cloth_next.tethys import contact_export, engine, profiles, protocol

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- profiles

def test_zone_resolution_uses_face_ownership_and_inheritance():
    zones = [profiles.ZoneWetness(1, "Nylon", True, "WATERPROOF", ()),
             profiles.ZoneWetness(2, "Disabled", False, "LEATHER", ()),
             profiles.ZoneWetness(3, "Plain", True, profiles.INHERIT, ()),
             profiles.ZoneWetness(4, "Wetter", True, profiles.INHERIT, (("water_capacity", 0.9),))]
    owners = (0, 1, 2, 3, 4)
    rows, material_of = profiles.resolve_materials("Cloth", "COTTON", (), zones, owners, [0, 0, 1, 2, 3, 4])
    assert material_of == [0, 0, 1, 0, 0, 2]          # disabled/plain zones inherit row 0
    assert len(rows) == 3
    cotton = profiles.PROFILES["COTTON"]
    assert rows[0][1] == pytest.approx(cotton.water_capacity * 1e-3)
    assert rows[1][3] == pytest.approx(profiles.PROFILES["WATERPROOF"].water_repellency)
    assert rows[2][1] == pytest.approx(0.9e-3)          # only the override differs
    assert rows[2][0] == rows[0][0]


def test_removing_an_override_restores_the_inherited_value():
    owners = (0, 1)
    zone = profiles.ZoneWetness(1, "Z", True, profiles.INHERIT, (("surface_mobility", 0.5),))
    rows, material_of = profiles.resolve_materials("C", "SILK", (), [zone], owners, [0, 1])
    assert material_of == [0, 1] and rows[1][2] == 0.5
    rows, material_of = profiles.resolve_materials("C", "SILK", (), [zone.__class__(1, "Z", True, profiles.INHERIT, ())],
                                                   owners, [0, 1])
    assert material_of == [0, 0] and len(rows) == 1


def test_invalid_overrides_and_ownership_are_refused():
    with pytest.raises(profiles.WetnessError):
        profiles.resolve_materials("C", "COTTON", (("water_repellency", 2.0),), [], (0,), [0])
    with pytest.raises(profiles.WetnessError):
        profiles.resolve_materials("C", "COTTON", (("not_a_property", 1.0),), [], (0,), [0])
    with pytest.raises(profiles.WetnessError):
        profiles.resolve_materials("C", "COTTON", (), [], (7,), [0])


def test_preset_mapping_is_by_fabric_identity_only():
    assert profiles.profile_for_preset("COTTON") == "COTTON"
    assert profiles.profile_for_preset("PRODUCT_GORETEX_PRO_3L") == "WATERPROOF"
    assert profiles.profile_for_preset("MIT_FAUX_FUR") == profiles.DEFAULT_PROFILE
    assert profiles.profile_for_preset(None) == profiles.DEFAULT_PROFILE
    assert set(profiles._PRESET_PROFILES.values()) <= set(profiles.PROFILES)


# ------------------------------------------------- no solver in the add-on

SOLVER_WORDS = ("flux", "advect", "diffus", "evaporat", "absorb", "capillar", "substep", "donor",
                "conservation", "drain")


def test_distributed_python_contains_no_tethys_solver():
    """The add-on may name wetness parameters but must not implement transport."""
    allowed = {"__init__.py", "profiles.py", "protocol.py", "engine.py", "cache.py", "contact_export.py"}
    package = ROOT / "cloth_next" / "tethys"
    assert {p.name for p in package.glob("*.py")} == allowed
    sources = list(package.glob("*.py")) + list((ROOT / "cloth_next" / "blender").glob("tethys_*.py"))
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                name = node.name.lower()
                assert not any(word in name for word in SOLVER_WORDS), f"{path.name}: {node.name}"
    for suffix in ("*.cpp", "*.hpp", "*.h", "*.c", "*.rs"):
        assert not list((ROOT / "cloth_next").rglob(suffix)), suffix


# ------------------------------------------------------------ availability

def _installation(kind="gaia", healthy=True):
    return SimpleNamespace(kind=kind, available=True, compatible=True, verified=True, healthy=healthy)


def test_gaia_gate(monkeypatch):
    from cloth_next.updater import engines
    monkeypatch.setattr(engines, "engine_of", lambda item: engines.GAIA if item.kind == "gaia" else engines.AETHER)
    gaia = SimpleNamespace(selected=_installation())
    assert engine.gaia_availability(gaia, True).available
    assert engine.gaia_availability(gaia, False).reason == "Needs GAIA access"
    assert not engine.gaia_availability(SimpleNamespace(selected=_installation("aether")), True).available
    assert not engine.gaia_availability(SimpleNamespace(selected=None), True).available
    assert not engine.gaia_availability(SimpleNamespace(selected=_installation(healthy=False)), True).available


def test_locate_rejects_tampered_or_foreign_binaries(tmp_path):
    from cloth_next.platform_support import WINDOWS_X64
    (tmp_path / "bin").mkdir()
    binary = tmp_path / "bin" / "TETHYS Engine.exe"
    binary.write_bytes(b"engine")
    manifest = {"schema_version": 1, "filename": "TETHYS Engine.exe", "platform": "windows-x64",
                "file_size": 6, "sha256": hashlib.sha256(b"engine").hexdigest(), "protocol": protocol.PROTOCOL_VERSION}
    (tmp_path / engine.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    assert engine.locate(tmp_path, platform=WINDOWS_X64) == binary
    binary.write_bytes(b"engin3")
    with pytest.raises(engine.EngineUnavailable, match="damaged"):
        engine.locate(tmp_path, platform=WINDOWS_X64)
    manifest["protocol"] = 99
    (tmp_path / engine.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(engine.EngineUnavailable, match="protocol"):
        engine.locate(tmp_path, platform=WINDOWS_X64)
    with pytest.raises(engine.EngineUnavailable, match="not included"):
        engine.locate(tmp_path / "missing", platform=WINDOWS_X64)


# ----------------------------------------------------------- engine round trips

TEST_KID = "tethys-test"   # trusted only by test builds of the engine (seed 00..1f)


def write_gaia_account(root: Path, *, installation="inst_tethys_test", **claims) -> Path:
    """PolyWorks account folder with a GAIA entitlement signed by the test key."""
    import base64
    import time
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    key = crypto.Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    now = int(time.time())
    payload = {"typ": "polyworks.feature-entitlement", "v": 1, "kid": TEST_KID, "sub": "usr_test",
               "inst": installation, "prd": "cloth-next", "feat": ["gaia"], "iat": now - 5,
               "exp": now + 7 * 86400, **claims}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    b64 = lambda data: base64.urlsafe_b64encode(data).decode().rstrip("=")
    root.mkdir(parents=True, exist_ok=True)
    (root / "installation-id").write_text(installation, encoding="utf-8")
    (root / "gaia-entitlement").write_text(b64(raw) + "." + b64(key.sign(raw)), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def gaia_account(tmp_path_factory, monkeypatch):
    """Every engine run in this module has GAIA access (the engine enforces it)."""
    root = write_gaia_account(tmp_path_factory.mktemp("polyworks") / "account")
    monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(root))
    return root


def _test_engine_path():
    import os
    explicit = os.environ.get("CLOTH_NEXT_TETHYS_TEST_ENGINE", "")
    name = "tethys-engine-test.exe" if os.name == "nt" else "tethys-engine-test"
    platform_dir = "windows-x64" if os.name == "nt" else "linux-x64"
    path = Path(explicit) if explicit else ROOT.parent / "TETHYS-Engine" / "build" / platform_dir / name
    return path if path.is_file() else None


def _engine():
    """Test build of the engine: it also trusts the test signing key."""
    path = _test_engine_path()
    if path is None:
        pytest.skip("No TETHYS test engine build (CLOTH_NEXT_TETHYS_TEST_ENGINE)")
    return path


def _production_engine():
    try:
        return engine.locate()
    except engine.EngineUnavailable:
        pytest.skip("TETHYS Engine is not staged in this checkout")


def _sheet(nx, ny, h):
    vertices = np.array([(i * h, 0.0, j * h) for j in range(ny) for i in range(nx)], float)
    triangles, polygon_of = [], []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            triangles += [(a, a + 1, a + nx + 1), (a, a + nx + 1, a + nx)]
            polygon_of += [len(polygon_of) // 2] * 2
    return vertices, np.array(triangles), np.array(polygon_of)


def _write_pc2(path, frames):
    frames = np.asarray(frames, "<f4")
    with open(path, "wb") as stream:
        stream.write(struct.pack("<12siiffi", b"POINTCACHE2\0", 1, frames.shape[1], 0.0, 1.0, frames.shape[0]))
        stream.write(frames.tobytes())


def _scenario(tmp_path, *, zones=True, water=True, frames=24, version=None, detail=1, texture=0):
    vertices, triangles, polygon_of = _sheet(9, 9, 0.02)
    polygons = int(polygon_of.max()) + 1
    # Sheet descends into water (z < 0) and comes back out.
    heights = [0.3 - 0.6 * min(1.0, k / 8) if k < 16 else 0.3 for k in range(frames)]
    pc2 = tmp_path / "cloth.pc2"
    _write_pc2(pc2, [vertices + np.array([0, 0, z]) for z in heights])
    owners = [1 if (p % 8) >= 4 else 0 for p in range(polygons)] if zones else [0] * polygons
    rows, material_of = profiles.resolve_materials(
        "Sheet", "COTTON", (), [profiles.ZoneWetness(1, "Nylon", True, "WATERPROOF", ())] if zones else [],
        owners, polygon_of.tolist())
    env = tmp_path / "water.tethysev"
    if water:
        def blocks():
            for _ in range(frames):
                z = np.arange(30) * 0.05 - 1.0
                influence = np.broadcast_to((z < 0)[:, None, None], (30, 10, 10)).astype(np.float32)
                yield protocol.WaterBlock((-0.1, -0.25, -1.0), (0.05, 0.05, 0.05), influence,
                                          np.zeros((30, 10, 10, 3), np.float32))
        protocol.write_env(env, 1, frames, 24.0, blocks())
    request = protocol.EngineRequest(
        settings=protocol.EngineSettings(fps=24.0, gravity=(0.0, 0.0, -9.81), debug_channels=True),
        first_frame=1, last_frame=frames, pc2_first_sample=0, vertex_count=len(vertices),
        triangles=triangles, polygon_of=polygon_of, polygon_count=polygons,
        material_of=np.array(material_of), materials=rows, matrix_world=np.eye(4),
        pc2_path=str(pc2), env_path=str(env) if water else "", output_path=str(tmp_path / "out.tethys"),
        cancel_path=str(tmp_path / "cancel"), identity=json.dumps({"test": True}),
        detail_level=detail, texture_size=texture,
        uv=(vertices[:, [0, 2]] / vertices[:, [0, 2]].max())[triangles].reshape(-1, 6) if texture else None)
    data = protocol.encode_request(request)
    if version is not None:
        data = data[:8] + struct.pack("<I", version) + data[12:]
    path = tmp_path / "request.tethysrq"
    path.write_bytes(data)
    return path, pc2, np.array(owners)


def _run(executable, request):
    result = subprocess.run([str(executable), "run", str(request)], capture_output=True, text=True, timeout=120)
    events = [json.loads(line[7:]) for line in result.stdout.splitlines() if line.startswith("TETHYS ")]
    return result.returncode, events


def test_engine_bake_conserves_water_and_zones_differ(tmp_path):
    executable = _engine()
    request, pc2, owners = _scenario(tmp_path)
    before = hashlib.sha256(pc2.read_bytes()).hexdigest()
    code, events = _run(executable, request)
    assert code == 0, events
    complete = events[-1]
    assert complete["event"] == "complete"
    total = complete["surface"] + complete["absorbed"] + complete["evaporated"] + complete["drained"]
    assert abs(complete["initial"] + complete["injected"] - total - complete["roundoff"]) <= 1e-12
    with protocol.CacheReader(tmp_path / "out.tethys", verify_all=True) as reader:
        assert (reader.first_frame, reader.last_frame, reader.polygon_count) == (1, 24, 64)
        assert reader.identity == json.dumps({"test": True})
        last = reader.read(24)
        dry = reader.read(1)
    saturation = last.channels["saturation"]
    cotton, nylon = saturation[owners == 0], saturation[owners == 1]
    assert cotton.min() > 0.5 and nylon.max() < 0.5 * cotton.min()
    assert float(dry.channels["saturation"].max()) == 0.0
    assert hashlib.sha256(pc2.read_bytes()).hexdigest() == before   # cloth cache is only read


def test_engine_output_is_deterministic(tmp_path):
    executable = _engine()
    digests = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        request, _pc2, _owners = _scenario(folder)
        assert _run(executable, request)[0] == 0
        # Identical inputs except paths; the cache holds no paths.
        digests.append(hashlib.sha256((folder / "out.tethys").read_bytes()).hexdigest())
    assert digests[0] == digests[1]


def test_engine_rejects_protocol_mismatch(tmp_path):
    executable = _engine()
    request, _pc2, _owners = _scenario(tmp_path, version=protocol.PROTOCOL_VERSION + 1)
    code, events = _run(executable, request)
    assert code == 2 and events[-1]["code"] == "TETHYS-E100"
    assert not (tmp_path / "out.tethys").exists()


def test_engine_cancel_leaves_no_files(tmp_path):
    executable = _engine()
    request, _pc2, _owners = _scenario(tmp_path)
    (tmp_path / "cancel").write_bytes(b"cancel")
    code, events = _run(executable, request)
    assert code == 3 and events[-1]["event"] == "cancelled"
    assert not (tmp_path / "out.tethys").exists()
    assert not (tmp_path / "out.tethys.partial").exists()


def test_corrupted_cache_is_rejected(tmp_path):
    executable = _engine()
    request, _pc2, _owners = _scenario(tmp_path)
    assert _run(executable, request)[0] == 0
    path = tmp_path / "out.tethys"
    data = bytearray(path.read_bytes())
    data[400] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(protocol.ProtocolError):
        with protocol.CacheReader(path, verify_all=True):
            pass
    path.write_bytes(bytes(data[:-30]))
    with pytest.raises(protocol.ProtocolError):
        protocol.CacheReader(path)


def test_no_water_means_no_wetness(tmp_path):
    executable = _engine()
    request, _pc2, _owners = _scenario(tmp_path, water=False)
    code, events = _run(executable, request)
    assert code == 0 and events[-1]["injected"] == 0.0


# ------------------------------------------------------------- cache files

def test_sidecar_authentication_and_owned_removal(tmp_path):
    executable = _engine()
    request, _pc2, _owners = _scenario(tmp_path)
    assert _run(executable, request)[0] == 0
    paths = tcache.CachePaths(tmp_path, "cn_tethys_test")
    (tmp_path / "out.tethys").replace(paths.cache)
    partial = tcache.write_partial(paths, {"identity_json": json.dumps({"test": True}),
                                           "fingerprints": {"combined": "abc"}})
    assert tcache.inspect(paths)[0] is tcache.CacheState.PARTIAL
    tcache.write_complete(paths, partial, images={}, result={})
    assert tcache.inspect(paths, "abc", full=True)[0] is tcache.CacheState.READY
    assert tcache.inspect(paths, "other")[0] is tcache.CacheState.STALE
    record = json.loads(paths.sidecar.read_text("utf-8"))
    record["frames"] = [9, 9]
    paths.sidecar.write_text(json.dumps(record), encoding="utf-8")
    assert tcache.inspect(paths)[0] is tcache.CacheState.CORRUPT
    unrelated = tmp_path / "keep.pc2"
    unrelated.write_bytes(b"x")
    tcache.remove(paths)
    assert not paths.cache.exists() and not paths.sidecar.exists() and unrelated.exists()


# ---------------------------------------------------------- water contact

def test_contact_crop_and_source_requirements(tmp_path):
    from cloth_next.gaia.container import write_water_container
    from cloth_next.gaia.water_field import WaterVelocityField
    influence = np.zeros((20, 20, 20), np.float32)
    influence[:10] = 1.0
    field = WaterVelocityField((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), np.zeros((20, 20, 20, 3), np.float32),
                               influence)
    for sampling, ok in (("all-liquid-particles", True), ("surface-only", False)):
        path = tmp_path / f"{sampling}.gaia"
        write_water_container(path, {"fps": 24.0, "fingerprint": "f", "provider": {"sampling": sampling}},
                              [(1, 0.0, field), (2, 1 / 24, field)])
        if ok:
            source = contact_export.inspect_source(path)
            assert source.frames == (1, 2)
        else:
            with pytest.raises(contact_export.ContactSourceError, match="fluid particles"):
                contact_export.inspect_source(path)
    block = contact_export.crop(field, (-0.2, -0.2, -0.2), (0.2, 0.2, 0.2))
    assert block is not None and block.influence.shape[0] < 20 and block.influence.max() == 1.0
    assert contact_export.crop(field, (5, 5, 5), (6, 6, 6)) is None
    dry = WaterVelocityField((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), np.zeros((20, 20, 20, 3), np.float32),
                             np.zeros((20, 20, 20), np.float32))
    assert contact_export.crop(dry, (-0.2, -0.2, -0.2), (0.2, 0.2, 0.2)) is None


# ------------------------------------------------------------- packaging

def _zip(path, files):
    import zipfile
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o755 << 16
            bundle.writestr(info, data)
    return path


def test_packaged_engine_validation(tmp_path):
    from tools import validate_extension
    companion = {"platform": "windows-x64"}
    base = {"blender_manifest.toml": "id='cloth_next'", "__init__.py": "", "bin/cloth-next-bake.exe": b"c",
            "companion_manifest.json": json.dumps(companion)}
    validate_extension.validate_zip(_zip(tmp_path / "plain.zip", base))   # engine is optional
    engine_bytes = b"tethys"
    manifest = {"schema_version": 1, "filename": "TETHYS Engine.exe", "platform": "windows-x64",
                "file_size": 6, "sha256": hashlib.sha256(engine_bytes).hexdigest(), "protocol": 1}
    good = dict(base, **{"bin/TETHYS Engine.exe": engine_bytes, "tethys_manifest.json": json.dumps(manifest)})
    validate_extension.validate_zip(_zip(tmp_path / "good.zip", good))
    for name, files in (("tampered", dict(good, **{"bin/TETHYS Engine.exe": b"tethyz"})),
                        ("foreign", dict(good, **{"bin/tethys-engine": b"x"})),
                        ("no-manifest", {k: v for k, v in good.items() if k != "tethys_manifest.json"})):
        with pytest.raises(ValueError):
            validate_extension.validate_zip(_zip(tmp_path / f"{name}.zip", files))


def test_killed_engine_leaves_cloth_cache_intact(tmp_path):
    executable = _engine()
    request, pc2, _owners = _scenario(tmp_path, frames=400)
    before = hashlib.sha256(pc2.read_bytes()).hexdigest()
    job = engine.EngineJob(executable, request, tmp_path / "cancel")
    deadline = __import__("time").monotonic() + 30
    while not any(e.get("event") == "progress" for e in job.take_events()):
        assert job.poll() is None and __import__("time").monotonic() < deadline
    job.cancel(grace=0.0)          # hard stop, as after a crash
    assert job.poll() is not None
    assert hashlib.sha256(pc2.read_bytes()).hexdigest() == before
    assert not (tmp_path / "out.tethys").exists()
    # The unpublished partial file is owned scratch and removable at once.
    partial = tmp_path / "out.tethys.partial"
    if partial.exists():
        partial.unlink()


def test_engine_refuses_without_gaia_entitlement(tmp_path, monkeypatch):
    """The shipped engine itself enforces GAIA access; Python cannot bypass it."""
    executable = _production_engine()
    request, pc2, _owners = _scenario(tmp_path)
    for account in (tmp_path / "empty-account",
                    write_gaia_account(tmp_path / "test-key-account")):   # test key: not trusted
        monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(account))
        code, events = _run(executable, request)
        assert code == 2 and events[-1]["code"] == "TETHYS-E140", events
        assert not (tmp_path / "out.tethys").exists()


def test_test_engine_rejects_bad_entitlements(tmp_path, monkeypatch):
    executable = _engine()
    request, _pc2, _owners = _scenario(tmp_path)
    for claims, status in (({"feat": ["cloud"]}, "no_gaia_feature"), ({"prd": "other"}, "wrong_product"),
                           ({"typ": "polyworks.entitlement"}, "wrong_type"),
                           ({"inst": "inst_elsewhere"}, "wrong_installation"), ({"iat": 900, "exp": 1000}, "expired")):
        root = write_gaia_account(tmp_path / status, **claims)
        monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(root))
        code, events = _run(executable, request)
        assert code == 2 and events[-1]["code"] == "TETHYS-E140" and status in events[-1]["message"], events


def test_vendored_engine_binaries_match_their_manifest(tmp_path):
    """Release workflows stage these prebuilt binaries; they must be intact and current."""
    vendor = ROOT / "vendor" / "tethys"
    manifest = json.loads((vendor / "ENGINE.json").read_text("utf-8"))
    assert manifest["protocol"] == protocol.PROTOCOL_VERSION
    assert set(manifest["binaries"]) == set(engine.FILENAMES)
    for platform, entry in manifest["binaries"].items():
        data = (vendor / platform / entry["filename"]).read_bytes()
        assert entry["filename"] == engine.FILENAMES[platform]
        assert len(data) == entry["file_size"] and hashlib.sha256(data).hexdigest() == entry["sha256"]
    from cloth_next.platform_support import platform_spec
    from tools import stage_tethys
    host = platform_spec().blender_platform
    extension = tmp_path / "extension"
    (extension / "bin").mkdir(parents=True)
    staged = stage_tethys.stage(vendor / host / engine.FILENAMES[host], extension)   # probes --version
    assert engine.locate(extension) == staged


def test_detail_and_uv_texture(tmp_path):
    executable = _engine()
    request, _pc2, owners = _scenario(tmp_path, detail=4, texture=64)
    code, events = _run(executable, request)
    assert code == 0, events
    start = next(e for e in events if e["event"] == "start")
    assert start["detail"] == 4 and start["texture"] == 64 and start["triangles"] == 128 * 16
    with protocol.CacheReader(tmp_path / "out.tethys", verify_all=True) as reader:
        assert reader.texture_size == 64 and reader.detail_level == 4
        frame = reader.read(reader.last_frame)
    texture = frame.channels["texture"]
    assert texture.shape == (64, 64, 2)
    # Left columns are cotton (saturated), right columns waterproof (dry inside).
    assert texture[32, 4, 1] > 0.2 and texture[32, 60, 1] < 0.5 * texture[32, 4, 1]
    complete = events[-1]
    total = complete["surface"] + complete["absorbed"] + complete["evaporated"] + complete["drained"]
    assert abs(complete["initial"] + complete["injected"] - total - complete["roundoff"]) <= 1e-12
