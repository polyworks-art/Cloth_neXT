"""Reproductions from the September 2026 diagnostic audit."""
import importlib
from types import SimpleNamespace

import pytest

from cloth_next.onboarding import _safe_asset
from cloth_next.core.safe_delete import cleanup_tombstones
import os
from contextlib import contextmanager


def test_windows_asset_cannot_escape_resource_root(tmp_path):
    root = tmp_path / 'resources'
    root.mkdir()
    (tmp_path / 'outside.png').write_bytes(b'outside')
    with pytest.raises(ValueError, match='package-relative'):
        _safe_asset(root, '..\\outside.png')


def test_onboarding_reload_unregister_cleans_old_timer(blender_env):
    manager = importlib.import_module('cloth_next.blender.onboarding_manager')
    manager.register()
    old = manager._startup_pulse
    importlib.reload(manager)
    manager.register()
    manager.unregister()
    assert old not in blender_env.bpy.app.timers.functions


def test_failed_gui_start_does_not_consume_welcome(blender_env, monkeypatch):
    manager = importlib.import_module('cloth_next.blender.onboarding_manager')
    preferences = SimpleNamespace(onboarding_state='')
    child = SimpleNamespace(returncode=None)
    child.poll = lambda: child.returncode
    monkeypatch.setattr(manager, '_preferences', lambda: preferences)
    monkeypatch.setattr(manager, 'companion_info_command', lambda *a: ['companion'])
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda *a, **k: child)
    manager.launch_screen('welcome')
    child.returncode = 2
    assert manager._poll_startup() is None
    assert manager._state(preferences).next_screen('2.3.7') == 'welcome'


def test_cleanup_does_not_enumerate_all_entries_before_limit(tmp_path, monkeypatch):
    visited = []

    for i in range(150):
        (tmp_path / ('ordinary-%04d.tmp' % i)).touch()
    original = os.scandir

    @contextmanager
    def candidates(path):
        with original(path) as entries:
            def counted():
                for entry in entries:
                    visited.append(entry.name)
                    yield entry
            yield counted()

    monkeypatch.setattr(os, 'scandir', candidates)
    cleanup_tombstones(tmp_path, ownership_authenticated=True,
                       lifecycle_stage='AUDIT', max_entries=1)
    assert len(visited) == 128


@pytest.mark.parametrize('previous_name', [
    'cn_test_cloth_previous.pc2',
    '.cn_test_cloth_previous.pc2.cancelled.tmp',
    'recovery/partials/cloth-uuid.pc2.partial',
])
def test_new_live_frame_does_not_evaluate_previous_cache(
        blender_env, tmp_path, previous_name):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name='cloth', type='MESH')
    blender_env.bpy.data.objects[obj.name] = obj
    previous = tmp_path / previous_name
    previous.parent.mkdir(parents=True, exist_ok=True)
    previous.write_bytes(b'previous run must remain recoverable')
    live = tmp_path / '.cn_test_cloth_new.pc2.live.tmp'
    live.write_bytes(b'new run')
    final = tmp_path / 'cn_test_cloth_new.pc2'
    modifier = obj.modifiers.new(module.import_result.MODIFIER_NAME, 'MESH_CACHE')
    modifier.filepath = str(previous)
    modifier.show_viewport = True
    module.mark_owned_playback(obj, modifier, str(previous))
    identity = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
    target = module.DeformablePlan(((0, 0, 0),), identity, obj.name,
                                  'cloth-uuid', final, 'topology', {}, 'CLOTH')
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), target.initial_local,
                          identity, obj.name, tmp_path, final, 3,
                          frame_start=10, frame_end=12, deformables=(target,))
    evaluated = []
    scene = SimpleNamespace(frame_current=10)

    def frame_set(frame):
        scene.frame_current = frame
        evaluated.extend(mod.filepath for mod in obj.modifiers
                         if mod.type == 'MESH_CACHE' and mod.show_viewport)

    scene.frame_set = frame_set
    blender_env.bpy.context.scene = scene
    module._advance_bake_timeline(plan, 11, {target.uuid: str(live)})
    assert previous.read_bytes() == b'previous run must remain recoverable'
    assert evaluated == [str(live)]
    module._restore_live_playback()
    assert modifier.filepath == str(previous)
    assert modifier.show_viewport
    assert not module._live_playback_records
