# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace


def test_bake_reads_backend_choice_from_addon_preferences(blender_env,
                                                          monkeypatch):
    from cloth_next.blender import solver_test

    context = object()
    monkeypatch.setattr(
        solver_test, "addon_preferences",
        lambda _context, _package: SimpleNamespace(
            solver_backend_choice="ROCM"))
    assert solver_test._backend_choice(context) == "ROCM"


def test_bake_defaults_to_auto_without_registered_preferences(blender_env,
                                                               monkeypatch):
    from cloth_next.blender import solver_test

    def unavailable(_context, _package):
        raise KeyError("source-tree registration")

    monkeypatch.setattr(solver_test, "addon_preferences", unavailable)
    assert solver_test._backend_choice(object()) == "AUTO"
