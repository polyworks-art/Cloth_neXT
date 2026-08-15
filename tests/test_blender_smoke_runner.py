from __future__ import annotations

from tools import run_blender_smoke


def test_supported_blender_version_follows_manifest_minimum():
    assert run_blender_smoke.minimum_blender_version() == (5, 0, 0)
    assert not run_blender_smoke.supported_blender_version((4, 4, 9))
    assert run_blender_smoke.supported_blender_version((5, 0, 0))
    assert run_blender_smoke.supported_blender_version((5, 2, 0))
