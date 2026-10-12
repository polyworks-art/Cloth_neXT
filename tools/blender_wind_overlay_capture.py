# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture the actual Wind draw callback in an isolated GPU-enabled Blender."""
import json
import math
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
import bpy
import gpu
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.blender import registration, wind_fields
registration.register()
scene = bpy.context.scene
bpy.ops.object.empty_add()
obj = bpy.context.object
obj.cloth_next.enabled = True; obj.cloth_next.role = 'FORCE'
force = obj.cloth_next.force
force.gravity_strength = 0; force.wind_local = True; force.wind_radius = 2
force.wind_turbulence = 2; force.wind_strength = 2; force.wind_show_vectors = True
obj.rotation_euler.y = math.pi / 2
force.keyframe_insert('wind_strength', frame=1)
force.wind_strength = 4; force.keyframe_insert('wind_strength', frame=60)
out = ROOT / '.tmp_verify'; out.mkdir(exist_ok=True)
report = out / 'wind-overlay-capture.json'
report.write_text('{"result":"RUNNING"}')


def capture():
    try:
        window = bpy.context.window
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        eye, target = Vector((6, -8, 6)), Vector((0, 0, 0))
        view = (Matrix.Translation(eye) @ (target-eye).to_track_quat('-Z', 'Y').to_matrix().to_4x4()).inverted()
        f, near, far = 1/math.tan(math.radians(30)), .01, 100
        projection = Matrix(((f/(800/600),0,0,0),(0,f,0,0),
                            (0,0,(far+near)/(near-far),2*far*near/(near-far)),(0,0,-1,0)))
        counts = []; pictures = []
        with bpy.context.temp_override(window=window, area=area, region=region):
            for frame in (1, 60):
                scene.frame_set(frame)
                offscreen = gpu.types.GPUOffScreen(800, 600)
                try:
                    with offscreen.bind():
                        framebuffer = gpu.state.active_framebuffer_get()
                        framebuffer.clear(color=(.025,.025,.035,1), depth=1.)
                        gpu.state.viewport_set(0,0,800,600)
                        gpu.state.scissor_test_set(False)
                        wind_fields.draw(SimpleNamespace(view_matrix=view, window_matrix=projection))
                        pixels = np.asarray(framebuffer.read_color(0,0,800,600,4,0,'UBYTE')).reshape(600,800,4)
                        cyan = (pixels[:,:,0]<90) & (pixels[:,:,1]>100) & (pixels[:,:,2]>150)
                        count = int(np.count_nonzero(cyan)); assert count > 50
                        counts.append(count); pictures.append(pixels.copy())
                        image = bpy.data.images.new('Wind Preview', width=800, height=600, alpha=True)
                        image.pixels.foreach_set((pixels.astype(np.float32)/255).reshape(-1))
                        image.filepath_raw = str(out/f'wind-preview-frame-{frame}.png')
                        image.file_format = 'PNG'; image.save(); bpy.data.images.remove(image)
                finally:
                    offscreen.free()
        assert not np.array_equal(*pictures)
        report.write_text(json.dumps({'result':'PASS','frames':[1,60],'visible_pixels':counts}))
    except Exception:
        report.write_text(json.dumps({'result':'FAIL','error':traceback.format_exc()}))
    finally:
        registration.unregister(); bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(capture, first_interval=2.)
