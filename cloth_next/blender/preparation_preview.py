# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep diagnostic FLIP GPU previews out of temporary preparation frame changes.

Physical surface/particle meshes and simulation inputs are never disabled.
Only debug-drawing flags are masked for the synchronous frame_set call.
Backing ID properties avoid deleting/restarting FLIP's modal draw operators.
"""
from contextlib import contextmanager

DEBUG_FLAGS = ('enable_fluid_particle_debug_output', 'export_force_field')


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
