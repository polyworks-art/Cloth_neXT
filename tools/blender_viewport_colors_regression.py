"""Blender regression for optional role colors and unrestricted shading."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
import addon_utils

addon_utils.enable('cloth_next', default_set=True)
from cloth_next.blender import viewport_colors
preferences = bpy.context.preferences.addons['cloth_next'].preferences
assert preferences.show_role_colors is False
obj = bpy.context.active_object
original = tuple(obj.color)
obj.cloth_next.enabled = True
assert tuple(obj.color) == original
spaces = [space for screen in bpy.data.screens for area in screen.areas
          for space in area.spaces if space.type == 'VIEW_3D']
assert spaces
for mode in ('RANDOM', 'MATERIAL', 'SINGLE', 'OBJECT', 'TEXTURE', 'VERTEX'):
    for space in spaces:
        space.shading.type = 'SOLID'
        space.shading.color_type = mode
    preferences.show_role_colors = True
    expected = viewport_colors.role_color(obj.cloth_next.role)
    assert all(abs(a-b) < 1e-6 for a,b in zip(obj.color, expected))
    viewport_colors.synchronize_objects()
    assert all(space.shading.color_type == mode for space in spaces)
    preferences.show_role_colors = False
    assert tuple(obj.color) == original
    assert all(space.shading.color_type == mode for space in spaces)
with tempfile.TemporaryDirectory(prefix='clothnext-colors-') as directory:
    preferences.show_role_colors = True
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / 'colors.blend'))
    preferences.show_role_colors = False
    bpy.ops.wm.open_mainfile(filepath=str(Path(directory) / 'colors.blend'))
    assert tuple(bpy.context.active_object.color) == original
    assert not viewport_colors.role_colors_enabled()  # preferences survive file load
addon_utils.disable('cloth_next', default_set=True)
assert not any(getattr(fn, '_clothnext_viewport_handler', False)
               for fn in bpy.app.handlers.load_post)
print('VIEWPORT COLORS PASS: default off, all color modes, toggle, file load, cleanup')
