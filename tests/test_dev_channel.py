# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import json
import pytest
from tools.prepare_dev_build import prepare
from tools.build_extension_repository import generate_single_candidate_index

def test_prepare_dev_build_updates_only_isolated_version_metadata(tmp_path):
    root=tmp_path; package=root/"cloth_next"; package.mkdir()
    (package/"blender_manifest.toml").write_text('id="cloth_next"\nversion = "0.2.0-beta.6"\n')
    (package/"solver_compatibility.json").write_text('{"cloth_next_version":"0.2.0-beta.6"}')
    prepare(root,"0.2.0-dev.1","a"*40,"123")
    assert '0.2.0-dev.1' in (package/"blender_manifest.toml").read_text()
    assert json.loads((package/"solver_compatibility.json").read_text())["cloth_next_version"]=="0.2.0-dev.1"
    metadata=json.loads((package/"dev_build.json").read_text())
    assert metadata["experimental"] is True and metadata["source_commit"]=="a"*40

def test_prepare_rejects_invalid_or_reused_style(tmp_path):
    with pytest.raises(ValueError): prepare(tmp_path,"0.2.0-beta.7","a"*40,"1")

def test_publish_workflow_cannot_tag_release_or_touch_public_channels():
    text=(Path(__file__).parents[1]/".github/workflows/publish-dev.yml").read_text()
    assert "gh release" not in text and "git tag" not in text
    assert "diff --cached --name-only" in text
    assert "dev/*" in text
    assert "generate_single_candidate_index" in text
    assert "candidates.Count -ne 1" in text
    assert "LastWriteTimeUtc" not in text
    assert "[regex]::Match($_.Name,'dev\\.(\\d+)')" in text


def test_single_candidate_index_keeps_retained_archives(tmp_path, monkeypatch):
    repository = tmp_path / "dev"; repository.mkdir()
    archives = []
    for number in range(1, 6):
        archive = repository / f"cloth_next-0.2.0-dev.{number}-windows-x64.zip"
        archive.write_bytes(f"immutable-{number}".encode())
        archives.append(archive)

    def fake_generate(_blender, directory):
        names = sorted(path.name for path in directory.glob("*.zip"))
        data = [{"id": "cloth_next", "version": "0.2.0-dev.5",
                 "archive_url": f"./{names[0]}"}]
        index = directory / "index.json"
        index.write_text(json.dumps({"version": "v1", "data": data}))
        return index

    monkeypatch.setattr("tools.build_extension_repository.generate_index",
                        fake_generate)
    index = generate_single_candidate_index(
        "blender", repository, archives[-1], tmp_path / "stage")
    payload = json.loads(index.read_text())
    assert payload["data"] == [{
        "id": "cloth_next", "version": "0.2.0-dev.5",
        "archive_url": "./cloth_next-0.2.0-dev.5-windows-x64.zip"}]
    assert [path.read_bytes() for path in archives] == [
        f"immutable-{number}".encode() for number in range(1, 6)]


def test_index_repair_runs_real_blender_and_changes_only_index():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/repair-dev-index.yml").read_text()
    assert "BLENDER_VERSION: \"5.1.2\"" in workflow
    assert "run_blender_dev_repository_regression.py" in workflow
    assert "duplicate-dev" in workflow and "repaired-dev" in workflow
    assert "dev/index.json" in workflow
    assert "changed.Count -ne 1" in workflow
    probe = (root / "tools/blender_dev_repository_probe.py").read_text()
    for source in ("repository_candidate", "installed_manifest",
                   "loaded_manifest", "update_offered"):
        assert source in probe
