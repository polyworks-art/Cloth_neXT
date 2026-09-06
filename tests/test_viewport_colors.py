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
    viewport_colors.apply_object(obj, use_role_colors=True)
    assert obj.color == viewport_colors.ROLE_COLORS["CLOTH"]
    obj.cloth_next.enabled = False
    viewport_colors.apply_object(obj)
    assert obj.color == (0.2, 0.3, 0.4, 0.5)
    assert viewport_colors._ORIGINAL_COLOR not in obj


def test_role_colors_default_off(monkeypatch):
    viewport_colors = _module(monkeypatch)
    class Object(dict):
        color = (0.2, 0.3, 0.4, 0.5)
    obj = Object()
    obj.cloth_next = SimpleNamespace(enabled=True, role="CLOTH")
    viewport_colors.apply_object(obj)
    assert obj.color == (0.2, 0.3, 0.4, 0.5)
    assert viewport_colors._ORIGINAL_COLOR not in obj


def test_preference_toggle_restores_original_color(monkeypatch):
    viewport_colors = _module(monkeypatch)
    class Object(dict):
        color = (0.2, 0.3, 0.4, 0.5)
    obj = Object()
    obj.cloth_next = SimpleNamespace(enabled=True, role="CLOTH")
    viewport_colors.bpy.data.objects = [obj]
    for _ in range(2):
        viewport_colors.update_role_colors(SimpleNamespace(show_role_colors=True), None)
        assert obj.color == viewport_colors.ROLE_COLORS["CLOTH"]
        viewport_colors.update_role_colors(SimpleNamespace(show_role_colors=False), None)
        assert obj.color == (0.2, 0.3, 0.4, 0.5)
        assert viewport_colors._ORIGINAL_COLOR not in obj


def test_all_shading_modes_survive_lifecycle_and_preference_changes(monkeypatch):
    viewport_colors = _module(monkeypatch)
    for mode in ("OBJECT", "RANDOM", "MATERIAL", "SINGLE", "TEXTURE", "VERTEX"):
        shading = SimpleNamespace(type="SOLID", color_type=mode)
        monkeypatch.setattr(viewport_colors, "_view3d_spaces", lambda: iter((
            (SimpleNamespace(tag_redraw=lambda: None), SimpleNamespace(shading=shading)),)))
        viewport_colors.register()
        viewport_colors.update_role_colors(SimpleNamespace(show_role_colors=True), None)
        viewport_colors._on_load_post()
        viewport_colors.update_role_colors(SimpleNamespace(show_role_colors=False), None)
        viewport_colors.unregister()
        assert shading.color_type == mode
        assert not viewport_colors.bpy.app.timers.functions


def test_file_load_restores_saved_role_colors_when_disabled(monkeypatch):
    viewport_colors = _module(monkeypatch)
    class Object(dict):
        color = (0.2, 0.3, 0.4, 0.5)
    obj = Object()
    obj.cloth_next = SimpleNamespace(enabled=True, role="CLOTH")
    viewport_colors.apply_object(obj, use_role_colors=True)
    viewport_colors.bpy.data.objects = [obj]
    viewport_colors._on_load_post()
    assert obj.color == (0.2, 0.3, 0.4, 0.5)


def test_register_replaces_stale_handlers_without_duplicates(monkeypatch):
    viewport_colors = _module(monkeypatch)
    def stale_handler(*_args):
        pass
    stale_handler._clothnext_viewport_handler = True
    viewport_colors.bpy.app.handlers.load_pre.append(stale_handler)
    viewport_colors.bpy.app.handlers.load_post.append(stale_handler)
    viewport_colors.register()
    viewport_colors.register()
    assert not viewport_colors.bpy.app.handlers.load_pre
    assert viewport_colors.bpy.app.handlers.load_post == [viewport_colors._on_load_post]
    viewport_colors.unregister()
    assert not viewport_colors.bpy.app.handlers.load_post
