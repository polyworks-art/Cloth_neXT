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


def test_refresh_forces_solid_viewport_to_object_color(monkeypatch):
    viewport_colors = _module(monkeypatch)
    redraws = []
    shading = SimpleNamespace(type="SOLID", color_type="MATERIAL")
    space = SimpleNamespace(shading=shading)
    area = SimpleNamespace(tag_redraw=lambda: redraws.append(1))
    monkeypatch.setattr(viewport_colors, "_view3d_spaces",
                        lambda: iter(((area, space),)))

    viewport_colors.refresh_viewports()

    assert shading.color_type == "OBJECT"
    assert redraws == [1]
    assert viewport_colors._shading_states == [(shading, "MATERIAL")]


def test_refresh_does_not_redraw_unchanged_object_color_viewport(monkeypatch):
    viewport_colors = _module(monkeypatch)
    redraws = []
    shading = SimpleNamespace(type="SOLID", color_type="OBJECT")
    space = SimpleNamespace(shading=shading)
    area = SimpleNamespace(tag_redraw=lambda: redraws.append(1))
    monkeypatch.setattr(viewport_colors, "_view3d_spaces",
                        lambda: iter(((area, space),)))

    assert viewport_colors.refresh_viewports() is False
    assert redraws == []


def test_file_load_discards_shading_rna_before_database_replacement(monkeypatch):
    viewport_colors = _module(monkeypatch)
    shading = SimpleNamespace(type="SOLID", color_type="MATERIAL")
    space = SimpleNamespace(shading=shading)
    area = SimpleNamespace(tag_redraw=lambda: None)
    monkeypatch.setattr(viewport_colors, "_view3d_spaces",
                        lambda: iter(((area, space),)))

    viewport_colors.register()
    assert viewport_colors._shading_states == [(shading, "MATERIAL")]
    assert viewport_colors._on_load_pre_clear_shading_states in (
        viewport_colors.bpy.app.handlers.load_pre)

    viewport_colors._on_load_pre_clear_shading_states(None)

    assert viewport_colors._shading_states == []
    viewport_colors.unregister()
    assert viewport_colors._on_load_pre_clear_shading_states not in (
        viewport_colors.bpy.app.handlers.load_pre)


def test_register_purges_stale_viewport_load_handler(monkeypatch):
    viewport_colors = _module(monkeypatch)

    def stale_handler(*_args):
        return None

    stale_handler._clothnext_viewport_handler = True
    viewport_colors.bpy.app.handlers.load_pre.append(stale_handler)

    viewport_colors.register()
    viewport_colors.register()

    assert viewport_colors.bpy.app.handlers.load_pre == [
        viewport_colors._on_load_pre_clear_shading_states]
    viewport_colors.unregister()
