# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import zipfile
from pathlib import Path

import pytest

from cloth_next.ppf.bootstrap import (
    atomic_replace_directory, copy_directory, find_license_files,
    safe_extract_zip, write_source_metadata,
)
from cloth_next.ppf.layout import BundledSolverLayout


def test_safe_archive_extraction(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("runtime/file.dll", b"dll")
    target = tmp_path / "out"
    safe_extract_zip(archive, target)
    assert (target / "runtime/file.dll").read_bytes() == b"dll"


@pytest.mark.parametrize("member", ["../escape", "/absolute", "a/../../escape"])
def test_archive_traversal_is_rejected(tmp_path, member):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, b"bad")
    with pytest.raises(ValueError):
        safe_extract_zip(archive, tmp_path / "out")


def test_directory_copy_rejects_symlinks_when_supported(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    try:
        (source / "link").symlink_to(tmp_path / "elsewhere")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        copy_directory(source, tmp_path / "target")


def test_atomic_target_exchange(tmp_path):
    target = tmp_path / "solver"
    staged = tmp_path / "staged"
    target.mkdir(); staged.mkdir()
    (target / "old").write_text("old")
    (staged / "new").write_text("new")
    atomic_replace_directory(staged, target)
    assert (target / "new").is_file()
    assert not list(tmp_path.glob(".backup-*"))


def test_source_metadata_redacts_path_and_records_files(tmp_path):
    (tmp_path / "ppf-cts-server.exe").write_bytes(b"exe")
    (tmp_path / "LICENSES").mkdir()
    (tmp_path / "LICENSES/LICENSE").write_text("license")
    layout = BundledSolverLayout.from_root(tmp_path)
    write_source_metadata(layout, source_type="local_directory", source_url=None,
        source_label="redacted", versions=("0.1.0", "0.11", "1"),
        health_passed=True, upstream_commit="commit")
    metadata = json.loads(layout.source_metadata_path.read_text())
    assert metadata["source_path"] == "redacted"
    assert str(tmp_path) not in layout.source_metadata_path.read_text()
    assert metadata["files"]["ppf-cts-server.exe"]["size"] == 3
    assert find_license_files(tmp_path)

