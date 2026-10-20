import importlib
from types import SimpleNamespace

import numpy as np


def scene_with_frames(log):
    scene = SimpleNamespace(name='NEON', frame_current=5, objects=())
    scene.frame_set = lambda frame, **_kw: (log.append(frame), setattr(scene, 'frame_current', frame))
    return scene


def test_recorded_capture_frames_need_no_extra_frame_changes(blender_env, monkeypatch):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    log = []
    scene = scene_with_frames(log)
    monkeypatch.setattr(water_flow, '_pressure_targets', lambda _scene: ['cloth'])
    monkeypatch.setattr(water_flow, '_world_box', lambda s, _t: (
        np.array([s.frame_current, 0., 0.]), np.array([s.frame_current + 1., 1., 1.])))
    for frame in range(1, 11):
        scene.frame_current = frame
        water_flow.record_motion_bounds(scene, frame)
    bounds = water_flow.hydrodynamic_bounds(SimpleNamespace(scene=scene), ['cloth'], 1, 10)
    assert log == []
    assert np.allclose(bounds, [1 - .25, -.25, -.25, 11 + .25, 1.25, 1.25])
    assert not water_flow._motion_bounds  # consumed once per Bake


def test_missing_frames_are_sampled_sparsely_with_progress(blender_env, monkeypatch):
    water_flow = importlib.import_module('cloth_next.blender.water_flow')
    log, progress = [], []
    scene = scene_with_frames(log)
    monkeypatch.setattr(water_flow, '_world_box', lambda s, _t: (
        np.array([s.frame_current, 0., 0.]), np.array([s.frame_current + 1., 1., 1.])))
    bounds = water_flow.hydrodynamic_bounds(SimpleNamespace(scene=scene), ['cloth'], 1, 250,
                                            progress=lambda c, t: progress.append((c, t)))
    sampled = log[:-1]
    assert len(sampled) == water_flow.MAX_BOUNDS_SAMPLES and sampled[0] == 1 and sampled[-1] == 250
    assert log[-1] == 5 and len(progress) == len(sampled)
    margin = water_flow.SAMPLED_BOUNDS_MARGIN_M
    assert np.allclose(bounds[:1] + bounds[3:4], [1 - margin, 251 + margin])
