# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from cloth_next.ppf import solver_overlay


def test_face_friction_overlay_is_idempotent(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    decoder = frontend / "_decoder_.py"
    scene = frontend / "_scene_.py"
    decoder.write_text(
        "prefix\n" + solver_overlay._DECODER_NEEDLE + "suffix\n",
        encoding="utf-8")
    scene.write_text(
        "prefix\n" + solver_overlay._SCENE_SIGNATURE
        + "body\n" + solver_overlay._SCENE_EXTEND
        + solver_overlay._SCENE_SHELL + solver_overlay._VIOLATION_NEEDLE
        + "suffix\n", encoding="utf-8")

    solver_overlay.apply_managed_solver_overlay(tmp_path)
    first_decoder = decoder.read_text(encoding="utf-8")
    first_scene = scene.read_text(encoding="utf-8")
    solver_overlay.apply_managed_solver_overlay(tmp_path)

    assert "face_friction" in first_decoder
    assert "per_element" in first_scene
    assert "combined_tri" in first_scene
    assert "combined_pair" in first_scene
    assert "exact if exact else original_intersections" in first_scene
    assert decoder.read_text(encoding="utf-8") == first_decoder
    assert scene.read_text(encoding="utf-8") == first_scene


def test_existing_v2_intersection_overlay_is_upgraded(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    decoder = frontend / "_decoder_.py"
    scene = frontend / "_scene_.py"
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

    solver_overlay.apply_managed_solver_overlay(tmp_path)

    upgraded = scene.read_text(encoding="utf-8")
    assert solver_overlay._VIOLATION_REPLACEMENT in upgraded
    assert solver_overlay._VIOLATION_REPLACEMENT_V2 not in upgraded
    assert (tmp_path / (
        f".cloth-next-{solver_overlay.OVERLAY_VERSION}")).is_file()
