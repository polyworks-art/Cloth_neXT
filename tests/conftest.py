# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared fixtures: an isolated fake-bpy Blender adapter environment."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from tests import fake_bpy


def _pop_blender_modules():
    removed = {}
    for name in list(sys.modules):
        if name == "bpy" or name.startswith("cloth_next.blender"):
            removed[name] = sys.modules.pop(name)
    return removed


@pytest.fixture
def blender_env():
    """Fresh fake ``bpy`` plus freshly imported cloth_next.blender modules."""
    saved = _pop_blender_modules()
    fake = fake_bpy.make_module()
    sys.modules["bpy"] = fake
    registration = importlib.import_module("cloth_next.blender.registration")
    env = SimpleNamespace(
        bpy=fake,
        registration=registration,
        object_properties=sys.modules["cloth_next.blender.object_properties"],
        physics_operators=sys.modules["cloth_next.blender.physics_operators"],
        physics_ui=sys.modules["cloth_next.blender.physics_ui"],
        solver_test=sys.modules["cloth_next.blender.solver_test"],
        addon_update_operators=sys.modules[
            "cloth_next.blender.addon_update_operators"],
    )
    try:
        yield env
    finally:
        _pop_blender_modules()
        sys.modules.update(saved)
