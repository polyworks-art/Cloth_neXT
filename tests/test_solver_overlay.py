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
        + solver_overlay._SCENE_SHELL + "suffix\n", encoding="utf-8")

    solver_overlay.apply_managed_solver_overlay(tmp_path)
    first_decoder = decoder.read_text(encoding="utf-8")
    first_scene = scene.read_text(encoding="utf-8")
    solver_overlay.apply_managed_solver_overlay(tmp_path)

    assert "face_friction" in first_decoder
    assert "per_element" in first_scene
    assert decoder.read_text(encoding="utf-8") == first_decoder
    assert scene.read_text(encoding="utf-8") == first_scene
