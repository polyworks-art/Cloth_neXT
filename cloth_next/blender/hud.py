# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached, draw-only DCC status card for the 3D Viewport."""
from __future__ import annotations
import bpy
from ..bake.controller import shared_controller
from ..bake.status import BakeState
from ..telemetry import shared_telemetry
from ..telemetry.hud_layout import build_hud_card

_handle=None; _draw_failed=False

def _preferences():
    try: return bpy.context.preferences.addons[__package__.partition(".blender")[0]].preferences
    except (KeyError,AttributeError): return None

def _draw():
    global _draw_failed
    if _draw_failed: return
    try:
        import blf, gpu
        snap=shared_controller.snapshot(); prefs=_preferences()
        if snap.state is BakeState.IDLE or (prefs and not prefs.show_bake_hud): return
        width=getattr(bpy.context.region,"width",800); height=getattr(bpy.context.region,"height",600)
        card=build_hud_card(snap,shared_telemetry.snapshot(),
            mode=getattr(prefs,"bake_hud_mode","EXPANDED"),
            anchor=getattr(prefs,"bake_hud_anchor","BOTTOM_LEFT"),
            scale=getattr(prefs,"bake_hud_scale",1.0), viewport_width=width,
            viewport_height=height, hardware=getattr(prefs,"show_hardware_metrics",True))
        shader=gpu.shader.from_builtin("UNIFORM_COLOR")
        from gpu_extras.batch import batch_for_shader
        def rect(x,y,w,h,color):
            batch=batch_for_shader(shader,"TRI_FAN",{"pos":((x,y),(x+w,y),(x+w,y+h),(x,y+h))})
            shader.bind(); shader.uniform_float("color",color); batch.draw(shader)
        rect(card.x,card.y,card.width,card.height,(.035,.04,.05,.88)); rect(card.x,card.y,4,card.height,(.92,.55,.12,1))
        if snap.progress_total:
            rect(card.x+4,card.y,card.width-4,3,(.15,.16,.18,1))
            rect(card.x+4,card.y,(card.width-4)*snap.progress_fraction,3,(.92,.55,.12,1))
        font=0; x=card.x+14; y=card.y+card.height-24
        blf.size(font,13); blf.color(font,.94,.94,.94,1); blf.position(font,x,y,0); blf.draw(font,card.title)
        for line in card.lines:
            y-=19; blf.size(font,11); blf.color(font,.78,.8,.83,1); blf.position(font,x,y,0); blf.draw(font,line)
    except Exception: _draw_failed=True

def register():
    global _handle,_draw_failed
    shared_telemetry.start()
    if _handle is not None or not hasattr(bpy.types,"SpaceView3D"): return
    _draw_failed=False; _handle=bpy.types.SpaceView3D.draw_handler_add(_draw,(),"WINDOW","POST_PIXEL")

def unregister():
    global _handle
    shared_telemetry.stop()
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle,"WINDOW"); _handle=None
