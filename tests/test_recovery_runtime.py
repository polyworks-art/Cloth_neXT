# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.bake import cache_metadata, pc2
from cloth_next.export_cache import deterministic_key
from cloth_next.ppf_run.session import (
    RecoveryOutcome, RecoveryOutcomeKind, SessionCancelled,
)


def _runtime(blender_env):
    return sys.modules["cloth_next.blender.recovery_runtime"]


class _Scene:
    def __init__(self, frame=42, subframe=0.5):
        self.frame_current = frame
        self.frame_subframe = subframe
        self.calls = []

    def frame_set(self, frame, subframe=0.0):
        self.calls.append((int(frame), float(subframe)))
        self.frame_current = int(frame)
        self.frame_subframe = float(subframe)


def test_scene_cache_identity_is_evaluated_at_bake_start_and_restored(
        blender_env):
    runtime = _runtime(blender_env)
    scene = _Scene()
    context = SimpleNamespace(scene=scene)
    snapshot = SimpleNamespace(
        bake_range=SimpleNamespace(start=10))
    observed = []
    original = runtime._original_scene_source_key
    runtime._original_scene_source_key = lambda _context, _snapshot: (
        observed.append((_context.scene.frame_current,
                         _context.scene.frame_subframe)) or
        ("raw-key", "safe source identity"))
    try:
        key, reason = runtime._canonical_scene_source_key(context, snapshot)
    finally:
        runtime._original_scene_source_key = original

    assert observed == [(10, 0.0)]
    assert scene.calls == [(10, 0.0), (42, 0.5)]
    assert (scene.frame_current, scene.frame_subframe) == (42, 0.5)
    assert key == deterministic_key("scene", {
        "canonical_frame_schema": 1,
        "bake_start": 10,
        "source_key": "raw-key",
    })
    assert "canonical Bake Start" in reason


def test_cancelled_session_with_downloaded_frames_preserves_partial_result(
        blender_env):
    runtime = _runtime(blender_env)
    outcome = RecoveryOutcome(
        kind=RecoveryOutcomeKind.FAILED,
        checkpoint_saved=False,
        artist_message="checkpoint failed",
        technical_reason="test",
        state_before="BUSY",
        saved_states=())

    def cancelled(_self):
        raise SessionCancelled(
            resumable=False, recovery_outcome=outcome)

    original = runtime._original_session_run
    runtime._original_session_run = cancelled
    dummy = SimpleNamespace(
        diagnostics=SimpleNamespace(fetched_frames=[1, 2]))
    try:
        with pytest.raises(SessionCancelled) as raised:
            runtime._session_run_with_partial_preservation(dummy)
    finally:
        runtime._original_session_run = original

    assert raised.value.resumable
    assert raised.value.recovery_outcome is outcome


def test_cancelled_session_without_downloaded_frames_remains_unresumable(
        blender_env):
    runtime = _runtime(blender_env)

    def cancelled(_self):
        raise SessionCancelled(resumable=False)

    original = runtime._original_session_run
    runtime._original_session_run = cancelled
    dummy = SimpleNamespace(
        diagnostics=SimpleNamespace(fetched_frames=[]))
    try:
        with pytest.raises(SessionCancelled) as raised:
            runtime._session_run_with_partial_preservation(dummy)
    finally:
        runtime._original_session_run = original

    assert not raised.value.resumable


def test_preserve_hook_publishes_playable_prefix_and_keeps_resume_file(
        blender_env, tmp_path):
    runtime = _runtime(blender_env)
    final = tmp_path / "preview.pc2"
    partial = tmp_path / "preview.pc2.partial"
    writer = pc2.StreamingPc2Writer(
        final, vertex_count=1, frame_count=4, resume_path=partial)
    writer.write_frame([[0, 0, 0]])
    writer.write_frame([[1, 0, 0]])

    original = runtime._original_writer_preserve
    runtime._original_writer_preserve = pc2.StreamingPc2Writer.preserve
    try:
        assert runtime._preserve_with_preview(writer) == partial
    finally:
        runtime._original_writer_preserve = original

    assert pc2.read_header(final).frame_count == 2
    assert pc2.partial_frame_count(
        partial, pc2.Pc2Header(1, 0.0, 1.0, 4)) == 2


def _material_meta(frame_count: int) -> dict:
    return {
        "fingerprints": {
            "settings": "settings", "geometry": "geometry",
            "combined": "combined", "topology": "topology",
            "object": "object", "scene": "scene",
        },
        "identities": {
            "cloth_next_version": "test", "blender_version": "test",
            "object": {}, "solver": {},
        },
        "expected": {
            "vertex_count": 1, "frame_count": frame_count,
            "start_frame": 0.0, "sample_rate": 1.0,
        },
        "details": {"blender_start_frame": 1,
                    "blender_end_frame": frame_count},
    }


def _single_plan(path: Path, frame_count=5):
    return SimpleNamespace(
        initial_local=((0.0, 0.0, 0.0),),
        world_matrix=((1, 0, 0, 0), (0, 1, 0, 0),
                      (0, 0, 1, 0), (0, 0, 0, 1)),
        cloth_object_name="Cloth",
        scene=SimpleNamespace(cloth_uuid="cloth-uuid"),
        pc2_path=path,
        topology_signature="topology",
        material_meta=_material_meta(frame_count),
        deformable_role="CLOTH",
        deformables=(),
        stitch_pairs=(),
        stitch_snap_distance=0.0,
        frame_count=frame_count,
        frame_start=1,
        frame_end=frame_count,
    )


def test_cancelled_preview_is_authenticated_instead_of_deleted(
        blender_env, tmp_path):
    runtime = _runtime(blender_env)
    path = tmp_path / "cancelled.pc2"
    pc2.write_pc2(path, [[(0, 0, 0)], [(1, 0, 0)]])
    plan = _single_plan(path)
    discarded = []
    original = runtime._original_discard_incomplete
    runtime._original_discard_incomplete = (
        lambda *_args, **_kwargs: discarded.append(True))
    try:
        runtime._retain_cancelled_preview(
            plan, state="cancelled", reason="user cancelled")
    finally:
        runtime._original_discard_incomplete = original

    assert not discarded
    inspection = cache_metadata.inspect_cache(
        path, settings_fingerprint="settings",
        geometry_fingerprint="geometry")
    assert inspection.usable
    assert inspection.metadata["expected"]["frame_count"] == 2
    assert inspection.metadata["details"]["partial_result"] == {
        "cached_frame_count": 2,
        "requested_frame_count": 5,
        "cancelled": True,
    }
