import importlib
from types import SimpleNamespace

import pytest


def cloth(cache_root):
    cache = SimpleNamespace(get_cache_abspath=lambda: str(cache_root))
    domain = SimpleNamespace(flip_fluid=SimpleNamespace(domain=SimpleNamespace(cache=cache)))
    return SimpleNamespace(cloth_next=SimpleNamespace(water_flow_domain=domain))


def test_pressure_needs_one_flip_surface_frame_past_the_bake_end(blender_env, tmp_path):
    preparation = importlib.import_module('cloth_next.blender.water_preparation')
    bakefiles = tmp_path / 'bakefiles'
    bakefiles.mkdir()
    for frame in range(1, 251):
        (bakefiles / f'{frame:06d}.bobj').write_bytes(b'')
    preparation._require_pressure_frames([cloth(tmp_path)], 1, 250)
    with pytest.raises(ValueError, match='missing 251.*through frame 251.*at frame 249'):
        preparation._require_pressure_frames([cloth(tmp_path)], 1, 251)
