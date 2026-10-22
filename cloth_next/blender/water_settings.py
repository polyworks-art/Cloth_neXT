# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene sharing of Flow settings, retaining the existing object export API.

Influence and Velocity Scale deliberately never enter the shared snapshot.
The scene remembers its source object, so newly enabled Cloth inherits the
same setup, including an explicitly disabled Flow, after save and reload.
"""
import bpy

SHARED_PROPERTIES = (
    'water_hydrodynamic_pressure', 'water_pressure_strength', 'water_pressure_tangential',
    'water_wet_fabric', 'water_fiber_density', 'water_absorption', 'water_wet_time', 'water_dry_time', 'water_wet_cling', 'water_wet_damping',
    'water_flow_enabled', 'water_flow_domain', 'water_flow_coupling_strength',
    'water_flow_native_flow', 'water_flow_resolution', 'water_flow_custom_resolution',
    'water_flow_container', 'water_flow_show_vectors', 'water_flow_debug_mode',
    'water_flow_show_bounds', 'water_flow_vector_stride', 'water_flow_vector_scale',
)
_sharing = False
_memberships = {}


def _cloth(obj):
    s = getattr(obj, 'cloth_next', None)
    return bool(s and s.enabled and s.role == 'CLOTH')


def _scene_for(owner, context):
    scene = getattr(context, 'scene', None)
    if scene is not None and any(obj == owner for obj in getattr(scene, 'objects', ())):
        return scene
    return next(iter(getattr(owner, 'users_scene', ())), None)


def _source(scene, excluding=None):
    objects = tuple(scene.objects)
    source = getattr(scene, 'cloth_next_water_flow_source', None)
    if source is not None and source != excluding and any(obj == source for obj in objects):
        settings = getattr(source, 'cloth_next', None)
        if settings and settings.role == 'CLOTH':
            return source
    candidates = [obj for obj in objects if obj != excluding and _cloth(obj)
                  and obj.cloth_next.water_flow_enabled]
    if not candidates and getattr(scene, 'cloth_next_water_flow_shared', False):
        candidates = [obj for obj in objects if obj != excluding and _cloth(obj)]
    layer = next(iter(getattr(scene, 'view_layers', ())), None)
    active = getattr(getattr(layer, 'objects', None), 'active', None)
    return active if active in candidates else next(iter(sorted(candidates, key=lambda obj: obj.name)), None)


def share_from(settings, context):
    global _sharing
    if _sharing:
        return
    owner = getattr(settings, 'id_data', None)
    scene = _scene_for(owner, context)
    if (scene is None or getattr(settings, 'role', '') != 'CLOTH'
            or (not settings.enabled and getattr(scene, 'cloth_next_water_flow_source', None) != owner)):
        return
    snapshot = {key: getattr(settings, key) for key in SHARED_PROPERTIES}
    _sharing = True
    try:
        scene.cloth_next_water_flow_source = owner
        scene.cloth_next_water_flow_shared = True
        for obj in tuple(scene.objects):
            if obj == owner or not _cloth(obj):
                continue
            for key, value in snapshot.items():
                if getattr(obj.cloth_next, key) != value:
                    setattr(obj.cloth_next, key, value)
    finally:
        _sharing = False


def inherit(settings, context):
    if _sharing:
        return
    owner = getattr(settings, 'id_data', None)
    scene = _scene_for(owner, context)
    if scene is not None and _cloth(owner):
        source = _source(scene, excluding=owner)
        if source is not None:
            share_from(source.cloth_next, context)


def _pointer(value):
    return value.as_pointer() if hasattr(value, 'as_pointer') else id(value)


def _sync_scene(scene):
    source = _source(scene)
    if source is not None:
        # Pass the owning scene rather than the currently displayed scene.
        from types import SimpleNamespace
        share_from(source.cloth_next, SimpleNamespace(scene=scene))
    _memberships[_pointer(scene)] = tuple(_pointer(obj) for obj in scene.objects if _cloth(obj))


def _load_post(*_args):
    _memberships.clear()
    for scene in getattr(bpy.data, 'scenes', ()):
        _sync_scene(scene)


def _initialize():
    # Blender restricts data access while enabling an extension. Migrate
    # existing scenes once registration has returned to the event loop.
    if not hasattr(bpy.data, 'scenes'):
        return .05
    _load_post()
    return None


def _depsgraph_post(scene, _depsgraph):
    if _sharing:
        return
    members = tuple(_pointer(obj) for obj in scene.objects if _cloth(obj))
    if _memberships.get(_pointer(scene)) != members:
        _sync_scene(scene)


_load_post = getattr(bpy.app.handlers, 'persistent', lambda f: f)(_load_post)
_depsgraph_post = getattr(bpy.app.handlers, 'persistent', lambda f: f)(_depsgraph_post)
_load_post._clothnext_shared_water = True
_depsgraph_post._clothnext_shared_water = True


def register():
    for name, callback in (('load_post', _load_post), ('depsgraph_update_post', _depsgraph_post)):
        handlers = getattr(bpy.app.handlers, name)
        for old in tuple(handlers):
            if getattr(old, '_clothnext_shared_water', False):
                handlers.remove(old)
        handlers.append(callback)
    if hasattr(bpy.data, 'scenes'):
        _load_post()
    elif not bpy.app.timers.is_registered(_initialize):
        bpy.app.timers.register(_initialize, first_interval=.05)


def unregister():
    if bpy.app.timers.is_registered(_initialize):
        bpy.app.timers.unregister(_initialize)
    for name in ('load_post', 'depsgraph_update_post'):
        handlers = getattr(bpy.app.handlers, name)
        for callback in tuple(handlers):
            if getattr(callback, '_clothnext_shared_water', False):
                handlers.remove(callback)
    _memberships.clear()
