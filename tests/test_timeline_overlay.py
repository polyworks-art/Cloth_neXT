import importlib
import sys

from tests import fake_bpy


def _module(monkeypatch):
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy.make_module())
    sys.modules.pop("cloth_next.blender.timeline_overlay", None)
    return importlib.import_module("cloth_next.blender.timeline_overlay")


def test_baked_strip_clamps_latest_frame(monkeypatch):
    overlay = _module(monkeypatch)
    monkeypatch.setattr(overlay, "_redraw", lambda: None)

    overlay.set_baked_range(10, 24, 100)
    assert overlay.baked_range() == (10, 24, 100)

    overlay.set_baked_range(10, 999, 100)
    assert overlay.baked_range() == (10, 100, 100)


def test_baked_strip_uses_cloth_next_turquoise(monkeypatch):
    overlay = _module(monkeypatch)
    red, green, blue, alpha = overlay.BAKED_COLOR
    assert green > 0.65
    assert blue > 0.55
    assert red < 0.1
    assert alpha > 0.9
    assert overlay.STRIP_HEIGHT == 17.0
    assert overlay.TRACK_LABEL == "Cloth NeXt Bake"
    assert overlay.TRACK_HEIGHT == 19.0
    assert overlay.TRACK_TEXT_COLOR == (1.0, 1.0, 1.0, 1.0)
