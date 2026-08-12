# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cloth_next.ppf import solver_overlay


def test_face_friction_overlay_is_idempotent(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    decoder = frontend / "_decoder_.py"
    scene = frontend / "_scene_.py"
    build_worker = frontend / "build_worker.py"
    decoder.write_text(
        "prefix\n" + solver_overlay._DECODER_NEEDLE + "suffix\n",
        encoding="utf-8")
    scene.write_text(
        "prefix\n" + solver_overlay._SCENE_SIGNATURE
        + "body\n" + solver_overlay._SCENE_EXTEND
        + solver_overlay._SCENE_SHELL + solver_overlay._VIOLATION_NEEDLE
        + "suffix\n", encoding="utf-8")
    build_worker.write_text(
        "prefix\n" + solver_overlay._BUILD_WORKER_NEEDLE + "suffix\n",
        encoding="utf-8")

    solver_overlay.apply_managed_solver_overlay(tmp_path)
    first_decoder = decoder.read_text(encoding="utf-8")
    first_scene = scene.read_text(encoding="utf-8")
    solver_overlay.apply_managed_solver_overlay(tmp_path)

    assert "face_friction" in first_decoder
    assert "per_element" in first_scene
    assert "combined_tri" in first_scene
    assert "combined_pair" in first_scene
    assert "exact if exact else original_intersections" in first_scene
    assert "self._has_self_intersection = False" in first_scene
    assert "all_violations = []" in first_scene
    assert "PPF_CTS_DATA_ROOT" in build_worker.read_text(encoding="utf-8")
    assert decoder.read_text(encoding="utf-8") == first_decoder
    assert scene.read_text(encoding="utf-8") == first_scene


def test_existing_v2_intersection_overlay_is_upgraded(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    decoder = frontend / "_decoder_.py"
    scene = frontend / "_scene_.py"
    build_worker = frontend / "build_worker.py"
    decoder.write_text(
        "prefix\n" + solver_overlay._DECODER_REPLACEMENT + "suffix\n",
        encoding="utf-8")
    scene.write_text(
        "prefix\n" + solver_overlay._SCENE_SIGNATURE_REPLACEMENT
        + "body\n" + solver_overlay._SCENE_EXTEND_REPLACEMENT
        + solver_overlay._SCENE_SHELL_REPLACEMENT
        + solver_overlay._VIOLATION_REPLACEMENT_V2
        + "suffix\n", encoding="utf-8")
    (tmp_path / ".cloth-next-face-friction-intersection-preview-v2").write_text(
        "face-friction-intersection-preview-v2\n", encoding="ascii")
    build_worker.write_text(
        solver_overlay._BUILD_WORKER_NEEDLE, encoding="utf-8")

    solver_overlay.apply_managed_solver_overlay(tmp_path)

    upgraded = scene.read_text(encoding="utf-8")
    assert solver_overlay._VIOLATION_REPLACEMENT in upgraded
    assert solver_overlay._VIOLATION_REPLACEMENT_V2 not in upgraded
    assert (tmp_path / (
        f".cloth-next-{solver_overlay.OVERLAY_VERSION}")).is_file()


def test_existing_v3_intersection_overlay_is_upgraded(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    decoder = frontend / "_decoder_.py"
    scene = frontend / "_scene_.py"
    (frontend / "build_worker.py").write_text(
        solver_overlay._BUILD_WORKER_NEEDLE, encoding="utf-8")
    decoder.write_text(
        solver_overlay._DECODER_REPLACEMENT, encoding="utf-8")
    scene.write_text(
        solver_overlay._SCENE_SIGNATURE_REPLACEMENT
        + solver_overlay._SCENE_EXTEND_REPLACEMENT
        + solver_overlay._SCENE_SHELL_REPLACEMENT
        + solver_overlay._VIOLATION_REPLACEMENT_V3,
        encoding="utf-8")

    solver_overlay.apply_managed_solver_overlay(tmp_path)

    upgraded = scene.read_text(encoding="utf-8")
    assert 'result.get("self_intersections", ())' in upgraded
    assert solver_overlay._VIOLATION_REPLACEMENT in upgraded


def test_existing_v4_overlay_gains_unverified_flag_suppression(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "_decoder_.py").write_text(
        solver_overlay._DECODER_REPLACEMENT, encoding="utf-8")
    (frontend / "build_worker.py").write_text(
        solver_overlay._BUILD_WORKER_NEEDLE, encoding="utf-8")
    scene = frontend / "_scene_.py"
    scene.write_text(
        solver_overlay._SCENE_SIGNATURE_REPLACEMENT
        + solver_overlay._SCENE_EXTEND_REPLACEMENT
        + solver_overlay._SCENE_SHELL_REPLACEMENT
        + solver_overlay._VIOLATION_REPLACEMENT_V4,
        encoding="utf-8")

    solver_overlay.apply_managed_solver_overlay(tmp_path)

    upgraded = scene.read_text(encoding="utf-8")
    assert "Without a confirmed pair" in upgraded
    assert "self._has_self_intersection = False" in upgraded
    assert "original_intersections = []" in upgraded


def test_protocol_013_uses_only_verified_upstream_integration(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "_decoder_.py").write_text("decoder\n", encoding="utf-8")
    scene = frontend / "_scene_.py"
    worker = frontend / "build_worker.py"
    scene.write_text(
        'all_violations = result["violations"]\n'
        'raise ValidationError(result["combined_message"], '
        'violations=all_violations)\n', encoding="utf-8")
    worker.write_text(
        'json.dump({"violations": violations}, fp)\n', encoding="utf-8")

    solver_overlay.apply_solver_overlay(
        tmp_path, protocol_version="0.13", schema_version="2",
        official_release_tag="2026-07-26-22-53", managed=True)

    assert scene.read_text(encoding="utf-8").startswith("all_violations")
    assert (tmp_path / ".cloth-next-upstream-integration-0.13-schema-2").is_file()
    assert not (tmp_path / (
        f".cloth-next-{solver_overlay.OVERLAY_VERSION}")).exists()


def test_protocol_018_uses_exact_verified_noop_recipe(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    scene = frontend / "_scene_.py"
    decoder = frontend / "_decoder_.py"
    worker = frontend / "build_worker.py"
    scene.write_text(
        'all_violations = result["violations"]\n'
        'raise ValidationError(result["combined_message"], '
        'violations=all_violations)\n'
        'statistics_input_path = os.path.join(path, "statistics_input.cbor")\n',
        encoding="utf-8")
    decoder.write_text(
        'elif key == "lock-translation":\n'
        'elif key == "lock-rotation":\n'
        'elif key == "lock-rotation-prohibit-axis":\n', encoding="utf-8")
    worker.write_text(
        'json.dump({"violations": violations}, fp)\n', encoding="utf-8")

    before = (scene.read_text(encoding="utf-8"),
              decoder.read_text(encoding="utf-8"),
              worker.read_text(encoding="utf-8"))
    solver_overlay.apply_solver_overlay(
        tmp_path, protocol_version="0.18", schema_version="2",
        official_release_tag="2026-08-12-15-47", managed=True)

    assert before == (scene.read_text(encoding="utf-8"),
                      decoder.read_text(encoding="utf-8"),
                      worker.read_text(encoding="utf-8"))
    assert (tmp_path / ".cloth-next-upstream-integration-0.18-schema-2").is_file()


def test_protocol_018_recipe_fails_closed_on_modified_frontend(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "_scene_.py").write_text("modified", encoding="utf-8")
    (frontend / "_decoder_.py").write_text("modified", encoding="utf-8")
    (frontend / "build_worker.py").write_text("modified", encoding="utf-8")

    with pytest.raises(solver_overlay.SolverOverlayError, match="anchors"):
        solver_overlay.apply_solver_overlay(
            tmp_path, protocol_version="0.18", schema_version="2",
            official_release_tag="2026-08-12-15-47", managed=True)


def test_unknown_or_external_release_is_not_patched(tmp_path):
    original = "untouched"
    (tmp_path / "frontend").mkdir()
    scene = tmp_path / "frontend" / "_scene_.py"
    scene.write_text(original, encoding="utf-8")
    solver_overlay.apply_solver_overlay(
        tmp_path, protocol_version="0.13", schema_version="2",
        official_release_tag=None, managed=False)
    assert scene.read_text(encoding="utf-8") == original
    with pytest.raises(solver_overlay.SolverOverlayError):
        solver_overlay.apply_solver_overlay(
            tmp_path, protocol_version="0.13", schema_version="2",
            official_release_tag="unknown", managed=True)
