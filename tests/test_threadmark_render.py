# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic-render ThreadMark session, deduplication, and lifecycle tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeWorker:
    def __init__(self, *, succeed=True):
        self.succeed = succeed
        self.paths = []
        self.shutdown_calls = 0

    def encode(self, path):
        self.paths.append(Path(path))
        return self.succeed, "" if self.succeed else "test-failure"

    def shutdown(self):
        self.shutdown_calls += 1


def _scene(tmp_path, suffix=".png"):
    exact = tmp_path / f"still{suffix}"
    frames = tmp_path / f"frame-####{suffix}"
    render = SimpleNamespace(
        filepath=str(exact),
        file_extension=suffix,
        use_file_extension=True,
        frame_path=lambda frame: str(tmp_path / f"frame-{frame:04}{suffix}"),
    )
    return SimpleNamespace(
        render=render, frame_current=1, objects=[], exact=exact, frames=frames
    )


@pytest.fixture
def runtime(blender_env, monkeypatch):
    module = blender_env.threadmark_render
    module.unregister()
    module._clear_session()
    workers = []

    def factory():
        worker = FakeWorker()
        workers.append(worker)
        return worker

    monkeypatch.setattr(module, "_worker_factory", factory)
    yield module, workers
    module.unregister()


def test_ineligible_scene_never_starts_worker(runtime, monkeypatch, tmp_path):
    module, workers = runtime
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: False)
    scene = _scene(tmp_path)
    module._on_render_pre(scene)
    scene.exact.write_bytes(b"original")
    module._on_render_write(scene)
    assert not workers
    assert scene.exact.read_bytes() == b"original"


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"])
def test_supported_still_output_is_processed(runtime, monkeypatch, tmp_path, suffix):
    module, workers = runtime
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: True)
    scene = _scene(tmp_path, suffix)
    module._on_render_pre(scene)
    scene.exact.write_bytes(b"complete Blender output")
    module._on_render_write(scene)
    assert len(workers) == 1
    assert workers[0].paths == [scene.exact.resolve()]


def test_exr_is_fail_open_and_does_not_start_worker(runtime, monkeypatch, tmp_path):
    module, workers = runtime
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: True)
    scene = _scene(tmp_path, ".exr")
    module._on_render_pre(scene)
    scene.exact.write_bytes(b"linear HDR original")
    module._on_render_write(scene)
    assert not workers
    assert scene.exact.read_bytes() == b"linear HDR original"


def test_animation_reuses_worker_and_processes_each_frame_once(
    runtime, monkeypatch, tmp_path
):
    module, workers = runtime
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: True)
    scene = _scene(tmp_path)
    for frame in (1, 2):
        scene.frame_current = frame
        module._on_render_pre(scene)
        path = tmp_path / f"frame-{frame:04}.png"
        path.write_bytes(f"frame {frame}".encode())
        module._on_render_write(scene)
        module._on_render_write(scene)
    assert len(workers) == 1
    assert workers[0].paths == [
        (tmp_path / "frame-0001.png").resolve(),
        (tmp_path / "frame-0002.png").resolve(),
    ]


def test_worker_failure_preserves_original(runtime, monkeypatch, tmp_path):
    module, workers = runtime
    worker = FakeWorker(succeed=False)
    monkeypatch.setattr(module, "_worker_factory", lambda: worker)
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: True)
    scene = _scene(tmp_path)
    module._on_render_pre(scene)
    scene.exact.write_bytes(b"original")
    module._on_render_write(scene)
    assert scene.exact.read_bytes() == b"original"
    assert worker.paths == [scene.exact.resolve()]


@pytest.mark.parametrize("terminal", ["_on_render_complete", "_on_render_cancel", "_on_load_pre"])
def test_terminal_events_shutdown_and_clear_session(
    runtime, monkeypatch, tmp_path, terminal
):
    module, workers = runtime
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: True)
    scene = _scene(tmp_path)
    module._on_render_pre(scene)
    scene.exact.write_bytes(b"output")
    module._on_render_write(scene)
    getattr(module, terminal)(scene)
    assert workers[0].shutdown_calls == 1
    assert not module._session.active
    assert not module._session.processed
    assert module._session.worker is None


def test_repeated_render_sessions_get_fresh_owned_workers(
    runtime, monkeypatch, tmp_path
):
    module, workers = runtime
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: True)
    scene = _scene(tmp_path)
    for index in range(2):
        module._on_render_pre(scene)
        scene.exact.write_bytes(f"output {index}".encode())
        module._on_render_write(scene)
        module._on_render_complete(scene)
    assert len(workers) == 2
    assert [worker.shutdown_calls for worker in workers] == [1, 1]


def test_ambiguous_output_identity_fails_open(runtime, monkeypatch, tmp_path):
    module, workers = runtime
    monkeypatch.setattr(module, "should_threadmark_render", lambda _scene: True)
    scene = _scene(tmp_path)
    module._on_render_pre(scene)
    scene.exact.write_bytes(b"still")
    (tmp_path / "frame-0001.png").write_bytes(b"frame")
    module._on_render_write(scene)
    assert not workers


def test_reload_safe_registration_and_stale_cleanup(runtime):
    module, _workers = runtime

    def stale(*_args):
        raise AssertionError("stale handler must not run")

    stale._clothnext_threadmark_handler = True
    module.bpy.app.handlers.render_write.append(stale)
    module.register()
    module.register()
    assert module.handler_count() == 5
    assert stale not in module.bpy.app.handlers.render_write
    module.unregister()
    assert module.handler_count() == 0
