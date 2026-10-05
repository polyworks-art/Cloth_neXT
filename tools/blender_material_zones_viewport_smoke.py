# SPDX-License-Identifier: GPL-3.0-or-later
"""Live-window GPU/modal smoke, intended for a separate factory-startup Blender.

The timer is test orchestration only; the production selector has no timer.
"""
from pathlib import Path
import sys
import json
import traceback
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
from mathutils import Quaternion, Vector
import cloth_next
from cloth_next.blender import material_zones as data, material_zone_selector as selector

OUT = Path(__file__).resolve().parents[1] / '.tmp_verify/material-zones-viewport.json'
cloth_next.register()
for obj in list(bpy.context.scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
mesh = bpy.data.meshes.new('Occlusion')
mesh.from_pydata(((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0),
                  (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1)), (),
                 ((3, 2, 1, 0), (4, 5, 6, 7)))
obj = bpy.data.objects.new('Occlusion', mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
obj.cloth_next.enabled = True
bpy.ops.clothnext.add_material_zone()
identity = obj.cloth_next.material_zones[0].identity
token = obj.cloth_next.material_zones[0].token
step = 0
active = None
reports = []


def tick():
    global step, active
    try:
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=area, region=region):
            projection = 'ORTHO' if step < 4 else 'PERSP'
            phase = step % 4
            if phase == 0:
                rv3d = area.spaces.active.region_3d
                rv3d.view_rotation = Quaternion((1, 0, 0, 0))
                rv3d.view_location, rv3d.view_distance = Vector((0, 0, 0)), 10
                rv3d.view_perspective = projection
                area.spaces.active.shading.show_xray = True
                area.tag_redraw()
                data.write_owners(obj, (0, 0))
            elif phase == 1:
                dispatch = 'INVOKE_DEFAULT' if projection == 'ORTHO' else 'EXEC_DEFAULT'
                assert bpy.ops.clothnext.edit_material_zone_selection(dispatch, identity=identity) == {'RUNNING_MODAL'}
                active = next(iter(selector._sessions))
                area.tag_redraw()
            elif phase == 2:
                active._project()
                point = active._points[0]
                def event(kind, shift=False):
                    return SimpleNamespace(type=kind, value='PRESS', shift=shift,
                                           mouse_x=point.x + region.x, mouse_y=point.y + region.y)
                assert active.modal(bpy.context, event('LEFTMOUSE')) == {'RUNNING_MODAL'}
                assert active._owners == (token, 0), active._owners
                active.modal(bpy.context, event('WHEELUPMOUSE', True))
                assert active._owners == (token, 0)
                active.modal(bpy.context, event('MOUSEMOVE', True))
                assert active._owners == (0, 0)
                active.modal(bpy.context, event('MOUSEMOVE'))
                assert active._owners == (token, 0)
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                assert active._gpu_batches is not None and active._gpu_faces
                finish = 'ESC' if projection == 'ORTHO' else 'RET'
                active.modal(bpy.context, event(finish))
                assert not active._handles and not selector._sessions
                assert data.read_owners(mesh) == ((0, 0) if finish == 'ESC' else (token, 0))
                reports.append({'projection': projection, 'xray': True, 'front_only': True,
                                'gpu_batches_drawn': True, 'finish': finish, 'cleanup': True})
            elif step == 7:
                cloth_next.unregister()
                OUT.write_text(json.dumps({'passed': True, 'sessions': reports}, indent=2))
                bpy.ops.wm.quit_blender()
                return None
        step += 1
        return .5
    except Exception:
        selector.cleanup_all()
        OUT.write_text(json.dumps({'passed': False, 'step': step, 'error': traceback.format_exc()}, indent=2))
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
