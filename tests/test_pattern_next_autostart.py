# SPDX-License-Identifier: GPL-3.0-or-later
"""PATTERN NeXt autostart must never wrap running modal operators.

Dev 2.9.64 crashed Tim's Blender 5.2.3 inside a timer (PyType_GenericNew from
py_timer_execute). The editor's 0.25 s autostart timer iterated
``Window.modal_operators`` in every window, building a Python object for
every running operator. It now only reads the collection's length.
"""

from __future__ import annotations

import importlib
import sys

import pytest


class _ModalOperators:
    """Length is fine; touching an item is what the fix must avoid."""

    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count

    def __iter__(self):
        raise AssertionError("autostart iterated Window.modal_operators")

    def __getitem__(self, index):
        raise AssertionError("autostart wrapped a running modal operator")


class _Window:
    def __init__(self, count, pointer=0x1000):
        self.modal_operators = _ModalOperators(count)
        self.pointer = pointer

    def as_pointer(self):
        return self.pointer


@pytest.fixture
def editor(blender_env):
    module = importlib.import_module("cloth_next.blender.pattern_next.editor")
    module._modal_seen.clear()
    yield module
    module._modal_seen.clear()


def test_no_modal_operator_does_not_block(editor):
    assert editor._blocking_modal(_Window(0), now=10.0) is False


def test_new_modal_operator_blocks_then_ages_into_background(editor):
    window = _Window(1)
    assert editor._blocking_modal(window, now=10.0) is True      # e.g. a transform
    assert editor._blocking_modal(window, now=10.5) is True
    assert editor._blocking_modal(window, now=11.1) is False     # session-long tracker


def test_another_modal_operator_blocks_again(editor):
    window = _Window(1)
    editor._blocking_modal(window, now=10.0)
    assert editor._blocking_modal(window, now=12.0) is False
    window.modal_operators.count = 2                             # transform over the tracker
    assert editor._blocking_modal(window, now=12.1) is True
    assert editor._blocking_modal(window, now=13.2) is False
    window.modal_operators.count = 3
    assert editor._blocking_modal(window, now=14.0) is True
    window.modal_operators.count = 2                             # one ended: no new wait
    assert editor._blocking_modal(window, now=15.1) is False
    window.modal_operators.count = 0
    assert editor._blocking_modal(window, now=15.2) is False
    assert window.as_pointer() not in editor._modal_seen


def test_windows_age_independently(editor):
    a, b = _Window(1, 0x1), _Window(1, 0x2)
    editor._blocking_modal(a, now=10.0)
    assert editor._blocking_modal(b, now=11.5) is True
    assert editor._blocking_modal(a, now=11.5) is False


def test_missing_collection_counts_as_none(editor):
    class Bare:
        def as_pointer(self):
            return 0x3
    assert editor._blocking_modal(Bare(), now=1.0) is False


def test_crash_trace_only_in_dev_builds(tmp_path, monkeypatch, blender_env):
    crash_trace = importlib.import_module("cloth_next.blender.crash_trace")
    monkeypatch.setattr(crash_trace, "path", lambda: tmp_path / "trace.txt")
    calls = []
    monkeypatch.setattr(crash_trace.faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(crash_trace.faulthandler, "enable",
                        lambda file, all_threads: calls.append(("enable", all_threads)))
    monkeypatch.setattr(crash_trace.faulthandler, "disable",
                        lambda: calls.append(("disable",)))
    assert crash_trace.enable(dev_build=False) is False
    assert calls == [] and not (tmp_path / "trace.txt").exists()
    assert crash_trace.enable(dev_build=True) is True
    assert crash_trace.enable(dev_build=True) is False           # only once
    assert calls == [("enable", True)]
    assert "Cloth NeXt Python crash trace" in (tmp_path / "trace.txt").read_text("utf-8")
    crash_trace.disable()
    assert calls[-1] == ("disable",) and crash_trace._stream is None
    assert sys.modules["cloth_next.blender.crash_trace"] is crash_trace


def test_edge_sewn_twice_is_detected():
    """Tim's 2.9.64 scene: edge 3->1 of A sewn to B and again to A's edge 1->2."""
    from cloth_next.pattern_next.seams import SeamSide, overlaps_sewn
    from cloth_next.pattern_next.shapes import rectangle_points
    points = rectangle_points(0.4, 0.5, (0, 3, 1, 2))   # outline order 0, 3, 1, 2
    first = SeamSide("A", 3, 1)                          # already sewn to B
    assert overlaps_sewn(points, SeamSide("A", 3, 1), [first])
    assert overlaps_sewn(points, SeamSide("A", 0, 1), [first])      # longer side covering it
    assert not overlaps_sewn(points, SeamSide("A", 1, 2), [first])  # shares only corner 1
    assert not overlaps_sewn(points, SeamSide("A", 1, 2), [])
    assert not overlaps_sewn(points, SeamSide("A", 1, 2), [SeamSide("A", 9, 8)])  # stale seam


def test_live_seams_close_over_their_closure_time(blender_env):
    """Live used to sew at full strength from the first step: two flat panels 40 cm
    apart stretched 5.7x (Tim's SewingBug.blend); now 1.2x, like a Bake."""
    live = importlib.import_module("cloth_next.blender.aether_engine_live")
    ramp = [(1, 24), (3, 1)]
    assert live.seam_progress(ramp, -1) == [{"id": 1, "progress": 0.0}, {"id": 3, "progress": 0.0}]
    assert live.seam_progress(ramp, 0) == [{"id": 1, "progress": 1 / 24}, {"id": 3, "progress": 1.0}]
    assert live.seam_progress(ramp, 11)[0]["progress"] == 0.5
    assert live.seam_progress(ramp, 500)[0]["progress"] == 1.0
