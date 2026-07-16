# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import json
import pytest
from tools.prepare_dev_build import prepare
from tools.build_extension_repository import generate_single_candidate_index
from tools.run_blender_dev_repository_regression import validate_results

def test_prepare_dev_build_updates_only_isolated_version_metadata(tmp_path):
    root=tmp_path; package=root/"cloth_next"; package.mkdir()
    (package/"blender_manifest.toml").write_text('id="cloth_next"\nversion = "0.2.0-beta.6"\n')
    (package/"solver_compatibility.json").write_text('{"cloth_next_version":"0.2.0-beta.6"}')
    prepare(root,"0.3.21","a"*40,"123")
    assert '0.3.21' in (package/"blender_manifest.toml").read_text()
    assert json.loads((package/"solver_compatibility.json").read_text())["cloth_next_version"]=="0.3.21"
    metadata=json.loads((package/"dev_build.json").read_text())
    assert metadata["experimental"] is True and metadata["source_commit"]=="a"*40

def test_prepare_accepts_next_release_line(tmp_path):
    package=tmp_path/"cloth_next"; package.mkdir()
    (package/"blender_manifest.toml").write_text('version = "0.2.0-beta.6"\n')
    (package/"solver_compatibility.json").write_text('{"cloth_next_version":"0.2.0-beta.6"}')
    prepare(tmp_path,"0.3.21","b"*40,"456")
    assert 'version = "0.3.21"' in (package/"blender_manifest.toml").read_text()

def test_prepare_rejects_invalid_or_reused_style(tmp_path):
    with pytest.raises(ValueError): prepare(tmp_path,"0.2.0-beta.7","a"*40,"1")
    with pytest.raises(ValueError): prepare(tmp_path,"00.3.21","a"*40,"1")
    with pytest.raises(ValueError): prepare(tmp_path,"0.3.0","a"*40,"1")

def test_publish_workflow_cannot_tag_release_or_touch_public_channels():
    text=(Path(__file__).parents[1]/".github/workflows/publish-dev.yml").read_text()
    assert "gh release" not in text and "git tag" not in text
    assert "diff --cached --name-only" in text
    assert "dev/*" in text
    assert "generate_single_candidate_index" in text
    assert "candidates.Count -ne 1" in text
    assert "LastWriteTimeUtc" not in text
    assert "Stable=[int]$current.Groups[1].Value" in text
    assert "Beta=[int]$current.Groups[2].Value" in text
    assert "Dev=[int]$current.Groups[3].Value" in text
    assert "Expression='Stable'" in text and "Expression='Dev'" in text
    assert "$old.File | Remove-Item -Force" in text
    assert "STABLE.BETA.DEV" in text
    assert "^0\\.2\\.0-dev" not in text


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


def test_release_repository_main_uses_single_candidate_generation():
    source = (Path(__file__).parents[1] / "tools" /
              "build_extension_repository.py").read_text()
    main = source[source.index("def main()") :]
    assert "generate_single_candidate_index(" in main
    assert "generate_index(args.blender, channel_dir)" not in main


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


def test_release_repository_repair_uses_verified_asset_and_single_candidate():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/repair-release-index.yml").read_text()
    assert "options: [dev, beta, stable]" in workflow
    assert "release_visible_in" in workflow
    assert "REPAIR_RELEASE_INDEX" in workflow
    assert "run_blender_dev_repository_regression.py" in workflow
    assert "--source-may-be-single" in workflow
    assert "candidates.Count -ne 1" in workflow
    assert "check_release_manifest" in workflow
    assert "check_sha256sums" in workflow
    assert "release manifest commit does not match immutable tag" in workflow
    assert "$existing=Test-Path -LiteralPath $channel" in workflow
    assert "New-Item -ItemType Directory -Force $channel" in workflow
    assert "changed files outside the selected verified repository" in workflow
    assert "push origin HEAD:gh-pages" in workflow
    assert "Remove-Item" not in workflow


def test_release_repair_accepts_clean_existing_repository():
    source = {"repository_candidate": "0.4.0", "duplicate_count": 1,
              "installed_manifest": "0.4.0", "loaded_manifest": "0.4.0",
              "update_offered": False}
    repaired = {"repository_candidate": "1.0.0", "duplicate_count": 1,
                "installed_manifest": "1.0.0", "loaded_manifest": "1.0.0",
                "update_offered": False}
    validate_results(source, repaired, "1.0.0", require_duplicate=False)
    source["loaded_manifest"] = "0.3.0"
    with pytest.raises(AssertionError, match="does not match"):
        validate_results(source, repaired, "1.0.0", require_duplicate=False)
