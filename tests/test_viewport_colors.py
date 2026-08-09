import importlib
import sys
from types import SimpleNamespace

from tests import fake_bpy


def _module(monkeypatch):
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy.make_module())
    sys.modules.pop("cloth_next.blender.viewport_colors", None)
    return importlib.import_module("cloth_next.blender.viewport_colors")


def test_role_palette_is_distinct_and_opaque(monkeypatch):
    viewport_colors = _module(monkeypatch)
    colors = tuple(viewport_colors.ROLE_COLORS.values())
    assert len(colors) == 6
    assert len(set(colors)) == len(colors)
    assert all(len(color) == 4 and color[3] == 1.0 for color in colors)
    assert viewport_colors.role_color("CLOTH")[2] > 0.7
    assert viewport_colors.role_color("COLLIDER")[0] > 0.7


def test_apply_and_restore_preserve_artist_color(monkeypatch):
    viewport_colors = _module(monkeypatch)
    class Object(dict):
        color = (0.2, 0.3, 0.4, 0.5)

    obj = Object()
    obj.cloth_next = SimpleNamespace(enabled=True, role="CLOTH")
    viewport_colors.apply_object(obj)
    assert obj.color == viewport_colors.ROLE_COLORS["CLOTH"]
    obj.cloth_next.enabled = False
    viewport_colors.apply_object(obj)
    assert obj.color == (0.2, 0.3, 0.4, 0.5)
    assert viewport_colors._ORIGINAL_COLOR not in obj
