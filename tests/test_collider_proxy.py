# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest


def _marked_proxy(env, source, name="Body_CNX_Proxy"):
    proxy = env.bpy.types.Object(name=name, type="MESH")
    proxy.get = lambda key, default=None: (
        True if key == env.collider_proxy.PROXY_MARKER else default)
    proxy.cloth_next.collider_proxy_source = source
    proxy.cloth_next.enabled = True
    proxy.cloth_next.role = "COLLIDER"
    return proxy


def test_proxy_peak_estimate_scales_with_vertices_and_samples(blender_env):
    env = blender_env
    env.registration.register()
    source = env.bpy.types.Object(name="Body", type="MESH")
    source.data = SimpleNamespace(vertices=range(112_904))
    settings = source.cloth_next
    settings.bake_start = 1
    settings.bake_end = 138
    settings.collider_samples_per_frame = 8
    proxy = _marked_proxy(env, source)
    proxy.data = SimpleNamespace(vertices=range(12_000))

    estimate = env.collider_proxy.proxy_estimate(source, proxy)

    assert estimate.sample_count == 1097
    assert estimate.source_peak_bytes > 30 * 1024 ** 3
    assert estimate.proxy_peak_bytes < 4 * 1024 ** 3
    env.registration.unregister()


def test_enabled_source_is_replaced_by_owned_proxy(blender_env):
    env = blender_env
    env.registration.register()
    cloth = env.bpy.types.Object(name="Cloth", type="MESH")
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    source = env.bpy.types.Object(name="Body", type="MESH")
    source.cloth_next.enabled = True
    source.cloth_next.role = "COLLIDER"
    source.cloth_next.collider_motion = "ANIMATED"
    proxy = _marked_proxy(env, source)
    source.cloth_next.collider_proxy_object = proxy
    source.cloth_next.collider_proxy_enabled = True
    context = SimpleNamespace(scene=SimpleNamespace(
        objects=[cloth, source, proxy]))

    # Discovery is used from Panel.draw and must never mutate Blender IDs.
    deformables, colliders = env.solver_test._enabled_objects_for_solve(context)

    assert deformables == (cloth,)
    assert colliders == (proxy,)
    assert proxy.cloth_next.collider_motion != "ANIMATED"

    # Explicit validation/Bake preparation owns the synchronization write.
    env.solver_test._sync_enabled_proxy_settings(context)
    assert proxy.cloth_next.collider_motion == "ANIMATED"
    env.registration.unregister()


def test_enabled_proxy_without_generated_object_is_actionable(blender_env):
    env = blender_env
    env.registration.register()
    source = env.bpy.types.Object(name="Body", type="MESH")
    source.cloth_next.collider_proxy_enabled = True

    with pytest.raises(env.collider_proxy.ColliderProxyError,
                       match="Generate or disable"):
        env.collider_proxy.resolve_proxy(source)
    env.registration.unregister()
