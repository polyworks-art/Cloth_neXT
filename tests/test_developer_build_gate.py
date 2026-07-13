# SPDX-License-Identifier: GPL-3.0-or-later

import json

from cloth_next.developer import is_dev_build


def test_dev_build_gate_fails_closed_without_valid_ci_metadata(tmp_path):
    assert is_dev_build(tmp_path) is False
    (tmp_path / "dev_build.json").write_text("not json", encoding="utf-8")
    assert is_dev_build(tmp_path) is False
    (tmp_path / "dev_build.json").write_text(json.dumps({
        "experimental": True, "dev_version": "0.2.0-beta.7"}), encoding="utf-8")
    assert is_dev_build(tmp_path) is False


def test_dev_build_gate_accepts_only_explicit_dev_snapshot(tmp_path):
    (tmp_path / "dev_build.json").write_text(json.dumps({
        "experimental": True, "dev_version": "0.2.0-dev.14"}), encoding="utf-8")
    assert is_dev_build(tmp_path) is True
