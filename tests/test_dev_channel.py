# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import json
import pytest
from tools.prepare_dev_build import prepare

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
