# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest

from cloth_next.blender.addon_identity import (
    addon_id_candidates, addon_preferences, package_addon_id)


def context(addons):
    return SimpleNamespace(preferences=SimpleNamespace(addons=addons))


def test_installed_extension_namespace_falls_back_to_manifest_id():
    prefs = SimpleNamespace(update_channel="DEV")
    value = addon_preferences(
        context({"cloth_next": SimpleNamespace(preferences=prefs)}),
        "bl_ext.user_default.cloth_next.blender")
    assert value is prefs
    assert package_addon_id("bl_ext.user_default.cloth_next.blender") == \
        "bl_ext.user_default.cloth_next"
    assert addon_id_candidates("bl_ext.user_default.cloth_next.blender") == (
        "bl_ext.user_default.cloth_next", "cloth_next")


def test_full_extension_namespace_remains_preferred():
    full = SimpleNamespace(name="full")
    fallback = SimpleNamespace(name="fallback")
    value = addon_preferences(context({
        "bl_ext.user_default.cloth_next": SimpleNamespace(preferences=full),
        "cloth_next": SimpleNamespace(preferences=fallback),
    }), "bl_ext.user_default.cloth_next.blender")
    assert value is full


def test_missing_preferences_is_explicit():
    with pytest.raises(KeyError, match="preferences are unavailable"):
        addon_preferences(context({}), "cloth_next.blender")
