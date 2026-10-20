# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep FLIP display loading out of temporary preparation frame changes.

FLIP simulation inputs and bake files are never touched. Debug drawing and
the display of surface/particle/whitewater meshes are masked for the
synchronous frame_set call only; the next ordinary frame change reloads them.
Backing ID properties avoid deleting/restarting FLIP's modal draw operators.
"""
from contextlib import contextmanager

DEBUG_FLAGS = ('enable_fluid_particle_debug_output', 'export_force_field')
DISPLAY_ENUMS = ('viewport_display', 'render_display',
                 'fluid_particle_viewport_display', 'fluid_particle_render_display',
                 'whitewater_viewport_display', 'whitewater_render_display')


@contextmanager
def quiet_debug_previews(scene):
    saved=[]
    try:
        for obj in getattr(scene,'objects',()):
            flip=getattr(obj,'flip_fluid',None)
            if getattr(flip,'object_type','')!='TYPE_DOMAIN':
                continue
            debug=getattr(getattr(flip,'domain',None),'debug',None)
            if debug is None:
                continue
            owner=getattr(debug,'id_data',None)
            if getattr(owner,'library',None) and not getattr(owner,'override_library',None):
                continue
            for key in DEBUG_FLAGS:
                if not getattr(debug,key,False):
                    continue
                existed=key in debug
                previous=debug[key] if existed else None
                saved.append((debug,key,existed,previous))
                debug[key]=False
            # Preparation never reads FLIP's display meshes (Water Flow reads
            # the bake files itself), but FLIP reloads surface, particle and
            # whitewater meshes on every frame change: ~0.7 s per frame in a
            # production scene versus ~0.03 s with display set to None.
            render=getattr(getattr(flip,'domain',None),'render',None)
            for key in DISPLAY_ENUMS:
                prop=getattr(getattr(render,'bl_rna',None),'properties',{}).get(key) if render is not None else None
                if prop is None or getattr(render,key,None)=='DISPLAY_NONE':
                    continue
                values=[item.identifier for item in prop.enum_items]
                if 'DISPLAY_NONE' not in values:
                    continue
                existed=key in render
                previous=render[key] if existed else None
                saved.append((render,key,existed,previous))
                render[key]=values.index('DISPLAY_NONE')
        yield
    finally:
        for debug,key,existed,previous in reversed(saved):
            if existed:
                debug[key]=previous
            else:
                del debug[key]


def frame_set(scene, frame, **kwargs):
    with quiet_debug_previews(scene):
        result = scene.frame_set(frame, **kwargs)
    # Hydrodynamic Pressure needs the cloth's extent over the Bake. Measure it
    # during the capture passes that already visit every frame.
    from .water_flow import record_motion_bounds
    record_motion_bounds(scene, frame)
    return result
