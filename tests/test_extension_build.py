# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import zipfile
import json

import pytest

from tools.build_extension import build_extension


def extension_source(tmp_path):
    source = tmp_path / "cloth_next"
    source.mkdir()
    (source / "__init__.py").write_text("")
    (source / "blender_manifest.toml").write_text(
        'id="cloth_next"\nversion="0.1.0"\nblender_version_min="5.0.0"\n')
    (source / "solver_compatibility.json").write_text("{}")
    (source / "bin").mkdir()
    (source / "bin" / "cloth-next-bake.exe").write_bytes(b"MZfixture")
    (source / "companion_manifest.json").write_text(json.dumps({}))
    return source


def test_build_is_always_solver_free(tmp_path):
    source = extension_source(tmp_path)
    solver = source / "solver" / "windows-x86_64"
    solver.mkdir(parents=True)
    (solver / "ppf-cts-server.exe").write_bytes(b"exe")
    output = tmp_path / "cloth_next-0.1.0-windows-x64.zip"
    build_extension(source, output)
    with zipfile.ZipFile(output) as bundle:
        names = bundle.namelist()
    assert "blender_manifest.toml" in names
    assert "solver_compatibility.json" in names
    assert not any("ppf-cts-server.exe" in name for name in names)
    assert not any(name.startswith("solver/") for name in names)


def test_build_excludes_runtime_state_directories(tmp_path):
    source = extension_source(tmp_path)
    for directory in ("downloads", "managed_solver", "staging", "logs", "__pycache__"):
        (source / directory).mkdir()
        (source / directory / "data.bin").write_bytes(b"x")
    output = tmp_path / "clean.zip"
    build_extension(source, output)
    with zipfile.ZipFile(output) as bundle:
        assert bundle.namelist() == sorted(
            ["__init__.py", "bin/cloth-next-bake.exe", "blender_manifest.toml",
             "companion_manifest.json", "solver_compatibility.json"])


def test_build_has_no_solver_bundling_mode():
    with pytest.raises(TypeError):
        build_extension(None, None, with_solver=True)  # noqa: PT011 — removed API
