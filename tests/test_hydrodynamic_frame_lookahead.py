import importlib
from types import SimpleNamespace

import pytest


def cloth(cache_root):
    cache = SimpleNamespace(get_cache_abspath=lambda: str(cache_root))
    domain = SimpleNamespace(flip_fluid=SimpleNamespace(domain=SimpleNamespace(cache=cache)))
    return SimpleNamespace(cloth_next=SimpleNamespace(water_flow_domain=domain))


def flip(tmp_path, last):
    bakefiles = tmp_path / 'bakefiles'
    bakefiles.mkdir()
    for frame in range(1, last + 1):
        (bakefiles / f'{frame:06d}.bobj').write_bytes(b'')
    return [cloth(tmp_path)]


def test_genuine_frame_past_the_bake_end_is_used_when_baked(blender_env, tmp_path):
    preparation = importlib.import_module('cloth_next.blender.water_preparation')
    assert preparation.pressure_source_end(flip(tmp_path, 250), 1, 249) == 250


def test_bake_ending_with_flip_holds_the_last_frame(blender_env, tmp_path):
    preparation = importlib.import_module('cloth_next.blender.water_preparation')
    assert preparation.pressure_source_end(flip(tmp_path, 250), 1, 250) == 250


def test_bake_frames_without_flip_surfaces_are_refused(blender_env, tmp_path):
    preparation = importlib.import_module('cloth_next.blender.water_preparation')
    with pytest.raises(ValueError, match='000251.bobj is not baked'):
        preparation.pressure_source_end(flip(tmp_path, 250), 1, 260)
